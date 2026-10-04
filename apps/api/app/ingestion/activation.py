"""Browser observation activation.

The inert bootstrap requests activation through the preview front door's reserved
same-origin endpoint. The control plane returns a signed, short-lived document
attestation bound to the client, client user, customer account, Interview
Session, preview environment, runtime allocation, optional candidate Preview
Version, origin, document nonce, SDK installation, protocol, annotation-manifest
version, redaction-policy version, and expiry, alongside the signed annotation
manifest it pins and the signed page proof the extension re-verifies.

The bootstrap exchanges that attestation exactly once for a short-lived,
write-only telemetry credential. Reload, full navigation, and an expired or
rejected credential each need a fresh attestation, so a prior document can never
activate or export for its replacement. A reconnect or resume reuses the
unexpired credential and reopens the same stream.

Contract: docs/arch-components/browser-sdk.md#activation-and-session-attestation
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from json import loads
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from app.db.pool import Database
from app.db.rows import as_optional_uuid, as_str, as_uuid
from app.envelope import JsonValue
from app.errors import AggregateNotFoundError
from app.ids import uuid7
from app.ingestion.credentials import (
    BROWSER_CREDENTIAL_LIFETIME_SECONDS,
    IssuedCredential,
    issue_credential,
)
from app.ingestion.errors import (
    IntegrityViolationError,
    SourceValidationError,
    TelemetryAuthenticationError,
)
from app.ingestion.frames import ObservationScope
from app.ingestion.limits import BrowserLimits, default_browser_limits
from app.ingestion.preview_environments import resolve_preview_environment_id
from app.ingestion.scheduling import schedule_readiness
from app.ingestion.signing import SignedEnvelope, Signer, payload_digest
from app.logging import get_logger

__all__ = [
    "ATTESTATION_LIFETIME_SECONDS",
    "ActivationRequest",
    "BrowserActivation",
    "IssuedAttestation",
    "assert_scope_matches",
    "create_browser_activation",
    "exchange_attestation",
    "load_browser_activation",
]

_log = get_logger(__name__)

#: Activation is a bounded pre-application gate; the attestation lives just long
#: enough for the bootstrap to exchange it and open its stream.
ATTESTATION_LIFETIME_SECONDS = 300

_PROTOCOL_VERSION = "1"
_OBSERVATION_SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class ActivationRequest:
    """What the front door forwards from one document's activation request."""

    public_preview_host: str
    document_nonce: str
    document_origin: str
    sdk_installation_id: str
    protocol_version: str
    schema_version: str


@dataclass(frozen=True, slots=True)
class IssuedAttestation:
    """The three signed envelopes one activation returns."""

    activation_id: UUID
    attestation: SignedEnvelope
    annotation_manifest: SignedEnvelope
    page_proof: SignedEnvelope
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class BrowserActivation:
    """The durable activation record a browser credential resolves against."""

    activation_id: UUID
    client_id: UUID
    client_user_id: UUID
    customer_account_id: UUID
    interview_session_id: UUID
    preview_environment_id: UUID
    preview_runtime_allocation_id: UUID
    preview_version_id: UUID | None
    document_attachment_id: UUID
    browser_stream_id: UUID | None
    policy_version: str
    redaction_version: str
    source_schema_version: str
    limits: BrowserLimits


async def _load_allocation(
    conn: AsyncConnection[DictRow], *, public_preview_host: str
) -> DictRow:
    """Resolve one preview origin to its allocation, session, and case identities."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select allocations.id as preview_runtime_allocation_id,
                   allocations.client_id,
                   allocations.state as allocation_state,
                   allocations.preview_version_id,
                   sessions.id as interview_session_id,
                   sessions.state as session_state,
                   sessions.client_user_id,
                   signals.id as product_signal_id,
                   signals.originating_customer_account_id,
                   signals.pinned_configuration_release_id
            from public.preview_runtime_allocations as allocations
            join public.interview_sessions as sessions
              on sessions.id = allocations.interview_session_id
            join public.product_signals as signals
              on signals.id = allocations.product_signal_id
            where allocations.origin_host = %s
            """,
            (public_preview_host,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="preview_runtime_allocations", row_id=UUID(int=0)
        )
    if as_str(row, "session_state") == "closed":
        raise TelemetryAuthenticationError(
            "the Interview Session bound to that preview origin is closed"
        )
    return row


async def _load_annotation_manifest(
    conn: AsyncConnection[DictRow], *, configuration_release_id: UUID
) -> dict[str, JsonValue]:
    """Read the signed annotation manifest onboarding pinned to this release.

    Onboarding owns the manifest's content. Ingestion refuses to activate a
    document whose release carries none rather than inventing an empty one: an
    empty manifest would silently authorize nothing and look healthy.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "select policy from public.configuration_releases where id = %s",
            (configuration_release_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="configuration_releases", row_id=configuration_release_id
        )
    policy = row["policy"]
    if not isinstance(policy, dict):
        raise SourceValidationError(
            field="configuration_releases.policy", reason="is not an object"
        )
    manifest = policy.get("annotation_manifest")
    if not isinstance(manifest, dict):
        raise SourceValidationError(
            field="configuration_releases.policy.annotation_manifest",
            reason=(
                "the pinned configuration release carries no annotation manifest; "
                "onboarding must publish one before a document can activate"
            ),
        )
    return dict(manifest)


def _manifest_versions(manifest: dict[str, JsonValue]) -> tuple[str, str, str]:
    """Return the manifest version and the redaction and policy versions it pins."""
    version = manifest.get("manifest_version")
    redaction = manifest.get("redaction")
    if not isinstance(version, str) or not isinstance(redaction, dict):
        raise SourceValidationError(
            field="annotation_manifest",
            reason="must carry a manifest_version and a redaction block",
        )
    redaction_version = redaction.get("redaction_version")
    policy_version = redaction.get("policy_version")
    if not isinstance(redaction_version, str) or not isinstance(policy_version, str):
        raise SourceValidationError(
            field="annotation_manifest.redaction",
            reason="must carry a redaction_version and a policy_version",
        )
    return version, redaction_version, policy_version


async def create_browser_activation(
    database: Database,
    *,
    signer: Signer,
    request: ActivationRequest,
    extension_protocol_version: str,
) -> IssuedAttestation:
    """Create one browser activation and return its three signed envelopes.

    The attestation pins the annotation manifest's version and the SHA-256 digest
    of the exact payload bytes the manifest envelope carries, so the bootstrap's
    digest check compares the same bytes uTrace signed. The page proof carries the
    `page_proof_claims` set of the extension protocol, including the extension
    protocol version this control plane speaks, so the extension can match the
    document and the control plane can verify the binding.
    """
    if request.protocol_version != _PROTOCOL_VERSION:
        raise SourceValidationError(
            field="protocol_version",
            reason=f"this control plane speaks protocol major {_PROTOCOL_VERSION}",
        )
    if request.schema_version != _OBSERVATION_SCHEMA_VERSION:
        raise SourceValidationError(
            field="schema_version",
            reason=f"this control plane speaks schema major {_OBSERVATION_SCHEMA_VERSION}",
        )

    issued_at = datetime.now(tz=UTC)
    expires_at = issued_at + timedelta(seconds=ATTESTATION_LIFETIME_SECONDS)
    limits = default_browser_limits()

    async with database.transaction() as conn:
        allocation = await _load_allocation(
            conn, public_preview_host=request.public_preview_host
        )
        client_id = as_uuid(allocation, "client_id")
        manifest = await _load_annotation_manifest(
            conn,
            configuration_release_id=as_uuid(
                allocation, "pinned_configuration_release_id"
            ),
        )
        manifest_version, redaction_version, policy_version = _manifest_versions(manifest)
        manifest_envelope = signer.sign(manifest)
        preview_environment_id = await resolve_preview_environment_id(
            conn,
            preview_runtime_allocation_id=as_uuid(
                allocation, "preview_runtime_allocation_id"
            ),
        )

        activation_id = uuid7()
        document_attachment_id = uuid7()
        attestation_payload: dict[str, JsonValue] = {
            "attestation_id": str(activation_id),
            "client_id": str(client_id),
            "client_user_id": str(as_uuid(allocation, "client_user_id")),
            "customer_account_id": str(
                as_uuid(allocation, "originating_customer_account_id")
            ),
            "session_id": str(as_uuid(allocation, "interview_session_id")),
            "preview_environment_id": str(preview_environment_id),
            "preview_runtime_allocation_id": str(
                as_uuid(allocation, "preview_runtime_allocation_id")
            ),
            "preview_version_id": _optional_str(
                as_optional_uuid(allocation, "preview_version_id")
            ),
            "document_attachment_id": str(document_attachment_id),
            "origin": request.document_origin,
            "document_nonce": request.document_nonce,
            "sdk_installation_id": request.sdk_installation_id,
            "protocol_version": _PROTOCOL_VERSION,
            "schema_version": _OBSERVATION_SCHEMA_VERSION,
            "annotation_manifest_version": manifest_version,
            "annotation_manifest_digest": payload_digest(manifest_envelope),
            "redaction_version": redaction_version,
            "policy_version": policy_version,
            "issued_at": _rfc3339(issued_at),
            "expires_at": _rfc3339(expires_at),
        }
        page_proof_payload: dict[str, JsonValue] = {
            "client_id": str(client_id),
            "session_id": str(as_uuid(allocation, "interview_session_id")),
            "preview_environment_id": str(preview_environment_id),
            "preview_runtime_allocation_id": str(
                as_uuid(allocation, "preview_runtime_allocation_id")
            ),
            "document_attachment_id": str(document_attachment_id),
            "document_nonce": request.document_nonce,
            "origin": request.document_origin,
            "sdk_protocol_version": extension_protocol_version,
            "issued_at": _rfc3339(issued_at),
            "expires_at": _rfc3339(expires_at),
        }

        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.telemetry_activations (
                    id, client_id, kind, client_user_id, customer_account_id,
                    interview_session_id, preview_environment_id,
                    preview_runtime_allocation_id, preview_version_id,
                    protocol_version, source_schema_version, policy_version,
                    redaction_version, annotation_manifest_version,
                    annotation_manifest_digest, limits, document_attachment_id,
                    document_nonce, document_origin, sdk_installation_id,
                    attestation_expires_at
                )
                values (
                    %s, %s, 'browser', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    activation_id,
                    client_id,
                    as_uuid(allocation, "client_user_id"),
                    as_uuid(allocation, "originating_customer_account_id"),
                    as_uuid(allocation, "interview_session_id"),
                    preview_environment_id,
                    as_uuid(allocation, "preview_runtime_allocation_id"),
                    as_optional_uuid(allocation, "preview_version_id"),
                    _PROTOCOL_VERSION,
                    _OBSERVATION_SCHEMA_VERSION,
                    policy_version,
                    redaction_version,
                    manifest_version,
                    payload_digest(manifest_envelope),
                    Jsonb(limits.model_dump()),
                    document_attachment_id,
                    request.document_nonce,
                    request.document_origin,
                    request.sdk_installation_id,
                    expires_at,
                ),
            )
        await schedule_readiness(
            conn,
            client_id=client_id,
            activation_id=activation_id,
            interview_session_id=as_uuid(allocation, "interview_session_id"),
            trigger="browser_activation_created",
        )

    _log.info(
        "browser_activation_created",
        client_id=str(client_id),
        activation_id=str(activation_id),
        interview_session_id=str(as_uuid(allocation, "interview_session_id")),
        document_attachment_id=str(document_attachment_id),
        annotation_manifest_version=manifest_version,
    )
    return IssuedAttestation(
        activation_id=activation_id,
        attestation=signer.sign(attestation_payload),
        annotation_manifest=manifest_envelope,
        page_proof=signer.sign(page_proof_payload),
        expires_at=expires_at,
    )


async def exchange_attestation(
    database: Database,
    *,
    signer: Signer,
    attestation: SignedEnvelope,
    public_preview_host: str,
) -> tuple[IssuedCredential, BrowserLimits]:
    """Exchange one verified attestation for its single telemetry credential.

    The exchange is single-use. A second attempt with the same attestation is an
    integrity violation, not a refresh: the bootstrap must run the whole gate
    again and receive a new attestation.
    """
    raw = signer.verify(attestation)
    claims = _decode_attestation_claims(raw)
    activation_id = claims["attestation_id"]

    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select activations.id, activations.client_id, activations.state,
                       activations.attestation_exchanged_at,
                       activations.attestation_expires_at, activations.limits,
                       allocations.origin_host
                from public.telemetry_activations as activations
                join public.preview_runtime_allocations as allocations
                  on allocations.id = activations.preview_runtime_allocation_id
                where activations.id = %s and activations.kind = 'browser'
                for update of activations
                """,
                (activation_id,),
            )
            row = await cur.fetchone()
        if row is None:
            raise TelemetryAuthenticationError(
                "the attestation names an activation this control plane did not issue"
            )
        if as_str(row, "state") != "active":
            raise TelemetryAuthenticationError(
                f"the observation activation is {as_str(row, 'state')}"
            )
        if as_str(row, "origin_host") != public_preview_host:
            raise IntegrityViolationError(
                identity_kind="preview_origin",
                identity_value=public_preview_host,
                reason="the attestation was issued for another preview origin",
            )
        if row["attestation_exchanged_at"] is not None:
            raise IntegrityViolationError(
                identity_kind="attestation",
                identity_value=str(activation_id),
                reason="has already been exchanged for a telemetry credential",
            )
        expires_at = row["attestation_expires_at"]
        if not isinstance(expires_at, datetime) or expires_at <= datetime.now(tz=UTC):
            raise TelemetryAuthenticationError("the attestation has expired")

        client_id = as_uuid(row, "client_id")
        credential = await issue_credential(
            conn,
            client_id=client_id,
            activation_id=activation_id,
            kind="browser",
            lifetime_seconds=BROWSER_CREDENTIAL_LIFETIME_SECONDS,
        )
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update public.telemetry_activations
                set attestation_exchanged_at = now(), revision = revision + 1,
                    updated_at = now()
                where id = %s
                """,
                (activation_id,),
            )
        limits_column = row["limits"]
    if not isinstance(limits_column, dict):
        raise SourceValidationError(
            field="telemetry_activations.limits", reason="is not an object"
        )
    return credential, BrowserLimits.model_validate(limits_column)


def _decode_attestation_claims(raw: bytes) -> dict[str, UUID]:
    """Read the identities this control plane needs from a verified attestation."""
    payload = loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise SourceValidationError(field="attestation", reason="is not an object")
    value = payload.get("attestation_id")
    if not isinstance(value, str):
        raise SourceValidationError(
            field="attestation.attestation_id", reason="is missing"
        )
    return {"attestation_id": UUID(value)}


def _optional_str(value: UUID | None) -> JsonValue:
    """Render an optional identifier for a signed payload."""
    return None if value is None else str(value)


def _rfc3339(moment: datetime) -> str:
    """Render one UTC instant as RFC 3339 with a literal ``Z``."""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


async def load_browser_activation(
    database: Database, *, activation_id: UUID
) -> BrowserActivation:
    """Load the authenticated scope one browser credential resolves to.

    Every identity a payload claims is compared against these columns. A payload
    identity never overrides the authenticated activation.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, client_id, client_user_id, customer_account_id,
                   interview_session_id, preview_environment_id,
                   preview_runtime_allocation_id, preview_version_id,
                   document_attachment_id, policy_version, redaction_version,
                   source_schema_version, limits
            from public.telemetry_activations
            where id = %s and kind = 'browser'
            """,
            (activation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="telemetry_activations", row_id=activation_id)
    limits_column = row["limits"]
    if not isinstance(limits_column, dict):
        raise SourceValidationError(
            field="telemetry_activations.limits", reason="is not an object"
        )
    return BrowserActivation(
        activation_id=as_uuid(row, "id"),
        client_id=as_uuid(row, "client_id"),
        client_user_id=as_uuid(row, "client_user_id"),
        customer_account_id=as_uuid(row, "customer_account_id"),
        interview_session_id=as_uuid(row, "interview_session_id"),
        preview_environment_id=as_uuid(row, "preview_environment_id"),
        preview_runtime_allocation_id=as_uuid(row, "preview_runtime_allocation_id"),
        preview_version_id=as_optional_uuid(row, "preview_version_id"),
        document_attachment_id=as_uuid(row, "document_attachment_id"),
        browser_stream_id=None,
        policy_version=as_str(row, "policy_version"),
        redaction_version=as_str(row, "redaction_version"),
        source_schema_version=as_str(row, "source_schema_version"),
        limits=BrowserLimits.model_validate(limits_column),
    )


def assert_scope_matches(
    activation: BrowserActivation, scope: ObservationScope
) -> None:
    """Refuse a stream whose claimed scope disagrees with its activation.

    Client, customer, user, session, preview environment, runtime allocation,
    candidate Preview Version, policy, and schema claims must all match the
    activation record. A mismatch is an integrity violation, not a hint.
    """
    claimed: tuple[tuple[str, object, object], ...] = (
        ("client_id", scope.client_id, activation.client_id),
        ("client_user_id", scope.client_user_id, activation.client_user_id),
        ("customer_account_id", scope.customer_account_id, activation.customer_account_id),
        ("session_id", scope.session_id, activation.interview_session_id),
        (
            "preview_environment_id",
            scope.preview_environment_id,
            activation.preview_environment_id,
        ),
        (
            "preview_runtime_allocation_id",
            scope.preview_runtime_allocation_id,
            activation.preview_runtime_allocation_id,
        ),
        ("preview_version_id", scope.preview_version_id, activation.preview_version_id),
        ("policy_version", scope.policy_version, activation.policy_version),
        ("redaction_version", scope.redaction_version, activation.redaction_version),
        ("schema_version", scope.schema_version, activation.source_schema_version),
    )
    for field, presented, authenticated in claimed:
        if presented != authenticated:
            raise IntegrityViolationError(
                identity_kind=field,
                identity_value=str(presented),
                reason="does not match the authenticated observation activation",
            )
