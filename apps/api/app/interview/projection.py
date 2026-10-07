"""The session projection the session tab reads over Supabase Realtime.

The projection is derived from committed durable state and written to
`public.session_projections`, which Realtime distributes under row-level
security scoped to the participant. A Realtime notification is a hint; the
durable row stays authoritative and the session tab renders what it carries
rather than an unverified model claim.

Its own `revision` is monotonic, so a surface can drop an out-of-order delivery,
and the control acknowledgement returns the same number the subscription will
see.

Nothing here exposes telemetry or implementation internals: `status_text` is
customer language, which is the [telemetry language
boundary](../../../../docs/PRD.md#telemetry-language-boundary) applied to the
one string the tab renders beside the character.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    ImplementationProgress,
    InterviewPhase,
    InterviewSessionState,
)

from app.db.pool import Database
from app.db.rows import as_bool, as_int, as_optional_str, as_str, as_uuid
from app.envelope import JsonValue
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.interview.character import CharacterProjection, CharacterState, load_character
from app.interview.settings import get_interview_settings
from app.logging import get_logger

__all__ = [
    "SessionProjection",
    "advance_phase_on_connection",
    "load_projection",
    "publish_projection",
    "publish_projection_on_connection",
]

_log = get_logger(__name__)

# The session tab's vocabulary. It is intentionally not the durable enum: the
# participant sees an encounter, not a lifecycle row.
_TAB_STATE_PARTICIPANT_SETUP = "participant_setup"
_CALL_STATES = frozenset({InterviewSessionState.PREPARING.value, InterviewSessionState.LIVE.value})
_TAB_STATE_INTERVIEWING = "interviewing"
_TAB_STATE_PAUSED = "paused"
_TAB_STATE_RECONNECTING = "reconnecting"
_TAB_STATE_ENDED = "ended"

_TAB_PHASE_BY_DURABLE: Mapping[str, str] = {
    InterviewPhase.PARTICIPANT_SETUP.value: "preflight",
    InterviewPhase.GREETING.value: "greeting",
    InterviewPhase.IMPLEMENTATION.value: "implementation",
    InterviewPhase.VALIDATION.value: "validation",
    InterviewPhase.CLOSING.value: "closed",
}

_TAB_PROGRESS_BY_DURABLE: Mapping[str, str | None] = {
    ImplementationProgress.NOT_STARTED.value: None,
    ImplementationProgress.REPRODUCTION_CAPTURED.value: "reproduction_captured",
    ImplementationProgress.MAKING_CHANGES.value: "making_changes",
    ImplementationProgress.PREPARING_PREVIEW.value: "preparing_the_preview",
    ImplementationProgress.READY_FOR_VALIDATION.value: "ready_for_validation",
}

_TAB_END_REASON_BY_CLOSURE: Mapping[str, str] = {
    "finish_later": "finish_later",
    "reconnect_window_expired": "connection_lost",
    "end_confirm": "ended_by_participant",
    "client_stopped": "client_stopped",
    "completed": "completed",
    "no_implementation": "no_implementation",
    "blocked": "blocked",
    "not_validated": "not_validated",
}

_STATUS_TEXT_BY_CHARACTER: Mapping[CharacterState, str] = {
    CharacterState.IDLE: "Ready when you are",
    CharacterState.LISTENING: "Listening",
    CharacterState.THINKING: "Thinking...",
    CharacterState.SPEAKING: "Speaking",
    CharacterState.ACKNOWLEDGEMENT: "Got it",
    CharacterState.CONCERN: "Looking into something",
    CharacterState.PAUSED: "Paused",
    CharacterState.RECONNECTING: "Reconnecting...",
    CharacterState.COMPLETION: "All done",
}

_SETUP_STATUS_TEXT = "Finishing setup"
_PREVIEW_STATUS_TEXT = "Preparing your preview"
_OPEN_PREVIEW_STATUS_TEXT = "Open your preview"
_OPEN_PREVIEW_DETAIL = "Open your preview so {agent_name} can see the workflow as you show it."
_OBSERVATION_UNAVAILABLE = (
    "We cannot see what is happening in the product right now, so let us keep "
    "talking about what you have already described."
)

_CLOSING_ACTIONS: tuple[str, ...] = ("finish_later", "end_confirm")


@dataclass(frozen=True, slots=True)
class SessionProjection:
    """One committed projection row and the revision the tab will see."""

    session_id: UUID
    revision: int
    state: str
    controls: Mapping[str, JsonValue]
    projection: Mapping[str, JsonValue]


_PHASE_ORDER: tuple[InterviewPhase, ...] = (
    InterviewPhase.PARTICIPANT_SETUP,
    InterviewPhase.GREETING,
    InterviewPhase.DISCOVERY,
    InterviewPhase.IMPLEMENTATION,
    InterviewPhase.VALIDATION,
    InterviewPhase.CLOSING,
)


async def advance_phase_on_connection(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID, phase: InterviewPhase
) -> bool:
    """Move a live session's phase forward to ``phase`` and republish its projection.

    The phase only ever advances: a late transcript fragment during validation
    cannot move the tab back to discovery. Returns whether the phase changed.
    """
    earlier = [item.value for item in _PHASE_ORDER[: _PHASE_ORDER.index(phase)]]
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.interview_sessions
            set phase = %s, revision = revision + 1, updated_at = now()
            where id = %s and state = %s and phase = any(%s)
            """,
            (phase.value, interview_session_id, InterviewSessionState.LIVE.value, earlier),
        )
        changed = cur.rowcount == 1
    if changed:
        await publish_projection_on_connection(conn, interview_session_id=interview_session_id)
        _log.info(
            "interview_phase_advanced",
            interview_session_id=str(interview_session_id),
            phase=phase.value,
        )
    return changed


async def return_to_implementation_on_connection(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID
) -> None:
    """Send a validating session back to implementation for a change the person asked for.

    The one way a phase moves backwards: the person, trying the new version, asked
    for a change, so the next version is built while the conversation continues.
    The progress projection starts over with it.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.interview_sessions
            set phase = %s, implementation_progress = %s, revision = revision + 1,
                updated_at = now()
            where id = %s and state = %s and phase = %s
            """,
            (
                InterviewPhase.IMPLEMENTATION.value,
                ImplementationProgress.REPRODUCTION_CAPTURED.value,
                interview_session_id,
                InterviewSessionState.LIVE.value,
                InterviewPhase.VALIDATION.value,
            ),
        )
        changed = cur.rowcount == 1
    if not changed:
        raise GuardRejectedError(
            machine="interview_session",
            trigger="return_to_implementation",
            reason="only a live session in validation goes back to implementation",
        )
    await publish_projection_on_connection(conn, interview_session_id=interview_session_id)
    _log.info(
        "interview_phase_returned_to_implementation",
        interview_session_id=str(interview_session_id),
    )


async def return_to_validation_on_connection(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID
) -> None:
    """Send a session back to validating the version it has, after a change was refused."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.interview_sessions
            set phase = %s, implementation_progress = %s, revision = revision + 1,
                updated_at = now()
            where id = %s and state = %s and phase = %s
            """,
            (
                InterviewPhase.VALIDATION.value,
                ImplementationProgress.READY_FOR_VALIDATION.value,
                interview_session_id,
                InterviewSessionState.LIVE.value,
                InterviewPhase.IMPLEMENTATION.value,
            ),
        )
    await publish_projection_on_connection(conn, interview_session_id=interview_session_id)


async def publish_projection(
    database: Database, *, interview_session_id: UUID
) -> SessionProjection:
    """Recompute and commit the projection in its own transaction."""
    async with database.transaction() as conn:
        return await publish_projection_on_connection(
            conn, interview_session_id=interview_session_id
        )


async def publish_projection_on_connection(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID
) -> SessionProjection:
    """Recompute and commit the projection inside a caller-owned transaction.

    Controllers use this so the projection a participant sees is committed in the
    same transaction as the transition that caused it.
    """
    row = await _load_session_row(conn, interview_session_id)
    character = await load_character(conn, interview_session_id=interview_session_id)
    projection, controls, tab_state = _build(row, character)

    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.session_projections (
                session_id, client_id, client_user_id, product_signal_id,
                revision, projection
            )
            values (%s, %s, %s, %s, 1, %s)
            on conflict (session_id) do update
            set revision = public.session_projections.revision + 1,
                projection = excluded.projection,
                updated_at = now()
            returning revision
            """,
            (
                interview_session_id,
                as_uuid(row, "client_id"),
                as_uuid(row, "client_user_id"),
                as_uuid(row, "product_signal_id"),
                Jsonb(projection),
            ),
        )
        written = await cur.fetchone()
        if written is None:
            raise AggregateNotFoundError(
                table="session_projections", row_id=interview_session_id
            )
        revision = as_int(written, "revision")
        await cur.execute(
            """
            update public.session_projections
            set projection = jsonb_set(projection, '{revision}', to_jsonb(%s::bigint))
            where session_id = %s
            returning projection
            """,
            (revision, interview_session_id),
        )
        stamped = await cur.fetchone()
    if stamped is None:
        raise AggregateNotFoundError(
            table="session_projections", row_id=interview_session_id
        )
    committed = stamped["projection"]
    if not isinstance(committed, Mapping):
        raise TypeError("session_projections.projection is not an object")
    _log.info(
        "session_projection_published",
        interview_session_id=str(interview_session_id),
        revision=revision,
        state=tab_state,
        character_state=character.state.value,
    )
    return SessionProjection(
        session_id=interview_session_id,
        revision=revision,
        state=tab_state,
        controls=controls,
        projection=dict(committed),
    )


async def load_projection(
    database: Database, *, interview_session_id: UUID
) -> SessionProjection:
    """Read the committed projection back without recomputing it."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select revision, projection from public.session_projections where session_id = %s",
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="session_projections", row_id=interview_session_id
        )
    projection = row["projection"]
    if not isinstance(projection, Mapping):
        raise TypeError("session_projections.projection is not an object")
    controls = projection["controls"]
    if not isinstance(controls, Mapping):
        raise TypeError("session projection carries no controls object")
    return SessionProjection(
        session_id=interview_session_id,
        revision=as_int(row, "revision"),
        state=str(projection["state"]),
        controls=dict(controls),
        projection=dict(projection),
    )


async def _load_session_row(
    conn: AsyncConnection[DictRow], interview_session_id: UUID
) -> DictRow:
    """Read every durable fact the projection is derived from."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select sessions.id, sessions.client_id, sessions.client_user_id,
                   sessions.product_signal_id, sessions.state, sessions.phase,
                   sessions.muted, sessions.paused, sessions.closure_reason,
                   sessions.implementation_progress,
                   clients.name as client_name,
                   coalesce(readiness.conversational_ready, false) as conversational,
                   coalesce(readiness.observation_ready, false) as observation,
                   coalesce(readiness.blockers, '{}') as blockers,
                   coalesce(readiness.browser_sdk_ready, false) as preview_reporting,
                   exists (
                     select 1 from public.preview_routes routes
                     where routes.interview_session_id = sessions.id
                       and routes.state = 'active' and routes.readiness_state = 'ready'
                   ) as preview_routed,
                   exists (
                     select 1 from public.validation_attempts attempts
                     where attempts.interview_session_id = sessions.id
                   ) as is_validation
            from public.interview_sessions as sessions
            join public.clients on clients.id = sessions.client_id
            left join public.session_readiness as readiness
              on readiness.interview_session_id = sessions.id
            where sessions.id = %s
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="interview_sessions", row_id=interview_session_id
        )
    return row


def _build(
    row: DictRow, character: CharacterProjection
) -> tuple[dict[str, JsonValue], dict[str, JsonValue], str]:
    """Derive the projection payload, the controls state, and the tab state."""
    settings = get_interview_settings()
    durable_state = as_str(row, "state")
    paused = as_bool(row, "paused")
    conversational = as_bool(row, "conversational")
    observation = as_bool(row, "observation")
    tab_state = _tab_state(
        durable_state=durable_state, paused=paused, character=character.state
    )
    controls: dict[str, JsonValue] = {
        "microphone": "muted" if as_bool(row, "muted") else "unmuted",
        "available": list(
            _available_controls(
                tab_state=tab_state,
                live=durable_state == InterviewSessionState.LIVE.value,
                paused=paused,
            )
        ),
    }
    closure_reason = as_optional_str(row, "closure_reason")
    # The participant can open the preview once its origin serves a ready route.
    preview_ready = as_bool(row, "preview_routed")
    # The preview's browser SDK reports only while the participant has it open.
    preview_open = as_bool(row, "preview_reporting")
    readiness: dict[str, JsonValue] = {
        "conversational": conversational,
        "observation": observation,
    }
    if conversational and not observation and durable_state == InterviewSessionState.LIVE.value:
        readiness["unavailable_detail"] = (
            _OPEN_PREVIEW_DETAIL.format(agent_name=settings.agent_display_name)
            if preview_ready and not preview_open
            else _OBSERVATION_UNAVAILABLE
        )
    projection: dict[str, JsonValue] = {
        "session_id": str(as_uuid(row, "id")),
        "product_signal_id": str(as_uuid(row, "product_signal_id")),
        "revision": 0,
        "client_name": as_str(row, "client_name"),
        "agent_name": settings.agent_display_name,
        "agent_character": settings.agent_character.value,
        "purpose": "validation" if as_bool(row, "is_validation") else "interview",
        "state": tab_state,
        "phase": _tab_phase(phase=as_str(row, "phase"), observation=observation),
        "readiness": readiness,
        "status_text": _status_text(
            tab_state=tab_state,
            character=character.state,
            observation=observation,
            preview_ready=preview_ready,
            preview_open=preview_open,
        ),
        "character": {
            "revision": character.character_revision,
            "state": character.state.value,
            "gazeX": character.gaze_x,
            "gazeY": character.gaze_y,
            "speechEnergy": character.speech_energy,
            "motionScale": character.motion_scale,
        },
        "controls": controls,
        "implementation_progress": _TAB_PROGRESS_BY_DURABLE[
            as_str(row, "implementation_progress")
        ],
        "preview_available": preview_ready,
        "end_reason": (
            _TAB_END_REASON_BY_CLOSURE.get(closure_reason, "blocked")
            if closure_reason is not None
            else None
        ),
    }
    return projection, controls, tab_state


def _tab_state(*, durable_state: str, paused: bool, character: CharacterState) -> str:
    """Map the durable session state onto the tab's encounter vocabulary.

    A preparing session has passed participant setup, so the tab shows the call
    and opens voice; that connection's readiness is what admits the session live.
    """
    if durable_state == InterviewSessionState.CLOSED.value:
        return _TAB_STATE_ENDED
    if durable_state not in _CALL_STATES:
        return _TAB_STATE_PARTICIPANT_SETUP
    if paused:
        return _TAB_STATE_PAUSED
    if character is CharacterState.RECONNECTING:
        return _TAB_STATE_RECONNECTING
    return _TAB_STATE_INTERVIEWING


def _tab_phase(*, phase: str, observation: bool) -> str:
    """Map the durable phase, splitting discovery by the observation boundary.

    Problem discussion may begin while the preview prepares; observed workflow
    probing waits for the complete correlated path, so the participant sees two
    phases where the durable projection records one.
    """
    mapped = _TAB_PHASE_BY_DURABLE.get(phase)
    if mapped is not None:
        return mapped
    return "observed_workflow" if observation else "problem_discussion"


def _available_controls(*, tab_state: str, live: bool, paused: bool) -> tuple[str, ...]:
    """Return the controls the participant may invoke right now.

    Mute and pause act on a live session only, so a session still connecting
    offers just the ways to leave it.
    """
    if tab_state == _TAB_STATE_ENDED:
        return ()
    if not live:
        return _CLOSING_ACTIONS
    if paused:
        return ("resume", *_CLOSING_ACTIONS)
    return ("mute", "unmute", "pause", *_CLOSING_ACTIONS)


def _status_text(
    *,
    tab_state: str,
    character: CharacterState,
    observation: bool,
    preview_ready: bool,
    preview_open: bool,
) -> str:
    """Return the customer-language status the tab renders beside the character.

    Before observation is ready the status says what the participant is waiting
    on: the preview still being prepared, or a ready preview they have not opened.
    """
    if tab_state == _TAB_STATE_PARTICIPANT_SETUP:
        return _SETUP_STATUS_TEXT
    if tab_state == _TAB_STATE_INTERVIEWING and not observation:
        if not preview_ready:
            return _PREVIEW_STATUS_TEXT
        if not preview_open:
            return _OPEN_PREVIEW_STATUS_TEXT
    return _STATUS_TEXT_BY_CHARACTER[character]
