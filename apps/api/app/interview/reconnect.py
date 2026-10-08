"""The bounded reconnect window after a lost voice connection.

When the participant leaves the call or loses its connection, the session reports
`reconnecting` and the session tab opens a new connection. A connection that is
not replaced within the window is a departure: the encounter is checkpointed
exactly as **Finish later** checkpoints it, and the case's return deadline starts,
so the person can come back from their uTrace account or the private link.
Already-authorized implementation and checks continue meanwhile; no validation
completes while the person is absent.

Contract: docs/arch-components/interview-and-voice.md#pause-interruption-and-departure
"""

from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from utrace_contracts.domain.lifecycle import ActorKind, InterviewSessionState

from app.db.pool import Database
from app.db.rows import as_bool, as_str
from app.envelope import Actor, JobIntent
from app.execution.jobs import ClaimedJob
from app.ids import uuid7
from app.interview.character import CharacterState, set_character
from app.interview.controls import RETURN_WINDOW_SECONDS, close_encounter
from app.interview.projection import publish_projection
from app.lifecycle.session_controls import close_session_on_connection, lock_session
from app.logging import get_logger

__all__ = [
    "EXPIRE_RECONNECT_WINDOW",
    "RECONNECT_WINDOW_EXPIRED",
    "expire_reconnect_window",
    "reconnect_window_job",
]

_log = get_logger(__name__)

EXPIRE_RECONNECT_WINDOW: Final[str] = "interview.expire_reconnect_window"
#: The session's closure reason when its lost connection was never replaced.
RECONNECT_WINDOW_EXPIRED: Final[str] = "reconnect_window_expired"
#: How long a lost connection may stay unreplaced. The session tab reconnects within
#: seconds on its own, so this only runs out when the person has actually gone.
_RECONNECT_WINDOW_SECONDS: Final[int] = 120
_JOB_TIMEOUT_SECONDS: Final[int] = 60
_ACTOR: Final[Actor] = Actor(kind=ActorKind.SERVICE, ref="interview.reconnect")


def reconnect_window_job(
    *, interview_session_id: UUID, lost_voice_connection_id: UUID, now: datetime
) -> JobIntent:
    """The Job that closes the encounter if the lost connection is not replaced in time."""
    due = now + timedelta(seconds=_RECONNECT_WINDOW_SECONDS)
    return JobIntent(
        job_id=uuid7(),
        job_type=EXPIRE_RECONNECT_WINDOW,
        input={
            "interview_session_id": str(interview_session_id),
            "lost_voice_connection_id": str(lost_voice_connection_id),
        },
        deadline_at=due + timedelta(seconds=_JOB_TIMEOUT_SECONDS * 5),
        timeout_seconds=_JOB_TIMEOUT_SECONDS,
        max_runs=3,
        resource_budget={"kind": EXPIRE_RECONNECT_WINDOW},
        retryable_categories=[],
        backoff_base_seconds=5,
        backoff_max_seconds=30,
        concurrency_key=f"reconnect_window:{interview_session_id}",
        cancellation_scope_id=None,
        not_before=due,
    )


async def expire_reconnect_window(database: Database, claimed: ClaimedJob) -> None:
    """Checkpoint the encounter unless its lost connection was replaced or it already ended.

    The check and the close share the session's row lock, which opening a voice
    connection also takes, so a reconnection either lands first and keeps the
    session live, or finds it closed and is refused.
    """
    interview_session_id = UUID(str(claimed.input["interview_session_id"]))
    lost_voice_connection_id = UUID(str(claimed.input["lost_voice_connection_id"]))
    async with database.transaction() as conn:
        session = await lock_session(conn, interview_session_id)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select exists (
                    select 1 from public.voice_connections as later
                    join public.voice_connections as lost on lost.id = %s
                    where later.interview_session_id = %s
                      and later.created_at > lost.created_at
                ) as replaced
                """,
                (lost_voice_connection_id, interview_session_id),
            )
            row = await cur.fetchone()
        replaced = row is not None and as_bool(row, "replaced")
        state = as_str(session, "state")
        if state == InterviewSessionState.CLOSED.value or as_bool(session, "paused") or replaced:
            _log.info(
                "reconnect_window_kept_session",
                interview_session_id=str(interview_session_id),
                state=state,
                paused=as_bool(session, "paused"),
                replaced=replaced,
            )
            return
        await close_session_on_connection(
            conn,
            client_id=claimed.client_id,
            session=session,
            session_id=interview_session_id,
            closure_reason=RECONNECT_WINDOW_EXPIRED,
            actor=_ACTOR,
            now=datetime.now(tz=UTC),
            departure=True,
            return_window_seconds=RETURN_WINDOW_SECONDS,
        )
        await set_character(
            conn, interview_session_id=interview_session_id, state=CharacterState.COMPLETION
        )
    await close_encounter(
        database,
        client_id=claimed.client_id,
        interview_session_id=interview_session_id,
        reason=RECONNECT_WINDOW_EXPIRED,
    )
    await publish_projection(database, interview_session_id=interview_session_id)
    _log.info(
        "reconnect_window_expired",
        interview_session_id=str(interview_session_id),
        lost_voice_connection_id=str(lost_voice_connection_id),
    )
