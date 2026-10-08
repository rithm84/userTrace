"""The voice agent: one connection's call, from joining it until it ends.

The agent joins the connection's private Daily room from the worker and runs the
conversation as a Pipecat pipeline: Deepgram Flux hears the participant, Claude
writes the agent's words with gesture marks inline, and ElevenLabs speaks them
(`app.interview.voice.pipeline` holds the pieces uTrace adds). The model calls the
application's tools itself (`app.interview.voice.tools`), and the application adds
what the agent should know or say to its conversation through `AgentChannel`: the
participant's preview activity, the change's progress, and the cues in
`app.interview.voice.cues`.

The run records the conversation as transcript fragments, shows who has the floor
through the character, and keeps conversational readiness. It runs as a Job, so a
worker restart rejoins the call from durable state. When the participant leaves,
the call ends and the bounded reconnect window opens.

Meeting tokens and the provider credentials never enter a log or the model's
context.
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    FunctionCallResultProperties,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext, LLMContextMessage
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.aggregators.llm_text_processor import LLMTextProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIFunctionCallReportLevel, RTVIObserverParams
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.turns.user_turn_strategies import EagerUserTurnStrategies
from pipecat.workers.runner import WorkerRunner
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    InterviewPhase,
    VoiceConnectionState,
)

from app.clients.overview import load_active_overview
from app.connectors.daily import DailyRooms
from app.context.bundles import assemble_bundle
from app.context.purposes import BundlePurpose
from app.context.sources import product_overview_items, recent_conversation_items
from app.db.kernel import append_fact
from app.db.pool import Database
from app.db.rows import as_bool, as_str, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, StreamRef
from app.errors import AggregateNotFoundError
from app.execution.jobs import ClaimedJob
from app.ids import uuid7
from app.interview.character import CharacterState, change_character, set_character
from app.interview.evidence import TranscriptFragment, record_transcript_fragment
from app.interview.observations import recent_activity
from app.interview.ports import RegisteredTarget
from app.interview.projection import advance_phase_on_connection, publish_projection_on_connection
from app.interview.readiness import load_readiness, record_conversational_facts
from app.interview.reconnect import reconnect_window_job
from app.interview.settings import InterviewSettings, VoiceSettings
from app.interview.voice.acknowledgements import Acknowledgements
from app.interview.voice.activity_feed import feed_activity
from app.interview.voice.channel import Floor, VoiceSubject
from app.interview.voice.checks_shown import ShownChecks
from app.interview.voice.cues import (
    agreed_change,
    announce_gate_outcome,
    announce_ready_preview,
    announce_validation_outcome,
    apply_controls,
    begin_validation_when_ready,
    prompt_open_preview,
)
from app.interview.voice.instructions import session_instructions
from app.interview.voice.judge import VoiceJudge
from app.interview.voice.pipeline import (
    BargeInStrategy,
    ClauseStartTTS,
    FluxSTT,
    MicGate,
    NearEnd,
    PlaybackTap,
    Screen,
    ScreenState,
    ScreenView,
    SpokenWords,
    WordsAndGestures,
    strip_marks,
)
from app.interview.voice.pointing import circle_step, gesture_at
from app.interview.voice.session_changes import SessionChanges
from app.interview.voice.tools import READ_TOOLS, TOOL_DEFINITIONS, ToolContext, execute_tool
from app.logging import get_logger

__all__ = ["AgentSubject", "record_call_ended", "run_voice_agent", "subject_from_job"]

_log = get_logger(__name__)
_AGENT_ACTOR: Final[Actor] = Actor(kind=ActorKind.SERVICE, ref="interview.voice.agent")

#: Spoken once the participant has joined a call nobody has spoken on yet: the agent
#: opens the conversation.
_GREETING_CUE: Final[str] = (
    "The call has just connected and the person is listening. Open the conversation "
    "now: greet them, say who you are and who you are helping, explain that you will "
    "ask short questions while they reproduce the workflow, that you can see structured "
    "product events from this session, and that you may point out fields or buttons. "
    "Then ask what they would like help with."
)
#: Follows the words of a turn the participant cut into, which they heard.
_CUT_IN_NOTE: Final[str] = (
    'They cut in while you were speaking, and heard only this much: "{heard}" '
    "Never say it again or start that turn over: answer what they say next, adding "
    "only what they still need from the rest in one short sentence at most."
)
#: How long the agent waits in the room for the participant to join before the call
#: is treated as lost.
_JOIN_WAIT_SECONDS: Final[float] = 90.0
_PURPOSE: Final = BundlePurpose.VOICE_TOOLS
_OBSERVATION_WINDOW_SECONDS: Final[int] = 300
_SAMPLE_RATE_IN: Final[int] = 16000
_SAMPLE_RATE_OUT: Final[int] = 24000
_PARTICIPANT_EVENT: Final[str] = "flux.end_of_turn"
_AGENT_EVENT: Final[str] = "agent.word_played"


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

    def voice(self) -> VoiceSubject:
        return VoiceSubject(
            client_id=self.client_id,
            product_signal_id=self.product_signal_id,
            interview_session_id=self.interview_session_id,
            voice_connection_id=self.voice_connection_id,
        )


async def subject_from_job(database: Database, claimed: ClaimedJob) -> AgentSubject:
    """Read the Job's frozen input and resolve the case it belongs to."""
    session_id = UUID(str(claimed.input["interview_session_id"]))
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select product_signal_id from public.interview_sessions where id = %s",
            (session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError("interview_sessions", session_id)
    return AgentSubject(
        client_id=UUID(str(claimed.input["client_id"])),
        interview_session_id=session_id,
        product_signal_id=as_uuid(row, "product_signal_id"),
        voice_connection_id=UUID(str(claimed.input["voice_connection_id"])),
        voice_run_id=UUID(str(claimed.input["voice_run_id"])),
        provider_session_ref=str(claimed.input["provider_session_ref"]),
        room_url=str(claimed.input["room_url"]),
        room_expires_at=datetime.fromisoformat(str(claimed.input["room_expires_at"])),
    )


class _Channel:
    """`AgentChannel` over the running pipeline."""

    def __init__(self, task: PipelineWorker, mic: MicGate, floor: Floor) -> None:
        self._task = task
        self._mic = mic
        self._floor = floor

    async def say(self, content: str) -> None:
        # The agent holds the floor from this request, so a cue that follows before
        # its answer starts waits for it instead of asking for a second answer.
        self._floor.agent_quiet.clear()
        await self._append(content, speak=True)

    async def note(self, content: str) -> None:
        await self._append(content, speak=False)

    async def set_input_muted(self, *, muted: bool) -> None:
        self._mic.muted = muted

    async def _append(self, content: str, *, speak: bool) -> None:
        await self._task.queue_frames(
            [
                LLMMessagesAppendFrame(
                    messages=[{"role": "user", "content": f"<application>{content}</application>"}],
                    run_llm=speak,
                )
            ]
        )


class _Answering(FrameProcessor):
    """Holds the floor for the agent while the model answers.

    An answer with words keeps it until their audio stops playing (`_AgentFloor`);
    one without words, or one the participant interrupts, releases it at once.
    """

    def __init__(self, *, floor: Floor) -> None:
        super().__init__()
        self._floor = floor
        self._has_words = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            self._has_words = False
            self._floor.agent_quiet.clear()
        elif isinstance(frame, LLMTextFrame) and strip_marks(frame.text):
            self._has_words = True
        elif isinstance(frame, InterruptionFrame) or (
            isinstance(frame, LLMFullResponseEndFrame) and not self._has_words
        ):
            self._floor.agent_quiet.set()
        await self.push_frame(frame, direction)


class _AgentFloor(FrameProcessor):
    """Follows the agent's speech as it plays, for the floor and the character."""

    def __init__(self, *, floor: Floor, show: "_Character") -> None:
        super().__init__()
        self._floor = floor
        self._show = show

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, BotStartedSpeakingFrame):
                self._floor.agent_quiet.clear()
                self.create_task(self._show(CharacterState.SPEAKING), "character:speaking")
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self._floor.agent_quiet.set()
                state = (
                    CharacterState.LISTENING
                    if self._floor.no_tool_running.is_set()
                    else CharacterState.THINKING
                )
                self.create_task(self._show(state), "character:quiet")
        await self.push_frame(frame, direction)


class _Character:
    """Commits character changes in the order they were made."""

    def __init__(self, database: Database, subject: AgentSubject) -> None:
        self._database = database
        self._subject = subject
        self._lock = asyncio.Lock()

    async def __call__(self, state: CharacterState) -> None:
        async with self._lock, self._database.transaction() as conn:
            if await change_character(
                conn, interview_session_id=self._subject.interview_session_id, state=state
            ):
                await publish_projection_on_connection(
                    conn, interview_session_id=self._subject.interview_session_id
                )


async def run_voice_agent(
    database: Database,
    *,
    rooms: DailyRooms,
    judge: VoiceJudge,
    voice: VoiceSettings,
    interview: InterviewSettings,
    subject: AgentSubject,
) -> None:
    """Join the call and hold the conversation until the participant leaves."""
    token = await rooms.meeting_token(
        room_name=subject.provider_session_ref,
        expires_at=subject.room_expires_at,
        user_name=interview.agent_display_name,
        owner=True,
    )
    floor = Floor(
        agent_quiet=asyncio.Event(),
        no_tool_running=asyncio.Event(),
        participant_quiet=asyncio.Event(),
    )
    floor.agent_quiet.set()
    floor.no_tool_running.set()
    floor.participant_quiet.set()
    show = _Character(database, subject)
    spoken = SpokenWords()
    near_end = NearEnd()
    view = ScreenView()
    mic = MicGate(near_end=near_end)
    call_started = asyncio.get_running_loop().time()

    transport = DailyTransport(
        subject.room_url,
        token,
        interview.agent_display_name,
        DailyParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            camera_out_enabled=False,
            microphone_out_enabled=True,
        ),
    )
    stt = FluxSTT(
        api_key=voice.deepgram_api_key,
        enable_eager_end_of_turn=True,
        settings=FluxSTT.Settings(
            model="flux-general-en",
            eot_threshold=voice.end_of_turn_threshold,
            eager_eot_threshold=voice.eager_end_of_turn_threshold,
            eot_timeout_ms=5000,
        ),
    )
    llm = AnthropicLLMService(
        api_key=voice.anthropic_api_key,
        settings=AnthropicLLMService.Settings(
            model=voice.text_model,
            system_instruction=await session_instructions(
                database, interview_session_id=subject.interview_session_id
            ),
            max_tokens=2000,
            enable_prompt_caching=True,
            # Measured fastest at full accuracy: thinking only between tool calls, low effort.
            extra={"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}},
        ),
    )
    tts = ClauseStartTTS(
        api_key=voice.elevenlabs_api_key,
        sample_rate=_SAMPLE_RATE_OUT,
        settings=ClauseStartTTS.Settings(model=voice.speech_model, voice=voice.voice_id),
        skip_aggregator_types=[WordsAndGestures.GESTURE],
    )

    async def run_tool(params: FunctionCallParams) -> None:
        floor.no_tool_running.clear()
        await show(CharacterState.THINKING)
        try:
            result = await execute_tool(
                await _tool_context(database, interview=interview, subject=subject),
                name=params.function_name,
                arguments=params.arguments,
            )
        finally:
            floor.no_tool_running.set()
        refused = any(key in json.loads(result) for key in ("error", "refused", "unavailable"))
        _log.info(
            "voice_tool_ran",
            interview_session_id=str(subject.interview_session_id),
            tool=params.function_name,
            refused=refused,
        )
        await params.result_callback(
            result,
            properties=FunctionCallResultProperties(
                run_llm=refused or params.function_name in READ_TOOLS
            ),
        )

    for tool in TOOL_DEFINITIONS:
        llm.register_function(tool.name, run_tool, cancel_on_interruption=False)
    context = LLMContext(
        messages=await _conversation_so_far(database, interview=interview, subject=subject),
        tools=ToolsSchema(
            standard_tools=[
                FunctionSchema(
                    name=tool.name,
                    description=tool.description,
                    properties=dict(tool.parameters["properties"]),  # type: ignore[call-overload]
                    required=list(tool.parameters["required"]),  # type: ignore[call-overload]
                )
                for tool in TOOL_DEFINITIONS
            ]
        ),
    )
    strategies = EagerUserTurnStrategies()
    strategies.start = [BargeInStrategy(spoken=spoken, near_end=near_end)]
    aggregators = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(user_turn_strategies=strategies)
    )

    async def read_screen() -> Screen:
        return Screen(
            targets=await judge.current_targets(subject=subject.voice()),
            agreed=await agreed_change(database, interview_session_id=subject.interview_session_id),
            progress=await _implementation_progress(database, subject=subject),
        )

    async def circle(target: RegisteredTarget) -> None:
        shown = await gesture_at(
            database,
            client_id=subject.client_id,
            interview_session_id=subject.interview_session_id,
            steps=[circle_step(target)],
            deadline_seconds=interview.visual_command_deadline_seconds,
        )
        _log.info(
            "voice_gesture_dispatched",
            interview_session_id=str(subject.interview_session_id),
            target_id=target.target_id,
            outcome=dict(shown),
        )

    # When the participant's current turn began, on the call's clock.
    turn_started_ms = {"participant": 0}

    def call_ms(at: float) -> int:
        return max(0, round((at - call_started) * 1000))

    def now() -> float:
        return asyncio.get_running_loop().time()

    async def heard(word: str) -> None:
        # Fragments are joined as they are, so each word carries its own space.
        at = call_ms(now())
        await _record_fragment(
            database,
            subject=subject,
            speaker="agent",
            event_type=_AGENT_EVENT,
            start_ms=at,
            end_ms=at,
            text=f" {strip_marks(word)}",
        )

    pipeline = Pipeline(
        [
            transport.input(),
            mic,
            stt,
            aggregators.user(),
            ScreenState(read=read_screen, view=view),
            llm,
            _Answering(floor=floor),
            LLMTextProcessor(text_aggregator=WordsAndGestures()),
            tts,
            transport.output(),
            PlaybackTap(spoken=spoken, view=view, gesture=circle, heard=heard),
            _AgentFloor(floor=floor, show=show),
            aggregators.assistant(),
        ]
    )
    task = PipelineWorker(
        pipeline,
        # The session tab shows what was said; the model's raw text (gesture marks
        # included), its context, tool calls, metrics, and logs stay on the backend.
        rtvi_observer_params=RTVIObserverParams(
            bot_llm_enabled=False,
            user_llm_enabled=False,
            metrics_enabled=False,
            system_logs_enabled=False,
            function_call_report_level={"*": RTVIFunctionCallReportLevel.DISABLED},
            skip_aggregator_types=[WordsAndGestures.GESTURE],
        ),
        params=PipelineParams(
            audio_in_sample_rate=_SAMPLE_RATE_IN, audio_out_sample_rate=_SAMPLE_RATE_OUT
        ),
    )
    agent = _Channel(task, mic, floor)
    changes = SessionChanges(database, interview_session_id=subject.interview_session_id)
    background: list[asyncio.Task[None]] = []
    joined = asyncio.Event()
    close_reason: list[str] = []

    @aggregators.assistant().event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(
        _aggregator: object, message: AssistantTurnStoppedMessage
    ) -> None:
        if message.interrupted and message.content.strip():
            context.add_message(
                {
                    "role": "user",
                    "content": "<application>"
                    + _CUT_IN_NOTE.format(heard=message.content.strip())
                    + "</application>",
                }
            )

    @aggregators.user().event_handler("on_user_turn_started")
    async def on_user_turn_started(_aggregator: object, _strategy: object) -> None:
        turn_started_ms["participant"] = call_ms(now())
        floor.participant_quiet.clear()
        await show(CharacterState.LISTENING)

    @aggregators.user().event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(_aggregator: object, _strategy: object, message: object) -> None:
        floor.participant_quiet.set()
        text = getattr(message, "content", None)
        if isinstance(text, str) and text.strip():
            await _record_fragment(
                database,
                subject=subject,
                speaker="participant",
                event_type=_PARTICIPANT_EVENT,
                start_ms=turn_started_ms["participant"],
                end_ms=call_ms(now()),
                text=text.strip(),
            )

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(_transport: object, _participant: object) -> None:
        joined.set()
        await _mark_attached(database, subject=subject)
        background.extend(
            _start_background(
                database, changes, subject=subject, agent=agent, judge=judge, floor=floor
            )
        )
        if await _awaiting_greeting(database, interview_session_id=subject.interview_session_id):
            await agent.say(_GREETING_CUE)

    @transport.event_handler("on_participant_left")
    async def on_participant_left(_transport: object, _participant: object, reason: object) -> None:
        close_reason.append(f"participant_left:{reason}")
        await task.cancel()

    @transport.event_handler("on_error")
    async def on_error(_transport: object, error: object) -> None:
        _log.warning(
            "voice_call_error",
            interview_session_id=str(subject.interview_session_id),
            error=str(error),
        )

    async def give_up_without_participant() -> None:
        try:
            await asyncio.wait_for(joined.wait(), timeout=_JOIN_WAIT_SECONDS)
        except TimeoutError:
            close_reason.append("participant_never_joined")
            await task.cancel()

    waiting = asyncio.create_task(give_up_without_participant())
    listener = asyncio.create_task(changes.run())
    await changes.listening()
    try:
        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(task)
        await runner.run()
    finally:
        stopping = (waiting, listener, *background)
        for running in stopping:
            running.cancel()
        await asyncio.gather(*stopping, return_exceptions=True)
        await record_call_ended(
            database, subject=subject, reason=close_reason[0] if close_reason else "call_ended"
        )
    _log.info(
        "voice_agent_finished",
        client_id=str(subject.client_id),
        interview_session_id=str(subject.interview_session_id),
        voice_connection_id=str(subject.voice_connection_id),
        close_reason=close_reason[0] if close_reason else "call_ended",
    )


def _start_background(
    database: Database,
    changes: SessionChanges,
    *,
    subject: AgentSubject,
    agent: _Channel,
    judge: VoiceJudge,
    floor: Floor,
) -> list[asyncio.Task[None]]:
    """The tasks that tell the agent what changes in the session while the call runs."""
    voice_subject = subject.voice()
    tasks = [
        apply_controls(
            database,
            changes,
            client_id=subject.client_id,
            interview_session_id=subject.interview_session_id,
            agent=agent,
        ),
        announce_ready_preview(
            database,
            changes,
            subject=voice_subject,
            judge=judge,
            agent=agent,
            agent_quiet=floor.agent_quiet,
        ),
        announce_gate_outcome(
            database,
            changes,
            product_signal_id=subject.product_signal_id,
            agent=agent,
            agent_quiet=floor.agent_quiet,
        ),
        announce_validation_outcome(
            database,
            changes,
            client_id=subject.client_id,
            product_signal_id=subject.product_signal_id,
            agent=agent,
            floor=floor,
        ),
        begin_validation_when_ready(
            database, changes, interview_session_id=subject.interview_session_id
        ),
        prompt_open_preview(
            database,
            changes,
            interview_session_id=subject.interview_session_id,
            agent=agent,
            agent_quiet=floor.agent_quiet,
        ),
        feed_activity(
            database,
            changes,
            voice_subject,
            agent,
            floor.agent_quiet,
            ShownChecks(database, subject=voice_subject, judge=judge),
            Acknowledgements(subject=voice_subject, judge=judge, floor=floor),
        ),
    ]
    started = [asyncio.create_task(coroutine) for coroutine in tasks]
    for task in started:
        task.add_done_callback(_report_failure)
    return started


def _report_failure(task: "asyncio.Task[None]") -> None:
    """Log a background task that ended with an error rather than by cancellation."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        coroutine = task.get_coro()
        _log.error(
            "voice_agent_task_failed",
            task=task.get_name() if coroutine is None else coroutine.__qualname__,
            error_type=type(error).__name__,
            detail=str(error),
            exc_info=error,
        )


async def _tool_context(
    database: Database, *, interview: InterviewSettings, subject: AgentSubject
) -> ToolContext:
    """The context one tool call runs against: the conversation as recorded right now."""
    readiness = await load_readiness(database, interview_session_id=subject.interview_session_id)
    observations = (
        await recent_activity(
            database,
            client_id=subject.client_id,
            interview_session_id=subject.interview_session_id,
            since=datetime.now(tz=UTC) - timedelta(seconds=_OBSERVATION_WINDOW_SECONDS),
            limit=interview.selected_observation_limit,
        )
        if readiness.observation
        else ()
    )
    overview = await load_active_overview(database, client_id=subject.client_id)
    bundle = await assemble_bundle(
        database,
        client_id=subject.client_id,
        product_signal_id=subject.product_signal_id,
        interview_session_id=subject.interview_session_id,
        purpose=_PURPOSE,
        budget_items=interview.context_budget_items,
        observations=observations,
        extra_items=product_overview_items(overview),
        recent_fragment_limit=interview.recent_conversation_fragments,
        prior_command_limit=interview.selected_observation_limit,
    )
    return ToolContext(
        database=database,
        client_id=subject.client_id,
        product_signal_id=subject.product_signal_id,
        interview_session_id=subject.interview_session_id,
        bundle=bundle,
        observation_window_seconds=_OBSERVATION_WINDOW_SECONDS,
    )


async def _conversation_so_far(
    database: Database, *, interview: InterviewSettings, subject: AgentSubject
) -> list[LLMContextMessage]:
    """What was said earlier in this session, as the agent's conversation so far.

    A call that replaces a lost one starts from the conversation on record, so the
    agent picks up where it left off instead of starting over.
    """
    async with database.connection() as conn:
        turns = await recent_conversation_items(
            conn,
            interview_session_id=subject.interview_session_id,
            limit=interview.recent_conversation_fragments,
        )
    messages: list[LLMContextMessage] = []
    for turn in turns:
        text = str(turn.body["text"]).strip()
        if text and turn.body["speaker"] == "participant":
            messages.append({"role": "user", "content": text})
        elif text:
            messages.append({"role": "assistant", "content": text})
    return messages


async def _implementation_progress(database: Database, *, subject: AgentSubject) -> str | None:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select implementation_progress from public.interview_sessions where id = %s",
            (subject.interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError("interview_sessions", subject.interview_session_id)
    progress = as_str(row, "implementation_progress")
    return None if progress == "not_started" else progress


async def _record_fragment(
    database: Database,
    *,
    subject: AgentSubject,
    speaker: str,
    event_type: str,
    start_ms: int,
    end_ms: int,
    text: str,
) -> None:
    """Persist one finished turn's words; the participant answering starts discovery."""
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


async def _mark_attached(database: Database, *, subject: AgentSubject) -> None:
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


async def _awaiting_greeting(database: Database, *, interview_session_id: UUID) -> bool:
    """Whether nobody has spoken in the session yet.

    A connection that replaces one the participant already spoke on is past the
    greeting, and the agent picks up the conversation instead of introducing itself.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select phase from public.interview_sessions where id = %s", (interview_session_id,)
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError("interview_sessions", interview_session_id)
    return as_str(row, "phase") == InterviewPhase.GREETING.value


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
