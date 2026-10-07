"""Participant routes.

Every route proves the same originating user. The session tab is the live-session
anchor; the preview tab and the extension join through separate audience-bound
lane credentials that the control plane joins on one session identity.

The session tab's own four routes live in `app.api.sessions`; the invitation
claim lives in `app.api.invitations`.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path

from app.api.responses import (
    ApiModel,
    ParticipantAccountResponse,
    ParticipantPreferencesView,
    PreviewHandoffResponse,
    SessionProjectionResponse,
)
from app.auth.dependencies import (
    AuthenticatedIdentity,
    CurrentDatabase,
    CurrentSettings,
    ParticipantOfPath,
)
from app.errors import GuardRejectedError
from app.extension.preferences import PreferencesRecord, load_preferences, update_preferences
from app.preview.config import get_preview_settings
from app.preview.handoff import mint_front_door_claim
from app.preview.records import RouteReadinessState
from app.preview.routing import route_for_session
from app.queries import load_participant_account, load_session, participant_user_id

__all__ = ["router"]

router = APIRouter(prefix="/v1", tags=["participant"])

# The invitation claim lives in `app.api.invitations`, which owns the claim
# completion: identity binding, the Interview Session, and the preview preparation
# Job.


@router.get("/sessions/{session_id}", response_model=SessionProjectionResponse)
async def get_session(
    participant: ParticipantOfPath,
    database: CurrentDatabase,
    session_id: Annotated[UUID, Path()],
) -> SessionProjectionResponse:
    """Return the durable session projection: never an unverified model claim."""
    return SessionProjectionResponse(
        session=await load_session(database, session_id=session_id)
    )


@router.post(
    "/sessions/{session_id}/preview-handoff", response_model=PreviewHandoffResponse
)
async def preview_handoff(
    participant: ParticipantOfPath,
    database: CurrentDatabase,
    settings: CurrentSettings,
    session_id: Annotated[UUID, Path()],
) -> PreviewHandoffResponse:
    """Mint the one-time front-door claim for this session's current route.

    The claim opens the origin the session's committed route revision serves, so
    a session whose preview is not routed yet is refused rather than handed a URL
    that cannot answer.
    """
    route = await route_for_session(database, interview_session_id=session_id)
    if route is None:
        raise GuardRejectedError(
            machine="preview_route",
            trigger="preview_handoff",
            reason="the Interview Session has no committed preview route yet",
        )
    if route.client_id != participant.client_id:
        raise GuardRejectedError(
            machine="preview_route",
            trigger="preview_handoff",
            reason="the committed route belongs to another client",
        )
    if route.readiness_state is not RouteReadinessState.READY:
        raise GuardRejectedError(
            machine="preview_route",
            trigger="preview_handoff",
            reason=(
                "the route's durable readiness is withdrawn, so the preview cannot "
                "be claimed until a new revision is committed"
            ),
        )
    claim = await mint_front_door_claim(
        database,
        client_id=participant.client_id,
        interview_session_id=session_id,
        preview_route_id=route.preview_route_id,
        public_preview_host=route.public_preview_host,
        lifetime_seconds=get_preview_settings().handoff_code_ttl_seconds,
    )
    return PreviewHandoffResponse(
        front_door_url=claim.url, expires_at=claim.expires_at.isoformat()
    )


class PreferencesUpdateRequest(ApiModel):
    """A requested change, naming the preferences revision the page read."""

    expected_revision: int
    visual_guidance_enabled: bool
    agent_cursor_enabled: bool


@router.get("/participants/me", response_model=ParticipantAccountResponse)
async def get_participant_account(
    identity: AuthenticatedIdentity, database: CurrentDatabase
) -> ParticipantAccountResponse:
    """Return the signed-in participant's own sessions and global preferences."""
    account = await load_participant_account(database, auth_user_id=identity.auth_user_id)
    async with database.transaction() as conn:
        preferences = await load_preferences(conn, utrace_user_id=account.utrace_user_id)
    return ParticipantAccountResponse(
        participation_count=account.participation_count,
        interviews=account.interviews,
        preferences=_preferences_view(preferences),
    )


@router.patch("/participants/me/preferences", response_model=ParticipantPreferencesView)
async def update_participant_preferences(
    identity: AuthenticatedIdentity,
    database: CurrentDatabase,
    body: PreferencesUpdateRequest,
) -> ParticipantPreferencesView:
    """Apply one preferences change against the revision the page read."""
    updated = await update_preferences(
        database,
        utrace_user_id=await participant_user_id(database, auth_user_id=identity.auth_user_id),
        expected_revision=body.expected_revision,
        visual_guidance_enabled=body.visual_guidance_enabled,
        agent_cursor_enabled=body.agent_cursor_enabled,
    )
    return _preferences_view(updated)


def _preferences_view(record: PreferencesRecord) -> ParticipantPreferencesView:
    return ParticipantPreferencesView(
        revision=record.preferences_revision,
        visual_guidance_enabled=record.visual_guidance_enabled,
        agent_cursor_enabled=record.agent_cursor_enabled,
    )
