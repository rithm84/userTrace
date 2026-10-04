"""Browser activation relayed through the preview front door.

The embedded SDK's inert bootstrap posts to the reserved same-origin
``/__utrace/activate`` and ``/__utrace/telemetry-credential`` paths. The front
door authenticates with its own service token, forwards the session cookie
context in ``x-utrace-session-credential``, and returns the control plane's
signed answer unchanged. It never mints, inspects, or caches an attestation or a
telemetry credential.

The propagation probe echo has the same shape: the front door serves
``/__utrace/propagation-probe`` and relays here, so the control plane sees what
trace context actually crossed the authenticated preview boundary and can prove
there is exactly one injector.

Contract: docs/arch-components/browser-sdk.md#activation-and-session-attestation
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import CurrentDatabase, CurrentSettings, FrontDoorService
from app.auth.lane import Lane, check_lane_revocation, verify_lane_credential
from app.ingestion.activation import (
    ActivationRequest,
    create_browser_activation,
    exchange_attestation,
)
from app.ingestion.errors import IntegrityViolationError
from app.ingestion.limits import BrowserLimits
from app.ingestion.settings import get_ingestion_settings
from app.ingestion.signing import SignedEnvelope
from app.ingestion.wiring import require_signer
from app.interview.settings import get_interview_settings
from app.logging import get_logger

__all__ = ["router"]

_log = get_logger(__name__)

router = APIRouter(prefix="/v1/preview", tags=["front-door"])

_PREVIEW_HOST = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
_TRACEPARENT = r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$"

_INGEST_PATH = "/v1/ingest/browser"


class ApiModel(BaseModel):
    """Base for this router's models: closed to extra fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class BrowserActivationRequest(ApiModel):
    """The embedded browser SDK's own request body, forwarded unchanged.

    The door adds nothing to it and drops nothing from it: the attestation pins
    the SDK installation, the document origin, the protocol and the schema, so
    every field has to arrive exactly as the SDK sent it.

    ``environment_name`` and ``client_release_id`` are part of that body and are
    accepted here so a conforming SDK is not refused. Binding them to the pinned
    configuration release is the telemetry-ingestion stream's to add; nothing in
    this router may quietly treat them as checked.

    Contract: packages/utrace-contracts/schemas/preview/activation-request.v1.schema.json
    """

    schema_version: str = Field(pattern=r"^[0-9]{1,3}$")
    sdk_installation_id: str = Field(min_length=1, max_length=128)
    environment_name: str = Field(min_length=1, max_length=64)
    client_release_id: str = Field(min_length=1, max_length=128)
    protocol_version: str = Field(pattern=r"^[0-9]{1,3}$")
    document_nonce: str = Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")
    document_origin: str = Field(pattern=r"^https?://[^/?#]+$", max_length=2048)
    observation_schema_version: str | None = Field(
        default=None, pattern=r"^[0-9]{1,3}$"
    )
    """The observation schema major, when the SDK pins it apart from its own."""

    def observation_schema(self) -> str:
        """The observation schema major this document speaks."""
        return (
            self.schema_version
            if self.observation_schema_version is None
            else self.observation_schema_version
        )


class ActivationRelayRequest(ApiModel):
    """What the front door forwards from one document's activation request."""

    schema_version: Literal["utrace_preview_activation_relay_request_v1"]
    public_preview_host: str = Field(pattern=_PREVIEW_HOST, max_length=253)
    activation_request: BrowserActivationRequest


class ActivationResponse(ApiModel):
    """The three signed envelopes the bootstrap verifies with its packaged key."""

    attestation: SignedEnvelope
    annotation_manifest: SignedEnvelope
    page_proof: SignedEnvelope


class TelemetryCredentialRequest(ApiModel):
    """The embedded browser SDK's own credential request body."""

    attestation: SignedEnvelope
    document_nonce: str = Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")


class TelemetryCredentialRelayRequest(ApiModel):
    """The one-time exchange of a verified attestation for a credential."""

    schema_version: Literal["utrace_preview_telemetry_credential_relay_request_v1"]
    public_preview_host: str = Field(pattern=_PREVIEW_HOST, max_length=253)
    credential_request: TelemetryCredentialRequest


class TelemetryCredentialBody(ApiModel):
    """The short-lived, write-only credential and where it may be presented."""

    token: str = Field(pattern=r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
    expires_at: str
    ingest_url: str = Field(pattern=r"^wss?://")


class TelemetryCredentialResponse(ApiModel):
    """The credential plus the operational limits the runtime must respect."""

    credential: TelemetryCredentialBody
    limits: BrowserLimits


class PropagationProbeRequest(ApiModel):
    """What the front door observed on one propagation-probe request."""

    schema_version: Literal["utrace_preview_propagation_probe_request_v1"]
    public_preview_host: str = Field(pattern=_PREVIEW_HOST, max_length=253)
    traceparent: str = Field(pattern=_TRACEPARENT)
    tracestate: str = Field(max_length=512)
    traceparent_count: int = Field(ge=0, le=16)


class PropagationProbeResponse(ApiModel):
    """The echo the SDK compares against what it injected.

    ``traceparent_count`` above one means a second injector is running, which
    fails the probe instead of being merged.
    """

    schema_version: Literal["utrace_preview_propagation_probe_v1"] = (
        "utrace_preview_propagation_probe_v1"
    )
    traceparent: str
    tracestate: str
    traceparent_count: int
    single_injector: bool


async def _session_scope(
    database: CurrentDatabase,
    settings: CurrentSettings,
    session_credential: str,
    public_preview_host: str,
) -> UUID:
    """Verify the forwarded session context and return its Interview Session."""
    claims = verify_lane_credential(
        signing_key=settings.lane_signing_key,
        token=session_credential,
        lane=Lane.FRONT_DOOR,
    )
    await check_lane_revocation(database, claims)
    if claims.interview_session_id is None:
        raise IntegrityViolationError(
            identity_kind="session_credential",
            identity_value="front_door",
            reason="carries no Interview Session binding",
        )
    if claims.binding != public_preview_host:
        raise IntegrityViolationError(
            identity_kind="public_preview_host",
            identity_value=public_preview_host,
            reason="is not the origin this session credential is bound to",
        )
    return claims.interview_session_id


@router.post("/activations", response_model=ActivationResponse)
async def create_activation(
    request: Request,
    service: FrontDoorService,
    database: CurrentDatabase,
    settings: CurrentSettings,
    body: ActivationRelayRequest,
    x_utrace_session_credential: Annotated[str, Header()],
) -> ActivationResponse:
    """Return one document's signed attestation, manifest, and page proof."""
    session_id = await _session_scope(
        database, settings, x_utrace_session_credential, body.public_preview_host
    )
    issued = await create_browser_activation(
        database,
        signer=require_signer(request.app),
        request=ActivationRequest(
            public_preview_host=body.public_preview_host,
            document_nonce=body.activation_request.document_nonce,
            document_origin=body.activation_request.document_origin,
            sdk_installation_id=body.activation_request.sdk_installation_id,
            protocol_version=body.activation_request.protocol_version,
            schema_version=body.activation_request.observation_schema(),
        ),
        extension_protocol_version=get_interview_settings().extension_protocol_version,
    )
    _log.info(
        "preview_activation_relayed",
        interview_session_id=str(session_id),
        activation_id=str(issued.activation_id),
        public_preview_host=body.public_preview_host,
    )
    return ActivationResponse(
        attestation=issued.attestation,
        annotation_manifest=issued.annotation_manifest,
        page_proof=issued.page_proof,
    )


@router.post("/telemetry-credentials", response_model=TelemetryCredentialResponse)
async def create_telemetry_credential(
    request: Request,
    service: FrontDoorService,
    database: CurrentDatabase,
    settings: CurrentSettings,
    body: TelemetryCredentialRelayRequest,
    x_utrace_session_credential: Annotated[str, Header()],
) -> TelemetryCredentialResponse:
    """Exchange one verified attestation for its single telemetry credential."""
    await _session_scope(
        database, settings, x_utrace_session_credential, body.public_preview_host
    )
    credential, limits = await exchange_attestation(
        database,
        signer=require_signer(request.app),
        attestation=body.credential_request.attestation,
        public_preview_host=body.public_preview_host,
    )
    ingestion = get_ingestion_settings()
    return TelemetryCredentialResponse(
        credential=TelemetryCredentialBody(
            token=credential.token,
            expires_at=credential.expires_at.isoformat().replace("+00:00", "Z"),
            ingest_url=f"{ingestion.ingest_websocket_origin.rstrip('/')}{_INGEST_PATH}",
        ),
        limits=limits,
    )


@router.post("/propagation-probes", response_model=PropagationProbeResponse)
async def echo_propagation_probe(
    service: FrontDoorService,
    database: CurrentDatabase,
    settings: CurrentSettings,
    body: PropagationProbeRequest,
    x_utrace_session_credential: Annotated[str, Header()],
) -> PropagationProbeResponse:
    """Echo the trace context the front door saw on the probe request.

    The front door serves ``/__utrace/propagation-probe`` on the preview origin
    and relays here. The echo proves what context actually crossed the
    authenticated preview boundary and that exactly one injector produced it.
    """
    session_id = await _session_scope(
        database, settings, x_utrace_session_credential, body.public_preview_host
    )
    _log.info(
        "propagation_probe_echoed",
        interview_session_id=str(session_id),
        traceparent_count=body.traceparent_count,
    )
    return PropagationProbeResponse(
        traceparent=body.traceparent,
        tracestate=body.tracestate,
        traceparent_count=body.traceparent_count,
        single_injector=body.traceparent_count == 1,
    )
