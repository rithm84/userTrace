"""Who has the floor in the voice agent's call, followed from its pipeline's frames.

`AgentTurn` keeps `Floor.agent_quiet` clear while the agent holds the floor, so the
application never talks over it or adds to its conversation while it answers or
works. `Answering`, just after the model, follows its answers and tool calls and
decides whether the agent speaks again once a tool's result is in; `AgentFloor`,
after the transport, follows the speech as it plays. `PipelineChannel` is the
application's `AgentChannel` into the running pipeline.
"""

from collections.abc import Callable, Coroutine

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    FunctionCallCancelFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    FunctionCallResultProperties,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
)
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.interview.character import CharacterState
from app.interview.voice.channel import Floor
from app.interview.voice.pipeline import MicGate, strip_marks
from app.interview.voice.tools import answer_follows, refused

__all__ = ["AgentFloor", "AgentTurn", "Answering", "PipelineChannel"]


class AgentTurn:
    """Whether the agent holds the floor, and so whether `Floor.agent_quiet` is set.

    It holds it from a request to speak until that answer starts, while any answer
    streams, from the end of an answer with words until their audio starts, while
    audio plays, and while any of its tools runs. Each is tracked on its own, so one
    answer's end never releases the floor while another's audio still plays or a
    tool still runs. A barge-in ends the answer but not its tools: they record what
    was said, and their results still arrive.
    """

    def __init__(self, floor: Floor) -> None:
        self._floor = floor
        self._requested = False
        self._streaming = 0
        self._awaiting_audio = False
        self._speaking = False
        self._tools = 0

    @property
    def working(self) -> bool:
        """Whether one of the agent's tools is running."""
        return self._tools > 0

    @property
    def audible(self) -> bool:
        """Whether the agent's audio is playing."""
        return self._speaking

    def request(self) -> None:
        """An answer was asked for: the floor is held until it starts."""
        self._requested = True
        self._refresh()

    def started(self) -> None:
        self._requested = False
        self._streaming += 1
        self._refresh()

    def ended(self, *, with_words: bool) -> None:
        self._streaming = max(0, self._streaming - 1)
        self._awaiting_audio = self._awaiting_audio or with_words
        self._refresh()

    def speaking(self, active: bool) -> None:
        self._speaking = active
        self._awaiting_audio = False
        self._refresh()

    def interrupted(self) -> None:
        self._requested = False
        self._streaming = 0
        self._awaiting_audio = False
        self._refresh()

    def tools_started(self, count: int) -> None:
        self._tools += count
        self._refresh()

    def tool_finished(self, *, answering: bool) -> None:
        """One tool's result is in; the answer to it, if one follows, holds the floor.

        While the participant speaks, the end of their turn brings that answer.
        """
        self._tools -= 1
        if answering and self._floor.participant_quiet.is_set():
            self._requested = True
        self._refresh()

    def _refresh(self) -> None:
        holding = (
            self._requested
            or self._streaming > 0
            or self._awaiting_audio
            or self._speaking
            or self._tools > 0
        )
        if holding:
            self._floor.agent_quiet.clear()
        else:
            self._floor.agent_quiet.set()


class PipelineChannel:
    """`AgentChannel` over the running pipeline."""

    def __init__(self, task: PipelineWorker, mic: MicGate, turn: AgentTurn) -> None:
        self._task = task
        self._mic = mic
        self._turn = turn

    async def say(self, content: str) -> None:
        # The agent holds the floor from this request, so a cue that follows before
        # its answer starts waits for it instead of asking for a second answer.
        self._turn.request()
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


class Answering(FrameProcessor):
    """Follows the model's answers and tool calls as they stream, for the agent's turn.

    It sits just after the model, where an answer's words, then its tool calls, then
    their results arrive in the order the model produced them. So when a result
    arrives it knows whether the answer that made the call said anything, and it
    decides there whether the agent speaks again (`answer_follows`): the assistant
    aggregator downstream runs the model again exactly when the result says so.
    """

    def __init__(self, *, turn: AgentTurn) -> None:
        super().__init__()
        self._turn = turn
        self._has_words = False
        #: Whether the answer that made each running call said anything, by call.
        self._answer_spoke: dict[str, bool] = {}

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            await self._follow(frame)
        await self.push_frame(frame, direction)

    async def _follow(self, frame: Frame) -> None:
        if isinstance(frame, LLMFullResponseStartFrame):
            self._has_words = False
            self._turn.started()
        elif isinstance(frame, LLMTextFrame) and strip_marks(frame.text).strip():
            self._has_words = True
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._turn.ended(with_words=self._has_words)
        elif isinstance(frame, FunctionCallsStartedFrame):
            # A system frame, so it arrives before the answer's end: the floor passes
            # from the answer to its tools without a gap.
            self._turn.tools_started(len(frame.function_calls))
        elif isinstance(frame, FunctionCallInProgressFrame):
            self._answer_spoke[frame.tool_call_id] = self._has_words
        elif isinstance(frame, FunctionCallResultFrame):
            answering = answer_follows(
                frame.function_name,
                refused=refused(frame.result),
                answer_spoke=self._answer_spoke.pop(frame.tool_call_id),
            )
            frame.properties = FunctionCallResultProperties(run_llm=answering)
            self._turn.tool_finished(answering=answering)
        elif isinstance(frame, FunctionCallCancelFrame):
            # A cancelled call has no result to answer; it may end before it started.
            self._answer_spoke.pop(frame.tool_call_id, None)
            self._turn.tool_finished(answering=False)
        elif isinstance(frame, InterruptionFrame):
            self._turn.interrupted()


class AgentFloor(FrameProcessor):
    """Follows the agent's speech as it plays, for the agent's turn and the character."""

    def __init__(
        self,
        *,
        turn: AgentTurn,
        show: Callable[[CharacterState], Coroutine[object, object, None]],
    ) -> None:
        super().__init__()
        self._turn = turn
        self._show = show

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, BotStartedSpeakingFrame):
                self._turn.speaking(True)
                self.create_task(self._show(CharacterState.SPEAKING), "character:speaking")
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self._turn.speaking(False)
                state = CharacterState.THINKING if self._turn.working else CharacterState.LISTENING
                self.create_task(self._show(state), "character:quiet")
        await self.push_frame(frame, direction)
