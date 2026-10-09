"""The voice agent: one connection's call, from joining it until it ends.

The agent joins the connection's private Daily room from its session on Pipecat
Cloud (`app.interview.voice.cloud_session`) and runs the
conversation as a Pipecat pipeline: Deepgram Flux hears the participant, GPT-6.1
Sol writes the agent's words with gesture marks inline, and ElevenLabs speaks them
(`app.interview.voice.pipeline` holds the pieces uTrace adds). The model calls the
application's tools itself (`app.interview.voice.tools`), and the application adds
what the agent should know or say to its conversation through `AgentChannel`: the
participant's preview activity, the change's progress, and the cues in
`app.interview.voice.cues`. Who holds the floor, and whether the agent speaks again
once a tool's result is in, is `app.interview.voice.turn_taking`'s.

The run records the conversation as transcript fragments, shows who has the floor
through the character, and keeps conversational readiness
(`app.interview.voice.call_record`). It runs as a Job, so a session that fails
rejoins the call from durable state. A participant who leaves, such as on a reload, has 30
seconds to come back to the same call; after that the call ends and the bounded
reconnect window opens.

Meeting tokens and the provider credentials never enter a log or the model's
context.
"""

import asyncio
import json
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import InterruptionWorkerFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.aggregators.llm_text_processor import LLMTextProcessor
from pipecat.processors.frameworks.rtvi import RTVIFunctionCallReportLevel, RTVIObserverParams
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.responses.llm import OpenAIResponsesLLMService
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.turns.user_turn_strategies import EagerUserTurnStrategies
from pipecat.workers.runner import WorkerRunner
from utrace_contracts.domain.lifecycle import InterviewPhase

from app.clients.overview import load_active_overview
from app.connectors.daily import DailyRooms
from app.context.bundles import assemble_bundle
from app.context.purposes import BundlePurpose
from app.context.sources import product_overview_items
from app.db.pool import Database
from app.db.rows import as_bool, as_str
from app.errors import AggregateNotFoundError
from app.interview.character import CharacterState, change_character
from app.interview.observations import recent_activity
from app.interview.ports import RegisteredTarget
from app.interview.projection import publish_projection_on_connection
from app.interview.readiness import load_readiness
from app.interview.settings import InterviewSettings, VoiceSettings
from app.interview.voice.acknowledgements import Acknowledgements
from app.interview.voice.activity_feed import feed_activity
from app.interview.voice.call_record import (
    AgentSubject,
    conversation_so_far,
    mark_attached,
    record_call_ended,
    record_fragment,
)
from app.interview.voice.channel import Floor
from app.interview.voice.checks_shown import ShownChecks
from app.interview.voice.cues import (
    agreed_change,
    agreed_checks,
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
    OneSpeculation,
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
from app.interview.voice.tools import (
    TOOL_DEFINITIONS,
    ToolContext,
    execute_tool,
    progress_in_words,
    refused,
)
from app.interview.voice.turn_taking import AgentFloor, AgentTurn, Answering, PipelineChannel
from app.logging import get_logger

__all__ = ["run_voice_agent"]

_log = get_logger(__name__)

#: Spoken once the participant has joined a call nobody has spoken on yet: the agent
#: opens the conversation.
_GREETING_CUE: Final[str] = (
    "The call has just connected and the person is listening. Open the conversation "
    "now, in two short sentences: greet them and say who you are and who you are "
    "helping, that you will ask a few short questions as they show you their workflow, "
    "that you can see what they do in this preview, and that you may point things out "
    "on their screen; then ask what they would like help with."
)
#: Spoken when the participant joins a call that reconnected before they answered the
#: greeting: they have heard who the agent is.
_REGREET_CUE: Final[str] = (
    "The call reconnected before they answered you. Without introducing yourself "
    "again, ask them in a few words what they would like help with."
)
#: Spoken when the participant comes back to a conversation already under way.
_RESUMED_CUE: Final[str] = (
    "The call dropped for a moment and they are back. In one short sentence, welcome "
    "them back and pick up where you left off. Do not introduce yourself again or "
    "repeat anything they already answered."
)
#: How long a participant who joins back is given to speak first.
_REJOIN_SETTLE_SECONDS: Final[float] = 2.0
#: How long the agent holds the call for a participant who left, such as on a reload,
#: before the call ends and the session's reconnect window opens.
_REJOIN_GRACE_SECONDS: Final[float] = 30.0
#: Who the room knows the agent as; participants are known by their client user.
_AGENT_USER_ID: Final[str] = "utrace-voice-agent"
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
        user_id=_AGENT_USER_ID,
        owner=True,
    )
    floor = Floor(agent_quiet=asyncio.Event(), participant_quiet=asyncio.Event())
    floor.agent_quiet.set()
    floor.participant_quiet.set()
    turn = AgentTurn(floor)
    show = _Character(database, subject)
    spoken = SpokenWords()
    near_end = NearEnd()
    view = ScreenView()
    mic = MicGate(near_end=near_end)

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
    # The Responses API over one WebSocket for the call, which sends each answer only
    # what changed since the last one.
    llm = OpenAIResponsesLLMService(
        api_key=voice.openai_api_key,
        service_tier=voice.text_service_tier.value,
        settings=OpenAIResponsesLLMService.Settings(
            model=voice.text_model,
            system_instruction=await session_instructions(
                database, interview_session_id=subject.interview_session_id
            ),
            max_completion_tokens=2000,
            # Pinned, because Pipecat otherwise asks for no reasoning, which Sol rejects.
            reasoning=OpenAIResponsesLLMService.ReasoningConfig(effort=voice.text_effort.value),
            # `AgentTurn` settles one tool result per answer, so each answer makes at
            # most one call.
            extra={"parallel_tool_calls": False},
        ),
    )
    tts = ClauseStartTTS(
        api_key=voice.elevenlabs_api_key,
        sample_rate=_SAMPLE_RATE_OUT,
        settings=ClauseStartTTS.Settings(model=voice.speech_model, voice=voice.voice_id),
        skip_aggregator_types=[WordsAndGestures.GESTURE],
    )

    async def run_tool(params: FunctionCallParams) -> None:
        # A tool called after words keeps the character speaking until they have
        # played; `AgentFloor` then shows it thinking if the tool still runs.
        if not turn.audible:
            await show(CharacterState.THINKING)
        result = json.loads(
            await execute_tool(
                await _tool_context(database, interview=interview, subject=subject),
                name=params.function_name,
                arguments=params.arguments,
            )
        )
        _log.info(
            "voice_tool_ran",
            interview_session_id=str(subject.interview_session_id),
            tool=params.function_name,
            refused=refused(result),
        )
        # `Answering` decides, once the result is in, whether the agent speaks again.
        await params.result_callback(result)

    # A barge-in never cancels a tool: each records what was said or reads what the
    # agent answers from, and its result settles in the answer that called it.
    for tool in TOOL_DEFINITIONS:
        llm.register_function(tool.name, run_tool, cancel_on_interruption=False)
    context = LLMContext(
        messages=await conversation_so_far(
            database, subject=subject, limit=interview.recent_conversation_fragments
        ),
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
    strategies.stop = [OneSpeculation()]
    aggregators = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(user_turn_strategies=strategies)
    )

    async def read_screen() -> Screen:
        agreed = await agreed_change(database, interview_session_id=subject.interview_session_id)
        return Screen(
            targets=await judge.current_targets(subject=subject.voice()),
            agreed=agreed,
            checks=(
                tuple(await agreed_checks(database, product_signal_id=subject.product_signal_id))
                if agreed
                else ()
            ),
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

    def now_ms() -> int:
        return subject.call_ms(datetime.now(tz=UTC))

    # When the participant's current turn began, on the call's clock.
    turn_started_ms = {"participant": 0}
    # When the last word played, on the call's clock.
    last_word_ms = [-1]

    def heard(word: str) -> Coroutine[object, object, None]:
        """Record one word as it starts playing.

        Words the speech engine releases together share a millisecond, and a fragment
        is unique by its time, so each word is stamped when it plays and at least a
        millisecond after the one before.
        """
        at = max(now_ms(), last_word_ms[0] + 1)
        last_word_ms[0] = at
        # Fragments are joined as they are, so each word carries its own space.
        return record_fragment(
            database,
            subject=subject,
            speaker="agent",
            event_type=_AGENT_EVENT,
            start_ms=at,
            end_ms=at,
            text=f" {word}",
        )

    pipeline = Pipeline(
        [
            transport.input(),
            mic,
            stt,
            aggregators.user(),
            ScreenState(read=read_screen, view=view),
            llm,
            Answering(turn=turn),
            LLMTextProcessor(text_aggregator=WordsAndGestures()),
            tts,
            transport.output(),
            PlaybackTap(spoken=spoken, view=view, gesture=circle, heard=heard),
            AgentFloor(turn=turn, show=show),
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
    agent = PipelineChannel(task, mic, turn)
    changes = SessionChanges(database, interview_session_id=subject.interview_session_id)
    background: list[asyncio.Task[None]] = []
    joined = asyncio.Event()
    close_reason: list[str] = []

    @aggregators.assistant().event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(
        _aggregator: object, message: AssistantTurnStoppedMessage
    ) -> None:
        heard_before_cut_in = strip_marks(message.content)
        if message.interrupted and heard_before_cut_in:
            context.add_message(
                {
                    "role": "user",
                    "content": "<application>"
                    + _CUT_IN_NOTE.format(heard=heard_before_cut_in)
                    + "</application>",
                }
            )

    @aggregators.user().event_handler("on_user_turn_started")
    async def on_user_turn_started(_aggregator: object, _strategy: object) -> None:
        turn_started_ms["participant"] = now_ms()
        floor.participant_quiet.clear()
        await show(CharacterState.LISTENING)

    @aggregators.user().event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(_aggregator: object, _strategy: object, message: object) -> None:
        floor.participant_quiet.set()
        text = getattr(message, "content", None)
        if isinstance(text, str) and text.strip():
            await record_fragment(
                database,
                subject=subject,
                speaker="participant",
                event_type=_PARTICIPANT_EVENT,
                start_ms=turn_started_ms["participant"],
                end_ms=now_ms(),
                text=text.strip(),
            )

    # The call ends only once the participant has been gone for the grace period, so a
    # reload or a browser taking over the call is the same call carrying on.
    departures: list[asyncio.Task[None]] = []

    def participants_present() -> bool:
        return any(key != "local" for key in transport.participants())

    async def end_unless_back(reason: str) -> None:
        await asyncio.sleep(_REJOIN_GRACE_SECONDS)
        close_reason.append(reason)
        await task.cancel()

    async def welcome_back() -> None:
        await agent.note("Their connection dropped for a moment and they are back in the call.")
        await asyncio.sleep(_REJOIN_SETTLE_SECONDS)
        if floor.free():
            await agent.say(_RESUMED_CUE)

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(_transport: object, _participant: object) -> None:
        joined.set()
        await mark_attached(database, subject=subject)
        background.extend(
            _start_background(
                database, changes, subject=subject, agent=agent, judge=judge, floor=floor
            )
        )
        await agent.say(
            await _opening_cue(database, interview_session_id=subject.interview_session_id)
        )

    @transport.event_handler("on_participant_joined")
    async def on_participant_joined(_transport: object, _participant: object) -> None:
        if not departures:
            return
        for departure in departures:
            departure.cancel()
        departures.clear()
        _log.info(
            "voice_participant_rejoined", interview_session_id=str(subject.interview_session_id)
        )
        await show(CharacterState.LISTENING)
        background.append(asyncio.create_task(welcome_back()))

    @transport.event_handler("on_participant_left")
    async def on_participant_left(_transport: object, _participant: object, reason: object) -> None:
        if participants_present() or departures:
            return
        _log.info(
            "voice_participant_left",
            interview_session_id=str(subject.interview_session_id),
            reason=str(reason),
        )
        # Nobody is listening, so the agent stops talking and waits for them.
        await task.queue_frame(InterruptionWorkerFrame())
        await show(CharacterState.RECONNECTING)
        departures.append(asyncio.create_task(end_unless_back(f"participant_left:{reason}")))

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
        stopping = (waiting, listener, *background, *departures)
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
    agent: PipelineChannel,
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
    return None if progress == "not_started" else progress_in_words(progress)


async def _opening_cue(database: Database, *, interview_session_id: UUID) -> str:
    """How the agent opens a call, from what the session's earlier calls already said.

    The agent introduces itself once per session: a call that replaces one on which
    the agent already spoke asks again, or picks up the conversation, instead.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select sessions.phase,
                   exists (
                     select 1 from public.voice_transcript_fragments as fragments
                     where fragments.interview_session_id = sessions.id
                       and fragments.speaker = 'agent'
                   ) as agent_spoke
            from public.interview_sessions as sessions
            where sessions.id = %s
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError("interview_sessions", interview_session_id)
    if as_str(row, "phase") != InterviewPhase.GREETING.value:
        return _RESUMED_CUE
    return _REGREET_CUE if as_bool(row, "agent_spoke") else _GREETING_CUE
