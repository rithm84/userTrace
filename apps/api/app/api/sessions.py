"""Interview Session routes.

The session tab is the mandatory live-session anchor. These routes are what it
calls: the participant compatibility preflight that admits the session, the
backend-authorized SDP exchange, the participant's controls, the committed
projection it renders, and the single-use binding offer it hands to the
extension.

Every route proves the same originating user through the session's own
participant dependency. Browser input selects no provider endpoint, deployment,
or privileged session configuration, and session descriptions never appear in a
response beyond the one answer that belongs to the offering connection.
"""

import asyncio
from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import CurrentDatabase, ParticipantOfPath
from app.auth.principals import ParticipantPrincipal
from app.db.pool import Database
from app.db.rows import as_optional_str, as_uuid
from app.envelope import JsonValue
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.extension.bindings import mint_binding_offer, served_allocation
from app.ingestion.control_hub import require_control_hub
from app.interview.controls import apply_participant_control
from app.interview.preflight import ParticipantEnvironment, run_participant_preflight
from app.interview.projection import load_projection
from app.interview.settings import get_interview_settings
from app.interview.voice.connections import open_voice_connection
from app.lifecycle.session_controls import ControlAction
from app.ports import PortRegistry

__all__ = ["provider_registry", "router"]

router = APIRouter(prefix="/v1/sessions", tags=["participant"])

RETURN_WINDOW_SECONDS = 72 * 60 * 60

_READY_ALLOCATION_STATES = frozenset({"ready", "bound"})
_OFFER_READY_WAIT_SECONDS: Final[float] = 30.0
_OFFER_READY_POLL_SECONDS: Final[float] = 0.25


class ApiModel(BaseModel):
    """Base for every model on this router: closed to extra fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class BrowserReport(ApiModel):
    """What the session tab observed about the browser."""

    name: str = Field(min_length=1)
    major_version: int = Field(ge=0)
    supported: bool


class ExtensionReport(ApiModel):
    """What the extension answered when the session tab probed it."""

    installed: bool
    account_linked: bool
    installation_proof: str | None = None
    protocol_version: str | None = None


class PermissionReport(ApiModel):
    """The two permissions the interview cannot start without."""

    host: bool
    microphone: bool


class PreflightRequest(ApiModel):
    """The participant compatibility preflight the session tab reports."""

    browser: BrowserReport
    extension: ExtensionReport
    permissions: PermissionReport


class PreflightCheckResult(ApiModel):
    """One named check and the guidance it carries when it fails."""

    id: str
    status: str
    detail: str


class PreflightResult(ApiModel):
    """Whether participation may begin, and what still needs doing if not."""

    passed: bool
    revision: int
    checks: list[PreflightCheckResult]


class VoiceConnectionRequestBody(ApiModel):
    """The browser's SDP offer for one voice connection."""

    sdp_offer: str = Field(min_length=1)


class VoiceConnectionResult(ApiModel):
    """The backend-authorized answer for one browser voice connection."""

    sdp_answer: str
    connection_id: UUID
    voice_run_id: UUID


class ControlRequest(ApiModel):
    """One participant control action."""

    action: ControlAction


class ControlsState(ApiModel):
    """The durable control state the surfaces reflect."""

    microphone: str
    available: list[str]


class SessionControlAck(ApiModel):
    """The projection revision the subscription will also see."""

    revision: int
    state: str
    controls: ControlsState


class SessionProjectionDocument(ApiModel):
    """The committed projection row: the revision and the document itself.

    This is the same pair ``public.session_projections`` carries and Supabase
    Realtime distributes, so the tab's first render and every later revision are
    one record in one vocabulary. ``revision`` is monotonic, and the document
    repeats it, which is what lets a surface drop an out-of-order delivery.
    """

    revision: int
    projection: dict[str, JsonValue]


class BindingOfferResult(ApiModel):
    """The single-use offer the session tab delivers to the official extension.

    ``offer_token`` is opaque to the tab. The control plane validates it together with
    the installation's proof of possession and the embedded SDK's page proof.
    """

    schema_version: int
    offer_token: str
    expires_at: str
    protocol_version: str
    session_id: UUID
    client_id: UUID
    extension_installation_id: UUID
    preview_runtime_allocation_id: UUID


def provider_registry(request: Request) -> PortRegistry:
    """Return the kernel's provider registry."""
    registry = request.app.state.ports
    if not isinstance(registry, PortRegistry):
        raise TypeError("the application was assembled without a port registry")
    return registry


@router.post("/{session_id}/preflight", response_model=PreflightResult)
async def preflight(
    participant: ParticipantOfPath,
    database: CurrentDatabase,
    session_id: Annotated[UUID, Path()],
    body: PreflightRequest,
) -> PreflightResult:
    """Verify the participant's environment and admit the session on a full pass."""
    interview = get_interview_settings()
    outcome = await run_participant_preflight(
        database,
        participant=participant,
        interview_session_id=session_id,
        environment=ParticipantEnvironment(
            browser_name=body.browser.name,
            browser_major_version=body.browser.major_version,
            extension_installed=body.extension.installed,
            extension_installation_proof=body.extension.installation_proof,
            reported_protocol_version=(
                body.extension.protocol_version or interview.extension_protocol_version
            ),
            host_permission_granted=body.permissions.host,
            microphone_permission_granted=body.permissions.microphone,
        ),
        supported_protocol_versions=_supported_protocol_versions(),
        possession_audience=interview.extension_api_audience,
        possession_max_age_seconds=interview.possession_proof_lifetime_seconds,
    )
    checks = [
        PreflightCheckResult(
            id=check.check_id,
            status="passed" if check.passed else "failed",
            detail=check.detail,
        )
        for check in outcome.checks
    ]
    if not outcome.passed or outcome.projection is None:
        return PreflightResult(passed=False, revision=0, checks=checks)
    return PreflightResult(
        passed=True, revision=outcome.projection.revision, checks=checks
    )


@router.post("/{session_id}/voice/connections", response_model=VoiceConnectionResult)
async def open_connection(
    request: Request,
    participant: ParticipantOfPath,
    database: CurrentDatabase,
    session_id: Annotated[UUID, Path()],
    body: VoiceConnectionRequestBody,
) -> VoiceConnectionResult:
    """Authorize one voice connection and return the provider's SDP answer."""
    voice = provider_registry(request).require_voice()
    opened = await open_voice_connection(
        database,
        voice,
        participant=participant,
        interview_session_id=session_id,
        sdp_offer=body.sdp_offer,
    )
    return VoiceConnectionResult(
        sdp_answer=opened.sdp_answer,
        connection_id=opened.connection_id,
        voice_run_id=opened.voice_run_id,
    )


@router.post("/{session_id}/controls", response_model=SessionControlAck)
async def controls(
    request: Request,
    participant: ParticipantOfPath,
    database: CurrentDatabase,
    session_id: Annotated[UUID, Path()],
    body: ControlRequest,
) -> SessionControlAck:
    """Apply one participant control and return the authoritative revision."""
    result = await apply_participant_control(
        database,
        participant=participant,
        interview_session_id=session_id,
        action=body.action,
        return_window_seconds=RETURN_WINDOW_SECONDS,
        observation=provider_registry(request).observation,
        control_hub=require_control_hub(request.app),
    )
    return _ack(result.projection.revision, result.projection.state, result.projection.controls)


@router.get("/{session_id}/projection", response_model=SessionProjectionDocument)
async def projection(
    participant: ParticipantOfPath,
    database: CurrentDatabase,
    session_id: Annotated[UUID, Path()],
) -> SessionProjectionDocument:
    """Return the committed session projection document and its revision.

    The session tab reads live updates over Supabase Realtime; this is the
    authoritative read it starts from and falls back to. The control
    acknowledgement on ``POST /controls`` stays the narrow shape it is: it
    answers what one action committed, not what the tab renders.
    """
    current = await load_projection(database, interview_session_id=session_id)
    return SessionProjectionDocument(
        revision=current.revision, projection=dict(current.projection)
    )


@router.post("/{session_id}/extension-offer", response_model=BindingOfferResult)
async def extension_offer(
    participant: ParticipantOfPath,
    database: CurrentDatabase,
    session_id: Annotated[UUID, Path()],
) -> BindingOfferResult:
    """Mint the single-use offer the session tab delivers to the extension."""
    interview = get_interview_settings()
    allocation_id = await _allocation_once_ready(database, interview_session_id=session_id)
    installation_id = await _linked_installation(
        database, participant=participant, interview_session_id=session_id
    )
    offer = await mint_binding_offer(
        database,
        client_id=participant.client_id,
        client_user_id=participant.client_user_id,
        interview_session_id=session_id,
        extension_installation_id=installation_id,
        preview_runtime_allocation_id=allocation_id,
        protocol_version=interview.extension_protocol_version,
        lifetime_seconds=interview.extension_offer_lifetime_seconds,
    )
    return BindingOfferResult(
        schema_version=1,
        offer_token=offer.offer_token,
        expires_at=_rfc3339(offer.expires_at.isoformat()),
        protocol_version=offer.protocol_version,
        session_id=session_id,
        client_id=participant.client_id,
        extension_installation_id=offer.extension_installation_id,
        preview_runtime_allocation_id=offer.preview_runtime_allocation_id,
    )


def _ack(revision: int, state: str, controls: object) -> SessionControlAck:
    """Render the control acknowledgement from a committed projection."""
    if not isinstance(controls, dict):
        raise TypeError("the session projection carries no controls object")
    return SessionControlAck(
        revision=revision,
        state=state,
        controls=ControlsState(
            microphone=str(controls["microphone"]),
            available=[str(action) for action in controls["available"]],
        ),
    )


def _supported_protocol_versions() -> tuple[str, ...]:
    """The current and previous extension protocol this control plane accepts."""
    interview = get_interview_settings()
    return tuple(
        dict.fromkeys(
            (
                interview.extension_protocol_version,
                interview.extension_previous_protocol_version,
            )
        )
    )


async def _allocation_once_ready(database: Database, *, interview_session_id: UUID) -> UUID:
    """Wait a bounded time for the session origin's allocation to be ready.

    The session tab asks for the offer as soon as the session leaves setup, which
    can be a moment before the claimed preview is bound to the session; it asks
    only once, so the request waits for that moment rather than refusing it.
    """
    deadline = asyncio.get_running_loop().time() + _OFFER_READY_WAIT_SECONDS
    while True:
        try:
            return await _ready_allocation(database, interview_session_id=interview_session_id)
        except GuardRejectedError:
            if asyncio.get_running_loop().time() >= deadline:
                raise
        await asyncio.sleep(_OFFER_READY_POLL_SECONDS)


async def _ready_allocation(
    database: Database, *, interview_session_id: UUID
) -> UUID:
    """Return the allocation the session origin serves, refusing an unready one."""
    async with database.connection() as conn:
        allocation_id = await served_allocation(
            conn, interview_session_id=interview_session_id
        )
        async with conn.cursor() as cur:
            await cur.execute(
                "select state from public.preview_runtime_allocations where id = %s",
                (allocation_id,),
            )
            row = await cur.fetchone()
    state = None if row is None else as_optional_str(row, "state")
    if state not in _READY_ALLOCATION_STATES:
        raise GuardRejectedError(
            machine="preview_runtime_allocation",
            trigger="extension_offer",
            reason=f"the allocation is not ready, found '{state}'",
        )
    return allocation_id


async def _linked_installation(
    database: Database,
    *,
    participant: ParticipantPrincipal,
    interview_session_id: UUID,
) -> UUID:
    """Return the installation this participant proved during preflight.

    The offer is scoped to one installation, so it cannot be presented by a
    different browser profile even if the token leaks.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select extension_installation_id
            from public.participant_preflight_records
            where interview_session_id = %s and passed
              and extension_installation_id is not null
            order by recorded_at desc
            limit 1
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
        if row is None:
            await cur.execute(
                """
                select id from public.extension_installations
                where utrace_user_id = %s and account_link_state = 'linked'
                order by registered_at desc
                limit 1
                """,
                (participant.utrace_user_id,),
            )
            row = await cur.fetchone()
            if row is None:
                raise AggregateNotFoundError(
                    table="extension_installations", row_id=participant.utrace_user_id
                )
            return as_uuid(row, "id")
    return as_uuid(row, "extension_installation_id")


def _rfc3339(value: str) -> str:
    """Render an ISO instant in the UTC form the extension protocol requires."""
    return value.replace("+00:00", "Z")
