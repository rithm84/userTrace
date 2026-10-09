"""The voice connection lifecycle and the call setup's success path.

Every connection gets a new `voice_run_id`, its own call (the provider session
reference names it), and a participant connection id. Only one connection holds current
participant authority: an authenticated takeover supersedes the previous one,
closes its provider session, and stops accepting its events.

A late setup response cannot activate a superseded or revoked connection. When
that happens the unused provider session is closed through a durable Job and the
cleanup is recorded, rather than being abandoned to expire.

Opening a connection is not idempotent at the provider, so the route commits the
connection row before the external call and reconciles afterwards. A failed
setup closes the row with its reason; it never leaves a connection that looks
current but has no session behind it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    InterviewSessionState,
    VoiceConnectionState,
)

from app.auth.principals import ParticipantPrincipal
from app.connectors.failures import ConnectorFailure, TechnicalCategory
from app.db.kernel import (
    ColumnValue,
    TransitionRequest,
    append_fact,
    apply_transition_on_connection,
)
from app.db.pool import Database
from app.db.rows import as_optional_str, as_str, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, JobIntent, JsonValue, StreamRef
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.ids import uuid7
from app.interview.character import CharacterState, set_character
from app.interview.projection import publish_projection_on_connection
from app.interview.readiness import record_conversational_facts
from app.lifecycle.interview import VOICE_CONNECTION_MACHINE, VoiceConnectionFacts
from app.lifecycle.machine import plan_transition
from app.logging import get_logger
from app.ports.voice import VoiceConnectionRequest, VoicePort, VoiceRejoinRequest

__all__ = [
    "CLOSE_PROVIDER_SESSION",
    "VOICE_AGENT",
    "OpenedVoiceConnection",
    "agent_job_intent",
    "close_voice_connection",
    "open_voice_connection",
]

_log = get_logger(__name__)

CLOSE_PROVIDER_SESSION = "interview.close_provider_session"
VOICE_AGENT = "interview.voice_agent"

_AGENT_TIMEOUT_SECONDS = 7200
_AGENT_MAX_RUNS = 8
_AGENT_BACKOFF_BASE_SECONDS = 2
_AGENT_BACKOFF_MAX_SECONDS = 30
_CLEANUP_TIMEOUT_SECONDS = 60
_CLEANUP_MAX_RUNS = 5
# The canonical technical categories an agent or cleanup Job may be retried
# for. A category outside this set fails the Job rather than repeating it.
_RETRYABLE: tuple[str, ...] = tuple(
    category.value
    for category in (
        TechnicalCategory.TIMEOUT,
        TechnicalCategory.RATE_LIMITED,
        TechnicalCategory.PROVIDER_UNAVAILABLE,
        TechnicalCategory.CAPACITY_EXHAUSTED,
    )
)

#: A running call whose room expires sooner than this is replaced rather than rejoined.
_REJOIN_MARGIN = timedelta(minutes=15)
_OPENABLE_STATES = frozenset(
    {InterviewSessionState.PREPARING.value, InterviewSessionState.LIVE.value}
)


@dataclass(frozen=True, slots=True)
class OpenedVoiceConnection:
    """What the connection route returns once the call is bound."""

    room_url: str
    participant_token: str
    connection_id: UUID
    voice_run_id: UUID
    provider_session_ref: str
    agent_job_id: UUID | None
    """The connection's newly queued voice agent Job, or ``None`` on a rejoin of a
    call whose agent still holds it."""


async def open_voice_connection(
    database: Database,
    voice: VoicePort,
    *,
    participant: ParticipantPrincipal,
    interview_session_id: UUID,
) -> OpenedVoiceConnection:
    """Admit the participant to their session's call, creating it when none is running.

    A call whose agent is still running is the participant's call to rejoin, as a
    reload or a second browser does: they get a new token for the same room, and any
    browser of theirs still in it is removed. Otherwise a new call replaces the old.
    """
    connection_id = uuid7()
    voice_run_id = uuid7()

    async with database.transaction() as conn:
        session = await _lock_session(conn, interview_session_id)
        state = as_str(session, "state")
        if state not in _OPENABLE_STATES:
            raise GuardRejectedError(
                machine="interview_session",
                trigger="open_voice_connection",
                reason=(
                    "a voice connection belongs to a session that is preparing or "
                    f"live, found '{state}'"
                ),
            )
        running = await _running_call(conn, interview_session_id=interview_session_id)
        if running is None:
            await _start_replacement(
                conn,
                client_id=participant.client_id,
                interview_session_id=interview_session_id,
                connection_id=connection_id,
                voice_run_id=voice_run_id,
            )
    if running is not None:
        return await _rejoin(
            voice, participant=participant, interview_session_id=interview_session_id, call=running
        )

    try:
        result = await voice.open_connection(
            VoiceConnectionRequest(
                client_id=participant.client_id,
                interview_session_id=interview_session_id,
                voice_run_id=voice_run_id,
                participant_connection_id=connection_id,
                participant_user_id=participant.client_user_id,
            )
        )
    except ConnectorFailure as failure:
        await close_voice_connection(
            database,
            client_id=participant.client_id,
            connection_id=connection_id,
            reason=f"provider_setup_{failure.category.value}",
            close_provider_session=failure.effect_state.value != "absent",
        )
        raise

    agent_job = agent_job_intent(
        client_id=participant.client_id,
        interview_session_id=interview_session_id,
        connection_id=connection_id,
        voice_run_id=voice_run_id,
        provider_session_ref=result.provider_session_ref,
        room_url=result.room_url,
        room_expires_at=result.room_expires_at,
    )
    async with database.transaction() as conn:
        current = await _lock_connection(conn, connection_id)
        already_closed = as_str(current, "state") == VoiceConnectionState.CLOSED.value
        if already_closed:
            await _schedule_provider_cleanup(
                conn,
                client_id=participant.client_id,
                connection_id=connection_id,
                provider_session_ref=result.provider_session_ref,
                reason="late_setup_response_for_superseded_connection",
            )
            raise GuardRejectedError(
                machine="voice_connection",
                trigger="activate",
                reason=(
                    "this connection was superseded while its call was being created; "
                    "the unused call is being closed"
                ),
            )
        await apply_transition_on_connection(
            conn,
            plan_transition(
                VOICE_CONNECTION_MACHINE,
                VoiceConnectionFacts(provider_session_bound=True, superseded=False),
                trigger="activate",
                current_state=as_str(current, "state"),
                client_id=participant.client_id,
                aggregate_kind=AggregateKind.VOICE_CONNECTION,
                aggregate_id=connection_id,
                expected_revision=_revision(current),
                state_column="state",
                mutation={"provider_session_ref": result.provider_session_ref},
                event=EventAppend(
                    message_id=uuid7(),
                    stream=StreamRef(
                        kind=AggregateKind.VOICE_CONNECTION, id=connection_id
                    ),
                    event_type="voice_connection_activated",
                    payload={
                        "voice_run_id": str(voice_run_id),
                        "interview_session_id": str(interview_session_id),
                    },
                    actor=Actor(
                        kind=ActorKind.CUSTOMER_PARTICIPANT,
                        ref=str(participant.client_user_id),
                    ),
                    causation_id=None,
                    correlation_id=as_uuid(current, "interview_session_id"),
                    occurred_at=datetime.now(tz=UTC),
                ),
                jobs=(agent_job,),
                commands=(),
                audit=AuditIntent(
                    audit_id=uuid7(),
                    subject=StreamRef(
                        kind=AggregateKind.VOICE_CONNECTION, id=connection_id
                    ),
                    action="activate_voice_connection",
                    outcome="active",
                    reason_code=None,
                    actor=Actor(
                        kind=ActorKind.CUSTOMER_PARTICIPANT,
                        ref=str(participant.client_user_id),
                    ),
                    pinned_configuration_release_id=None,
                    detail={"voice_run_id": str(voice_run_id)},
                ),
                additional_locks=(),
            ),
        )
        await set_character(
            conn,
            interview_session_id=interview_session_id,
            state=CharacterState.LISTENING,
        )
        await publish_projection_on_connection(
            conn, interview_session_id=interview_session_id
        )

    await record_conversational_facts(
        database,
        interview_session_id=interview_session_id,
        participant_authenticated=True,
        voice_transport_ready=True,
        sideband_attached=False,
        backend_context_current=True,
    )
    _log.info(
        "voice_connection_opened",
        client_id=str(participant.client_id),
        interview_session_id=str(interview_session_id),
        voice_connection_id=str(connection_id),
        voice_run_id=str(voice_run_id),
    )
    return OpenedVoiceConnection(
        room_url=result.room_url,
        participant_token=result.participant_token,
        connection_id=connection_id,
        voice_run_id=voice_run_id,
        provider_session_ref=result.provider_session_ref,
        agent_job_id=agent_job.job_id,
    )


async def close_voice_connection(
    database: Database,
    *,
    client_id: UUID,
    connection_id: UUID,
    reason: str,
    close_provider_session: bool,
) -> None:
    """Close one connection and, when a provider session exists, clean it up."""
    async with database.transaction() as conn:
        current = await _lock_connection(conn, connection_id)
        if as_str(current, "state") == VoiceConnectionState.CLOSED.value:
            return
        provider_ref = as_optional_str(current, "provider_session_ref")
        jobs: tuple[JobIntent, ...] = ()
        if close_provider_session and provider_ref is not None:
            jobs = (
                _cleanup_job_intent(
                    connection_id=connection_id,
                    provider_session_ref=provider_ref,
                    reason=reason,
                ),
            )
        await apply_transition_on_connection(
            conn,
            _close_request(
                client_id=client_id,
                connection_id=connection_id,
                current=current,
                reason=reason,
                jobs=jobs,
                superseded=False,
            ),
        )
    _log.info(
        "voice_connection_closed",
        client_id=str(client_id),
        voice_connection_id=str(connection_id),
        reason=reason,
        provider_cleanup_scheduled=close_provider_session,
    )


def agent_job_intent(
    *,
    client_id: UUID,
    interview_session_id: UUID,
    connection_id: UUID,
    voice_run_id: UUID,
    provider_session_ref: str,
    room_url: str,
    room_expires_at: datetime,
) -> JobIntent:
    """The durable Job in which the agent joins one connection's call and holds it."""
    now = datetime.now(tz=UTC)
    return JobIntent(
        job_id=uuid7(),
        job_type=VOICE_AGENT,
        input={
            "client_id": str(client_id),
            "interview_session_id": str(interview_session_id),
            "voice_connection_id": str(connection_id),
            "voice_run_id": str(voice_run_id),
            "provider_session_ref": provider_session_ref,
            "room_url": room_url,
            "room_expires_at": room_expires_at.isoformat(),
        },
        deadline_at=now + timedelta(seconds=_AGENT_TIMEOUT_SECONDS),
        timeout_seconds=_AGENT_TIMEOUT_SECONDS,
        max_runs=_AGENT_MAX_RUNS,
        resource_budget={"call_seconds": _AGENT_TIMEOUT_SECONDS},
        retryable_categories=_RETRYABLE,
        backoff_base_seconds=_AGENT_BACKOFF_BASE_SECONDS,
        backoff_max_seconds=_AGENT_BACKOFF_MAX_SECONDS,
        concurrency_key=f"voice_agent:{interview_session_id}",
        cancellation_scope_id=None,
        not_before=now,
    )


def _cleanup_job_intent(
    *, connection_id: UUID, provider_session_ref: str, reason: str
) -> JobIntent:
    """The durable Job that closes a superseded or unused call."""
    now = datetime.now(tz=UTC)
    return JobIntent(
        job_id=uuid7(),
        job_type=CLOSE_PROVIDER_SESSION,
        input={
            "voice_connection_id": str(connection_id),
            "provider_session_ref": provider_session_ref,
            "reason": reason,
        },
        deadline_at=now + timedelta(seconds=_CLEANUP_TIMEOUT_SECONDS * _CLEANUP_MAX_RUNS),
        timeout_seconds=_CLEANUP_TIMEOUT_SECONDS,
        max_runs=_CLEANUP_MAX_RUNS,
        resource_budget={},
        retryable_categories=_RETRYABLE,
        backoff_base_seconds=_AGENT_BACKOFF_BASE_SECONDS,
        backoff_max_seconds=_AGENT_BACKOFF_MAX_SECONDS,
        concurrency_key=None,
        cancellation_scope_id=None,
        not_before=now,
    )


async def _schedule_provider_cleanup(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    connection_id: UUID,
    provider_session_ref: str,
    reason: str,
) -> None:
    """Record the cleanup of a call no connection may use."""
    await append_fact(
        conn,
        client_id=client_id,
        event=EventAppend(
            message_id=uuid7(),
            stream=StreamRef(kind=AggregateKind.VOICE_CONNECTION, id=connection_id),
            event_type="voice_provider_session_orphaned",
            payload={"reason": reason},
            actor=Actor(kind=ActorKind.SYSTEM, ref="interview.voice"),
            causation_id=None,
            correlation_id=None,
            occurred_at=datetime.now(tz=UTC),
        ),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=StreamRef(kind=AggregateKind.VOICE_CONNECTION, id=connection_id),
            action="close_orphaned_provider_session",
            outcome="scheduled",
            reason_code=reason,
            actor=Actor(kind=ActorKind.SYSTEM, ref="interview.voice"),
            pinned_configuration_release_id=None,
            detail={},
        ),
        jobs=(
            _cleanup_job_intent(
                connection_id=connection_id,
                provider_session_ref=provider_session_ref,
                reason=reason,
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class _RunningCall:
    """A session's active connection whose agent is still holding its call."""

    connection_id: UUID
    voice_run_id: UUID
    provider_session_ref: str
    room_url: str
    room_expires_at: datetime


async def _running_call(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID
) -> _RunningCall | None:
    """The session's call the participant can rejoin, if its agent is still running it.

    A room this close to expiring is not rejoined: a new call outlives it.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select connections.id, connections.voice_run_id, connections.provider_session_ref,
                   jobs.input ->> 'room_url' as room_url,
                   jobs.input ->> 'room_expires_at' as room_expires_at
            from public.voice_connections as connections
            join public.jobs
              on jobs.job_type = %s
             and jobs.input ->> 'voice_connection_id' = connections.id::text
            where connections.interview_session_id = %s
              and connections.state = %s
              and jobs.state in ('pending', 'ready', 'leased')
            """,
            (VOICE_AGENT, interview_session_id, VoiceConnectionState.ACTIVE.value),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    expires_at = datetime.fromisoformat(as_str(row, "room_expires_at"))
    if expires_at - datetime.now(tz=UTC) < _REJOIN_MARGIN:
        return None
    return _RunningCall(
        connection_id=as_uuid(row, "id"),
        voice_run_id=as_uuid(row, "voice_run_id"),
        provider_session_ref=as_str(row, "provider_session_ref"),
        room_url=as_str(row, "room_url"),
        room_expires_at=expires_at,
    )


async def _rejoin(
    voice: VoicePort,
    *,
    participant: ParticipantPrincipal,
    interview_session_id: UUID,
    call: _RunningCall,
) -> OpenedVoiceConnection:
    """Admit the participant to the call their session's agent is still holding."""
    token = await voice.rejoin_connection(
        VoiceRejoinRequest(
            client_id=participant.client_id,
            interview_session_id=interview_session_id,
            provider_session_ref=call.provider_session_ref,
            room_expires_at=call.room_expires_at,
            participant_user_id=participant.client_user_id,
        )
    )
    _log.info(
        "voice_connection_rejoined",
        client_id=str(participant.client_id),
        interview_session_id=str(interview_session_id),
        voice_connection_id=str(call.connection_id),
    )
    return OpenedVoiceConnection(
        room_url=call.room_url,
        participant_token=token,
        connection_id=call.connection_id,
        voice_run_id=call.voice_run_id,
        provider_session_ref=call.provider_session_ref,
        agent_job_id=None,
    )


async def _start_replacement(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    interview_session_id: UUID,
    connection_id: UUID,
    voice_run_id: UUID,
) -> None:
    """Close the session's open connections and insert the one that replaces them."""
    superseded = await _supersede_current(
        conn, client_id=client_id, interview_session_id=interview_session_id
    )
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.voice_connections (
                id, client_id, interview_session_id, state, voice_run_id,
                participant_connection_id
            )
            values (%s, %s, %s, %s, %s, %s)
            """,
            (
                connection_id,
                client_id,
                interview_session_id,
                VoiceConnectionState.CONNECTING.value,
                voice_run_id,
                connection_id,
            ),
        )
        if superseded:
            # The link is written after the row it points at exists, which is
            # also after the one-current index has seen the old rows closed.
            await cur.execute(
                """
                update public.voice_connections
                set superseded_by_id = %s, revision = revision + 1, updated_at = now()
                where id = any(%s)
                """,
                (connection_id, list(superseded)),
            )


async def _supersede_current(
    conn: AsyncConnection[DictRow], *, client_id: UUID, interview_session_id: UUID
) -> tuple[UUID, ...]:
    """Close every open connection for this session before a takeover.

    Returns the connections it closed so the caller can link them to the
    replacement once that row exists.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, state, revision, provider_session_ref, interview_session_id
            from public.voice_connections
            where interview_session_id = %s and state <> %s
            for update
            """,
            (interview_session_id, VoiceConnectionState.CLOSED.value),
        )
        open_connections = list(await cur.fetchall())
    closed: list[UUID] = []
    for row in open_connections:
        provider_ref = as_optional_str(row, "provider_session_ref")
        jobs: tuple[JobIntent, ...] = ()
        if provider_ref is not None:
            jobs = (
                _cleanup_job_intent(
                    connection_id=as_uuid(row, "id"),
                    provider_session_ref=provider_ref,
                    reason="superseded_by_authenticated_takeover",
                ),
            )
        await apply_transition_on_connection(
            conn,
            _close_request(
                client_id=client_id,
                connection_id=as_uuid(row, "id"),
                current=row,
                reason="superseded_by_authenticated_takeover",
                jobs=jobs,
                superseded=True,
            ),
        )
        closed.append(as_uuid(row, "id"))
    return tuple(closed)


def _close_request(
    *,
    client_id: UUID,
    connection_id: UUID,
    current: DictRow,
    reason: str,
    jobs: Sequence[JobIntent],
    superseded: bool,
) -> TransitionRequest:
    """Build the kernel transition that closes one voice connection."""
    mutation: dict[str, ColumnValue] = {"closed_at": datetime.now(tz=UTC)}
    payload: Mapping[str, JsonValue] = {"reason": reason, "superseded": superseded}
    actor = Actor(kind=ActorKind.SYSTEM, ref="interview.voice")
    return plan_transition(
        VOICE_CONNECTION_MACHINE,
        VoiceConnectionFacts(
            provider_session_bound=current["provider_session_ref"] is not None,
            superseded=superseded,
        ),
        trigger="close",
        current_state=as_str(current, "state"),
        client_id=client_id,
        aggregate_kind=AggregateKind.VOICE_CONNECTION,
        aggregate_id=connection_id,
        expected_revision=_revision(current),
        state_column="state",
        mutation=mutation,
        event=EventAppend(
            message_id=uuid7(),
            stream=StreamRef(kind=AggregateKind.VOICE_CONNECTION, id=connection_id),
            event_type="voice_connection_closed",
            payload=payload,
            actor=actor,
            causation_id=None,
            correlation_id=as_uuid(current, "interview_session_id"),
            occurred_at=datetime.now(tz=UTC),
        ),
        jobs=jobs,
        commands=(),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=StreamRef(kind=AggregateKind.VOICE_CONNECTION, id=connection_id),
            action="close_voice_connection",
            outcome="closed",
            reason_code=reason,
            actor=actor,
            pinned_configuration_release_id=None,
            detail={"provider_cleanup_jobs": len(jobs)},
        ),
        additional_locks=(),
    )


async def _lock_session(
    conn: AsyncConnection[DictRow], interview_session_id: UUID
) -> DictRow:
    """Lock the Interview Session a connection is being opened against."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select id, state, revision from public.interview_sessions where id = %s for update",
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="interview_sessions", row_id=interview_session_id
        )
    return row


async def _lock_connection(
    conn: AsyncConnection[DictRow], connection_id: UUID
) -> DictRow:
    """Lock one voice connection row."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, state, revision, provider_session_ref, interview_session_id
            from public.voice_connections
            where id = %s
            for update
            """,
            (connection_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="voice_connections", row_id=connection_id)
    return row


def _revision(row: DictRow) -> int:
    """Return a locked row's revision, refusing a non-integer column."""
    value = row["revision"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("voice_connections.revision is not an integer")
    return value
