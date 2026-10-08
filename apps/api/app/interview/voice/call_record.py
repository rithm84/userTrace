"""What one voice agent run records about its call.

The run serves one voice connection (`AgentSubject`). It records the conversation
as transcript fragments on the connection's clock, the conversational half of
readiness while it is in the call, and, once the call ends, the bounded reconnect
window in which the participant may come back.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from pipecat.processors.aggregators.llm_context import LLMContextMessage
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    InterviewPhase,
    VoiceConnectionState,
)

from app.context.sources import recent_conversation_items
from app.db.kernel import append_fact
from app.db.pool import Database
from app.db.rows import as_bool, as_str, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, StreamRef
from app.errors import AggregateNotFoundError
from app.execution.jobs import ClaimedJob
from app.ids import uuid7
from app.interview.character import CharacterState, set_character
from app.interview.evidence import TranscriptFragment, record_transcript_fragment
from app.interview.projection import advance_phase_on_connection, publish_projection_on_connection
from app.interview.readiness import record_conversational_facts
from app.interview.reconnect import reconnect_window_job
from app.interview.voice.channel import VoiceSubject
from app.logging import get_logger

__all__ = [
    "AgentSubject",
    "conversation_so_far",
    "mark_attached",
    "record_call_ended",
    "record_fragment",
    "subject_from_job",
]

_log = get_logger(__name__)
_AGENT_ACTOR: Final[Actor] = Actor(kind=ActorKind.SERVICE, ref="interview.voice.agent")


@dataclass(frozen=True, slots=True)
class AgentSubject:
    """The connection and call one agent run serves."""

    client_id: UUID
    interview_session_id: UUID
    product_signal_id: UUID
    voice_connection_id: UUID
    voice_run_id: UUID
    provider_session_ref: str
    room_url: str
    room_expires_at: datetime
    connection_opened_at: datetime
    """When the connection opened: its transcript's clock counts from here, so an agent
    run that rejoins the call after a worker restart carries on the same clock."""

    def voice(self) -> VoiceSubject:
        return VoiceSubject(
            client_id=self.client_id,
            product_signal_id=self.product_signal_id,
            interview_session_id=self.interview_session_id,
            voice_connection_id=self.voice_connection_id,
        )

    def call_ms(self, at: datetime) -> int:
        """`at` on the call's clock, in milliseconds since the connection opened."""
        return max(0, round((at - self.connection_opened_at).total_seconds() * 1000))


async def subject_from_job(database: Database, claimed: ClaimedJob) -> AgentSubject:
    """Read the Job's frozen input and resolve the case and connection it belongs to."""
    session_id = UUID(str(claimed.input["interview_session_id"]))
    connection_id = UUID(str(claimed.input["voice_connection_id"]))
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select sessions.product_signal_id, connections.created_at as opened_at
            from public.interview_sessions as sessions
            join public.voice_connections as connections
              on connections.interview_session_id = sessions.id and connections.id = %s
            where sessions.id = %s
            """,
            (connection_id, session_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError("voice_connections", connection_id)
    return AgentSubject(
        client_id=UUID(str(claimed.input["client_id"])),
        interview_session_id=session_id,
        product_signal_id=as_uuid(row, "product_signal_id"),
        voice_connection_id=connection_id,
        voice_run_id=UUID(str(claimed.input["voice_run_id"])),
        provider_session_ref=str(claimed.input["provider_session_ref"]),
        room_url=str(claimed.input["room_url"]),
        room_expires_at=datetime.fromisoformat(str(claimed.input["room_expires_at"])),
        connection_opened_at=row["opened_at"],
    )


async def record_fragment(
    database: Database,
    *,
    subject: AgentSubject,
    speaker: str,
    event_type: str,
    start_ms: int,
    end_ms: int,
    text: str,
) -> None:
    """Persist one fragment of what was said; the participant answering starts discovery."""
    async with database.transaction() as conn:
        await record_transcript_fragment(
            conn,
            client_id=subject.client_id,
            fragment=TranscriptFragment(
                interview_session_id=subject.interview_session_id,
                voice_connection_id=subject.voice_connection_id,
                voice_run_id=subject.voice_run_id,
                provider_session_ref=subject.provider_session_ref,
                speaker=speaker,
                provider_event_type=event_type,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
            ),
            provenance={
                "provider": "utrace_voice_agent",
                "provider_session_ref": subject.provider_session_ref,
                "voice_run_id": str(subject.voice_run_id),
                "recorded_at": datetime.now(tz=UTC).isoformat(),
            },
        )
        if speaker == "participant":
            await advance_phase_on_connection(
                conn,
                interview_session_id=subject.interview_session_id,
                phase=InterviewPhase.DISCOVERY,
            )


async def conversation_so_far(
    database: Database, *, subject: AgentSubject, limit: int
) -> list[LLMContextMessage]:
    """What was said earlier in this session, as the agent's conversation so far.

    A call that replaces a lost one starts from the conversation on record, so the
    agent picks up where it left off instead of starting over.
    """
    async with database.connection() as conn:
        turns = await recent_conversation_items(
            conn, interview_session_id=subject.interview_session_id, limit=limit
        )
    messages: list[LLMContextMessage] = []
    for turn in turns:
        text = str(turn.body["text"]).strip()
        if text and turn.body["speaker"] == "participant":
            messages.append({"role": "user", "content": text})
        elif text:
            messages.append({"role": "assistant", "content": text})
    return messages


async def mark_attached(database: Database, *, subject: AgentSubject) -> None:
    """Record that the agent is in the call with the participant, completing readiness."""
    await record_conversational_facts(
        database,
        interview_session_id=subject.interview_session_id,
        participant_authenticated=True,
        voice_transport_ready=True,
        sideband_attached=True,
        backend_context_current=True,
    )
    async with database.transaction() as conn:
        await publish_projection_on_connection(
            conn, interview_session_id=subject.interview_session_id
        )


async def record_call_ended(database: Database, *, subject: AgentSubject, reason: str) -> None:
    """Withdraw the call's half of readiness and open the reconnect window.

    A call a takeover superseded ends after its successor started, when its room is
    deleted, so it leaves the session's readiness to the successor.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select superseded_by_id from public.voice_connections where id = %s",
            (subject.voice_connection_id,),
        )
        connection = await cur.fetchone()
    if connection is None:
        raise AggregateNotFoundError("voice_connections", subject.voice_connection_id)
    if connection["superseded_by_id"] is not None:
        _log.info(
            "voice_agent_superseded_call_ended",
            interview_session_id=str(subject.interview_session_id),
            voice_connection_id=str(subject.voice_connection_id),
            close_reason=reason,
        )
        return
    await record_conversational_facts(
        database,
        interview_session_id=subject.interview_session_id,
        participant_authenticated=True,
        voice_transport_ready=False,
        sideband_attached=False,
        backend_context_current=True,
    )
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update public.voice_connections
                set state = %s, closed_at = now(), revision = revision + 1, updated_at = now()
                where id = %s and state <> %s
                """,
                (
                    VoiceConnectionState.CLOSED.value,
                    subject.voice_connection_id,
                    VoiceConnectionState.CLOSED.value,
                ),
            )
            await cur.execute(
                "select state, paused, product_signal_id from public.interview_sessions where id = %s",
                (subject.interview_session_id,),
            )
            session = await cur.fetchone()
        if session is not None and as_str(session, "state") != "closed":
            await set_character(
                conn,
                interview_session_id=subject.interview_session_id,
                state=CharacterState.RECONNECTING,
            )
            if not as_bool(session, "paused"):
                await _open_reconnect_window(
                    conn,
                    subject=subject,
                    product_signal_id=as_uuid(session, "product_signal_id"),
                    reason=reason,
                )
        await publish_projection_on_connection(
            conn, interview_session_id=subject.interview_session_id
        )
    _log.info(
        "voice_agent_detached",
        interview_session_id=str(subject.interview_session_id),
        voice_connection_id=str(subject.voice_connection_id),
        close_reason=reason,
    )


async def _open_reconnect_window(
    conn: AsyncConnection[DictRow], *, subject: AgentSubject, product_signal_id: UUID, reason: str
) -> None:
    """Start the bounded window in which the lost connection must be replaced."""
    now = datetime.now(tz=UTC)
    stream = StreamRef(kind=AggregateKind.INTERVIEW_SESSION, id=subject.interview_session_id)
    await append_fact(
        conn,
        client_id=subject.client_id,
        event=EventAppend(
            message_id=uuid7(),
            stream=stream,
            event_type="voice_reconnect_window_opened",
            payload={
                "voice_connection_id": str(subject.voice_connection_id),
                "close_reason": reason,
            },
            actor=_AGENT_ACTOR,
            causation_id=None,
            correlation_id=product_signal_id,
            occurred_at=now,
        ),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=stream,
            action="open_reconnect_window",
            outcome="window_opened",
            reason_code=reason,
            actor=_AGENT_ACTOR,
            pinned_configuration_release_id=None,
            detail={"voice_connection_id": str(subject.voice_connection_id)},
        ),
        jobs=(
            reconnect_window_job(
                interview_session_id=subject.interview_session_id,
                lost_voice_connection_id=subject.voice_connection_id,
                now=now,
            ),
        ),
    )
