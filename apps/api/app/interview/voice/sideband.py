"""The durable worker that observes one GPT-Live session.

The backend attaches an authenticated sideband WebSocket to a session the
browser already owns. It never sends `session.start`, never sends audio, and
never relays a browser-supplied event: browser-supplied voice events cannot
authorize backend tools or replace authenticated sideband results.

The worker persists transcript fragments and the playback evidence the transport
exposes, answers client delegations, and applies the session-control Commands
the participant's Mute, Pause, and Resume queued. It runs as a Job so a worker
restart resumes from durable state rather than losing the session's record.

Session descriptions and transport credentials are excluded from logs and model
context; nothing here handles either.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from utrace_contracts.domain.lifecycle import (
    InterviewPhase,
    VoiceConnectionState,
)

from app.connectors.openai_live_events import (
    ContextAppendAcknowledged,
    DelegationCreated,
    InputAudioMuteChanged,
    InputAudioReflected,
    OutputAudioSegment,
    ResponseDelegationEnvelope,
    SessionClosed,
    SessionError,
    SessionInfo,
    SessionStarted,
    SessionUpdated,
    TranscriptFragment,
    UnrecognizedEvent,
)
from app.db.pool import Database
from app.db.rows import as_str, as_uuid
from app.errors import AggregateNotFoundError
from app.execution.jobs import ClaimedJob
from app.interview.character import CharacterState, change_character, set_character
from app.interview.evidence import (
    PlaybackSegment,
    record_playback_segments,
    record_transcript_fragment,
)
from app.interview.evidence import (
    TranscriptFragment as StoredFragment,
)
from app.interview.projection import (
    advance_phase_on_connection,
    publish_projection_on_connection,
)
from app.interview.readiness import record_conversational_facts
from app.interview.voice.acknowledgements import Acknowledgements
from app.interview.voice.activity_feed import feed_activity
from app.interview.voice.adapter import GptLiveVoiceAdapter
from app.interview.voice.agent_speech import AgentSpeech
from app.interview.voice.checks_shown import ShownChecks
from app.interview.voice.cues import (
    announce_gate_failure,
    announce_ready_preview,
    apply_controls,
    begin_validation_when_ready,
    prompt_open_preview,
)
from app.interview.voice.delegation import DelegationRunner, DelegationSubject
from app.interview.voice.session_changes import SessionChanges
from app.interview.voice.speech_gestures import (
    GestureMoments,
    SpokenWindow,
    show_what_is_said,
)
from app.interview.voice.turns import Floor, TurnDetector
from app.logging import get_logger

__all__ = [
    "SidebandSubject",
    "SidebandSummary",
    "VoiceSessionMisconfigured",
    "drain_sideband",
    "subject_from_job",
]

_log = get_logger(__name__)


class VoiceSessionMisconfigured(RuntimeError):
    """The provider session is not the client-delegation session uTrace created."""


#: Generated-audio evidence is written in batches of this many segments (200 ms
#: each), so it never queues the transcript and character updates behind it.
_PLAYBACK_BATCH_SEGMENTS = 10
_CLIENT_DELEGATION = "client"
_GENERATED = "generated"

#: Speakable context sent once the backend is listening to a call nobody has spoken
#: on yet. GPT-Live waits for the caller by default, and conversational readiness
#: permits the greeting, so this cue is what makes the agent open the call.
_GREETING_CUE = (
    "The call has just connected and the person is listening. Open the conversation "
    "now: greet them, say who you are and who you are helping, explain that you will "
    "ask short questions while they reproduce the workflow, that you can see structured "
    "product events from this session, and that you may point out fields or buttons. "
    "Then ask what they would like help with."
)

@dataclass(frozen=True, slots=True)
class SidebandSubject:
    """The connection one sideband run observes."""

    client_id: UUID
    interview_session_id: UUID
    product_signal_id: UUID
    voice_connection_id: UUID
    voice_run_id: UUID
    provider_session_ref: str


@dataclass(frozen=True, slots=True)
class SidebandSummary:
    """What one sideband run recorded before the provider session closed."""

    fragments: int
    playback_segments: int
    delegations: int
    close_reason: str | None


async def subject_from_job(database: Database, claimed: ClaimedJob) -> SidebandSubject:
    """Read the Job's frozen input and resolve the case it belongs to."""
    session_id = UUID(str(claimed.input["interview_session_id"]))
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select product_signal_id from public.interview_sessions where id = %s",
            (session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"interview session {session_id} does not exist")
    return SidebandSubject(
        client_id=UUID(str(claimed.input["client_id"])),
        interview_session_id=session_id,
        product_signal_id=as_uuid(row, "product_signal_id"),
        voice_connection_id=UUID(str(claimed.input["voice_connection_id"])),
        voice_run_id=UUID(str(claimed.input["voice_run_id"])),
        provider_session_ref=str(claimed.input["provider_session_ref"]),
    )


async def drain_sideband(
    database: Database,
    adapter: GptLiveVoiceAdapter,
    delegations: DelegationRunner,
    *,
    subject: SidebandSubject,
) -> SidebandSummary:
    """Attach the sideband and record the session until the provider closes it."""
    counters = _Counters()
    playback: list[PlaybackSegment] = []
    async with adapter.sessions.attach_sideband(
        provider_session_id=subject.provider_session_ref
    ) as sideband:
        await _mark_attached(database, subject=subject)
        if await _awaiting_greeting(database, interview_session_id=subject.interview_session_id):
            await sideband.append_commentary(content=_GREETING_CUE, delegation_id=None)
        # Set exactly while the agent is not talking: application context appended
        # mid-sentence can make it break off, so everything but a delegation's own
        # answer waits for it.
        agent_quiet = asyncio.Event()
        agent_quiet.set()
        # Set while no delegation awaits its answer or the turn that speaks it, and while
        # the participant is not talking: with `agent_quiet`, who has the floor.
        no_delegation = asyncio.Event()
        no_delegation.set()
        participant_quiet = asyncio.Event()
        participant_quiet.set()
        changes = SessionChanges(database, interview_session_id=subject.interview_session_id)
        # The agent's latest words at each fragment, for the cursor to follow as it speaks.
        spoken: asyncio.Queue[str] = asyncio.Queue()
        window = SpokenWindow()
        moments = GestureMoments()
        listener = asyncio.create_task(changes.run())
        await changes.listening()
        background = (
            listener,
            asyncio.create_task(
                apply_controls(
                    database,
                    changes,
                    client_id=subject.client_id,
                    interview_session_id=subject.interview_session_id,
                    sideband=sideband,
                )
            ),
            asyncio.create_task(
                announce_ready_preview(
                    database,
                    changes,
                    subject=_delegation_subject(subject),
                    delegations=delegations,
                    sideband=sideband,
                    agent_quiet=agent_quiet,
                    moments=moments,
                )
            ),
            asyncio.create_task(
                announce_gate_failure(
                    database,
                    changes,
                    product_signal_id=subject.product_signal_id,
                    sideband=sideband,
                    agent_quiet=agent_quiet,
                )
            ),
            asyncio.create_task(
                begin_validation_when_ready(
                    database, changes, interview_session_id=subject.interview_session_id
                )
            ),
            asyncio.create_task(
                prompt_open_preview(
                    database,
                    changes,
                    interview_session_id=subject.interview_session_id,
                    sideband=sideband,
                    agent_quiet=agent_quiet,
                )
            ),
            asyncio.create_task(
                feed_activity(
                    database,
                    changes,
                    _delegation_subject(subject),
                    sideband,
                    agent_quiet,
                    ShownChecks(
                        database, subject=_delegation_subject(subject), delegations=delegations
                    ),
                    Acknowledgements(
                        subject=_delegation_subject(subject),
                        delegations=delegations,
                        moments=moments,
                        floor=Floor(
                            agent_quiet=agent_quiet,
                            no_delegation=no_delegation,
                            participant_quiet=participant_quiet,
                        ),
                    ),
                )
            ),
            asyncio.create_task(
                show_what_is_said(
                    spoken,
                    delegations=delegations,
                    subject=_delegation_subject(subject),
                    moments=moments,
                )
            ),
        )
        for task in background:
            task.add_done_callback(_report_failure)
        turns = TurnDetector()
        speech = AgentSpeech()
        pending: set[asyncio.Task[None]] = set()
        try:
            async for event in sideband.events():
                if isinstance(event, SessionStarted | SessionUpdated):
                    _require_client_delegation(event)
                    continue
                if isinstance(event, TranscriptFragment):
                    await _record_fragment(database, subject=subject, event=event)
                    counters.fragments += 1
                    if event.speaker() == "agent":
                        await speech.said()
                        latest = window.add(event.delta)
                        if latest is not None:
                            spoken.put_nowait(latest)
                    continue
                if isinstance(event, OutputAudioSegment):
                    playback.append(
                        PlaybackSegment(
                            start_ms=event.start_ms,
                            end_ms=event.end_ms,
                            audio_byte_length=event.audio_byte_length,
                        )
                    )
                    counters.playback_segments += 1
                    turn = turns.agent_audio(
                        level=event.level, byte_length=event.audio_byte_length
                    )
                    if turn is not None:
                        if turns.agent_speaking:
                            agent_quiet.clear()
                        else:
                            agent_quiet.set()
                            if not pending:
                                no_delegation.set()
                            moments.agent_stopped()
                            window.end_turn()
                        await _show_turn(
                            database,
                            subject=subject,
                            # Quiet while a delegation is still being answered is the
                            # agent waiting on its answer, not listening.
                            state=(
                                CharacterState.THINKING
                                if turn is CharacterState.LISTENING and pending
                                else turn
                            ),
                        )
                    if len(playback) >= _PLAYBACK_BATCH_SEGMENTS:
                        await _record_playback(database, subject=subject, segments=playback)
                        playback.clear()
                    continue
                if isinstance(event, InputAudioReflected):
                    # Reflected caller audio proves only receipt, so it is not evidence;
                    # its loudness is what shows the participant has started talking.
                    turn = turns.participant_audio(
                        level=event.level, byte_length=event.audio_byte_length
                    )
                    if turns.participant_speaking:
                        participant_quiet.clear()
                    else:
                        participant_quiet.set()
                    if turn is not None:
                        await _show_turn(database, subject=subject, state=turn)
                    continue
                if isinstance(event, DelegationCreated):
                    if event.target != _CLIENT_DELEGATION:
                        raise VoiceSessionMisconfigured(
                            "this session was configured for client delegation but "
                            f"the provider delegated to {event.target!r}"
                        )
                    counters.delegations += 1
                    task = asyncio.create_task(
                        delegations.handle(
                            subject=_delegation_subject(subject),
                            sideband=sideband,
                            event=event,
                            speech=speech,
                        )
                    )
                    pending.add(task)
                    no_delegation.clear()
                    task.add_done_callback(pending.discard)
                    task.add_done_callback(_report_failure)
                    continue
                if isinstance(event, ContextAppendAcknowledged):
                    _log.info(
                        "voice_context_append_accepted",
                        interview_session_id=str(subject.interview_session_id),
                        provider_event=event.type,
                    )
                    continue
                if isinstance(event, InputAudioMuteChanged):
                    _log.info(
                        "voice_input_mute_confirmed",
                        interview_session_id=str(subject.interview_session_id),
                        muted=event.muted(),
                    )
                    continue
                if isinstance(event, SessionClosed):
                    counters.close_reason = event.reason
                    break
                if isinstance(event, SessionError):
                    _log.warning(
                        "voice_session_error",
                        interview_session_id=str(subject.interview_session_id),
                        error_type=event.error_type,
                        code=event.code,
                        message=event.message,
                        param=event.param,
                        client_event_id=event.client_event_id,
                    )
                    continue
                if isinstance(event, SessionInfo):
                    _log.info(
                        "voice_session_info",
                        interview_session_id=str(subject.interview_session_id),
                        code=event.code,
                    )
                    continue
                if isinstance(event, ResponseDelegationEnvelope):
                    _refuse_responses_delegation()
                if isinstance(event, UnrecognizedEvent):
                    _log.info(
                        "voice_event_unrecognized",
                        interview_session_id=str(subject.interview_session_id),
                        provider_type=event.provider_type,
                    )
        finally:
            # Stopped and awaited, so nothing this run started still touches the
            # session once it returns.
            stopping = (*background, *pending)
            for task in stopping:
                task.cancel()
            await asyncio.gather(*stopping, return_exceptions=True)
        if playback:
            await _record_playback(database, subject=subject, segments=playback)
    await _mark_detached(database, subject=subject, reason=counters.close_reason)
    _log.info(
        "voice_sideband_drained",
        client_id=str(subject.client_id),
        interview_session_id=str(subject.interview_session_id),
        voice_connection_id=str(subject.voice_connection_id),
        fragments=counters.fragments,
        playback_segments=counters.playback_segments,
        delegations=counters.delegations,
        close_reason=counters.close_reason,
    )
    return SidebandSummary(
        fragments=counters.fragments,
        playback_segments=counters.playback_segments,
        delegations=counters.delegations,
        close_reason=counters.close_reason,
    )


def _delegation_subject(subject: SidebandSubject) -> DelegationSubject:
    """The delegation-facing identity of the connection this sideband observes."""
    return DelegationSubject(
        client_id=subject.client_id,
        product_signal_id=subject.product_signal_id,
        interview_session_id=subject.interview_session_id,
        voice_connection_id=subject.voice_connection_id,
    )


def _report_failure(task: "asyncio.Task[None]") -> None:
    """Log a background sideband task that ended with an error rather than by cancellation."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        coroutine = task.get_coro()
        _log.error(
            "voice_sideband_task_failed",
            # An eagerly finished task keeps no coroutine; its name still identifies it.
            task=task.get_name() if coroutine is None else coroutine.__qualname__,
            error_type=type(error).__name__,
            detail=str(error),
            causes=[f"{type(cause).__name__}: {cause}" for cause in _causes(error)],
            exc_info=error,
        )


def _causes(error: BaseException) -> list[BaseException]:
    """The errors an exception group gathered, however deeply nested, or the error itself."""
    if isinstance(error, BaseExceptionGroup):
        return [cause for inner in error.exceptions for cause in _causes(inner)]
    return [error]


class _Counters:
    """Mutable run counters, kept out of the frozen summary."""

    def __init__(self) -> None:
        self.fragments = 0
        self.playback_segments = 0
        self.delegations = 0
        self.close_reason: str | None = None


def _refuse_responses_delegation() -> None:
    """Refuse a session the provider did not configure for client delegation."""
    raise VoiceSessionMisconfigured(
        "the provider sent a Responses delegation envelope, so this session was "
        "not configured for client delegation"
    )


def _require_client_delegation(event: SessionStarted | SessionUpdated) -> None:
    """Refuse a session the provider did not configure for client delegation."""
    if event.delegation_type is not None and event.delegation_type != _CLIENT_DELEGATION:
        raise VoiceSessionMisconfigured(
            "the provider session reports delegation "
            f"{event.delegation_type!r} rather than client delegation"
        )


async def _record_fragment(
    database: Database, *, subject: SidebandSubject, event: TranscriptFragment
) -> None:
    """Persist one transcript fragment; the participant answering starts discovery."""
    async with database.transaction() as conn:
        await record_transcript_fragment(
            conn,
            client_id=subject.client_id,
            fragment=StoredFragment(
                interview_session_id=subject.interview_session_id,
                voice_connection_id=subject.voice_connection_id,
                voice_run_id=subject.voice_run_id,
                provider_session_ref=subject.provider_session_ref,
                speaker=event.speaker(),
                provider_event_type=event.type,
                start_ms=event.start_ms,
                end_ms=event.end_ms,
                text=event.delta,
            ),
            provenance={
                "provider": "openai_gpt_live",
                "provider_session_ref": subject.provider_session_ref,
                "voice_run_id": str(subject.voice_run_id),
                "recorded_at": datetime.now(tz=UTC).isoformat(),
            },
        )
        # The participant answering the greeting is what starts discovery; the
        # advance republishes the projection. Who is talking comes from the audio.
        if event.speaker() == "participant":
            await advance_phase_on_connection(
                conn,
                interview_session_id=subject.interview_session_id,
                phase=InterviewPhase.DISCOVERY,
            )


async def _show_turn(
    database: Database, *, subject: SidebandSubject, state: CharacterState
) -> None:
    """Show a change of who is talking, republishing only when the character moved."""
    async with database.transaction() as conn:
        if await change_character(
            conn, interview_session_id=subject.interview_session_id, state=state
        ):
            await publish_projection_on_connection(
                conn, interview_session_id=subject.interview_session_id
            )


async def _record_playback(
    database: Database, *, subject: SidebandSubject, segments: list[PlaybackSegment]
) -> None:
    """Persist one batch of the generated-audio evidence the transport exposed."""
    async with database.transaction() as conn:
        await record_playback_segments(
            conn,
            client_id=subject.client_id,
            interview_session_id=subject.interview_session_id,
            voice_connection_id=subject.voice_connection_id,
            segments=segments,
            delivery=_GENERATED,
        )


async def _mark_attached(database: Database, *, subject: SidebandSubject) -> None:
    """Record that the backend sideband is attached, completing conversational readiness."""
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


async def _awaiting_greeting(database: Database, *, interview_session_id: UUID) -> bool:
    """Whether the session is still in its greeting phase, so nobody has spoken yet.

    A connection that replaces one the participant already spoke on is past it, and
    the agent picks up the conversation instead of introducing itself again.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select phase from public.interview_sessions where id = %s",
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError("interview_sessions", interview_session_id)
    return as_str(row, "phase") == InterviewPhase.GREETING.value


async def _mark_detached(
    database: Database, *, subject: SidebandSubject, reason: str | None
) -> None:
    """Withdraw the sideband half of readiness when the provider session ends."""
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
                set state = %s, closed_at = now(), revision = revision + 1,
                    updated_at = now()
                where id = %s and state <> %s
                """,
                (
                    VoiceConnectionState.CLOSED.value,
                    subject.voice_connection_id,
                    VoiceConnectionState.CLOSED.value,
                ),
            )
            await cur.execute(
                "select state from public.interview_sessions where id = %s",
                (subject.interview_session_id,),
            )
            session = await cur.fetchone()
        if session is not None and as_str(session, "state") != "closed":
            await set_character(
                conn,
                interview_session_id=subject.interview_session_id,
                state=CharacterState.RECONNECTING,
            )
        await publish_projection_on_connection(
            conn, interview_session_id=subject.interview_session_id
        )
    _log.info(
        "voice_sideband_detached",
        interview_session_id=str(subject.interview_session_id),
        voice_connection_id=str(subject.voice_connection_id),
        close_reason=reason,
    )
