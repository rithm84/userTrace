"""The voice agent's turns, driven with the frames Deepgram Flux and the model send.

A predicted end of turn starts one speculative answer. When Flux revises the
prediction inside the same turn, the agent must still answer the turn once: the
same words keep the answer already running, and new words replace it. An answer a
barge-in cancelled must never arrive as the next one.

An answer may also be to keep listening. Through the agent's own pipeline pieces
and Pipecat's assistant aggregator, such an answer must reach neither the speech
engine nor the floor, and the context must show that the agent listened; a
backchannel must never follow the agent's last one straight after or within
seconds, unless the participant asked something.
"""

import asyncio
import time
from collections.abc import Sequence
from types import SimpleNamespace
from typing import cast

import pytest
from pipecat.frames.frames import (
    EagerTranscriptionFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor, FrameProcessorSetup
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.utils.asyncio.task_manager import TaskManager
from websockets.protocol import State

from app.interview.voice import pipeline
from app.interview.voice.channel import Floor
from app.interview.voice.pipeline import (
    Listening,
    OneSpeculation,
    ResponsesLLM,
    WordsAndGestures,
)
from app.interview.voice.turn_taking import AgentTurn, Answering


async def test_a_revised_prediction_answers_the_turn_once() -> None:
    strategy = OneSpeculation()
    await strategy.setup(
        cast(
            FrameProcessorSetup,
            SimpleNamespace(task_manager=TaskManager(loop=asyncio.get_running_loop())),
        )
    )
    events: list[str] = []

    @strategy.event_handler("on_user_turn_inference_triggered")
    async def inferred(*_: object, **__: object) -> None:
        events.append("inference")

    @strategy.event_handler("on_user_turn_speculation_cancelled")
    async def cancelled(*_: object, **__: object) -> None:
        events.append("cancelled")

    # What Flux sent at 03:08:18.857 and 03:08:19.057 in the call that answered twice.
    await strategy.process_frame(EagerTranscriptionFrame("Alright. Let me try", "user", "t1"))
    await strategy.process_frame(EagerTranscriptionFrame("Alright. Let me try.", "user", "t2"))
    assert events == ["inference"]

    await strategy.process_frame(
        EagerTranscriptionFrame("Alright, let me try saving it now.", "user", "t3")
    )
    assert events == ["inference", "cancelled", "inference"]
    await strategy.cleanup()


class _StillAnswering:
    """A Responses socket on which a cancelled answer is still being generated."""

    state = State.OPEN
    closed = False

    async def recv(self) -> str:
        await asyncio.sleep(3600)
        return ""

    async def close(self) -> None:
        self.closed = True


async def test_a_cancelled_answer_still_running_is_dropped_with_its_connection() -> None:
    llm = ResponsesLLM(api_key="unused", settings=ResponsesLLM.Settings(model="gpt-6.1-sol"))
    socket = _StillAnswering()
    llm._websocket = socket  # type: ignore[assignment]
    llm._needs_drain = True
    started = time.monotonic()

    await llm._drain_cancelled_response()

    # Pipecat alone waits five seconds, then answers the next request from this socket.
    assert time.monotonic() - started < 1.0
    assert socket.closed
    assert llm._websocket is None


def _turn(heard: str, *answer: str) -> list[Frame]:
    """The participant's words, then the model's answer to them as it streams."""
    return [
        LLMMessagesAppendFrame(messages=[{"role": "user", "content": heard}], run_llm=False),
        # The context holds their words before the model answers, as the user aggregator does.
        SleepFrame(0.05),
        LLMFullResponseStartFrame(),
        *(LLMTextFrame(text) for text in answer),
        LLMFullResponseEndFrame(),
        # The aggregator records each answer before the next one is judged, as playing it does.
        SleepFrame(0.05),
    ]


class _SpeechEngine(FrameProcessor):
    """Where the speech engine sits: records the text that would be spoken."""

    def __init__(self) -> None:
        super().__init__()
        self.spoken: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            self.spoken.append(frame.text)
        await self.push_frame(frame, direction)


async def _answers(frames: Sequence[Frame]) -> tuple[list[str], list[str], Floor]:
    """Run the model's frames through the agent's pieces; return what reached the
    speech engine, the agent's answers as its context records them, and the floor."""
    floor = Floor(
        agent_quiet=asyncio.Event(), participant_quiet=asyncio.Event(), agent_spoke=asyncio.Event()
    )
    floor.participant_quiet.set()
    context = LLMContext()
    speech = _SpeechEngine()
    await run_test(
        Pipeline(
            [
                Listening(context=context),
                Answering(turn=AgentTurn(floor)),
                speech,
                LLMContextAggregatorPair(context).assistant(),
            ]
        ),
        frames_to_send=frames,
    )
    spoken = speech.spoken
    recorded = [
        str(message["content"])
        for message in context.get_messages()
        if isinstance(message, dict) and message.get("role") == "assistant"
    ]
    return spoken, recorded, floor


async def test_an_answer_that_listens_is_never_spoken_and_frees_the_floor() -> None:
    spoken, recorded, floor = await _answers(
        [
            *_turn("Sure.", "<lis", "ten", "/>"),
            *_turn("Let me try.", "<listen/>", " Mm-hmm."),
            # Cut off by a barge-in before its first word: no choice to record.
            *_turn("Okay, TikTok first,", ""),
            *_turn("then the segment,", "<listen", " />"),
        ]
    )

    assert spoken == []
    assert recorded == ["<listen/>", "<listen/>", "<listen/>"]
    # A spoken answer holds the floor until its audio plays; these never do.
    assert floor.agent_quiet.is_set()


async def test_backchannels_never_follow_one_straight_after_or_within_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "_BACKCHANNEL_SPACING_SECONDS", 0.5)
    spoken, recorded, _ = await _answers(
        [
            *_turn("Sure.", "Mm", "-hmm."),
            # Straight after it, even past the spacing.
            SleepFrame(0.6),
            *_turn("Let me try.", "Okay."),
            # A question is answered, however briefly.
            *_turn("Should I save it now?", "Go", " ahead."),
            *_turn("Okay, saving.", "Got it."),
            *_turn("Where did it go?", "It's", " under Saved views now."),
            # Not straight after a backchannel, but within the spacing of the last one.
            *_turn("Okay.", "Okay."),
            SleepFrame(0.6),
            *_turn("Now refreshing.", "Okay, take", " your time."),
        ]
    )

    assert spoken == [
        "Mm",
        "-hmm.",
        "Go",
        " ahead.",
        "It's",
        " under Saved views now.",
        "Okay, take",
        " your time.",
    ]
    assert recorded == [
        "Mm-hmm.",
        "<listen/>",
        "Go ahead.",
        "<listen/>",
        "It's under Saved views now.",
        "<listen/>",
        "Okay, take your time.",
    ]


async def test_a_listening_mark_inside_spoken_words_is_never_read_out() -> None:
    words = WordsAndGestures()
    said: list[str] = []
    for chunk in ("Okay, <lis", "ten/> that's ", 'the <c id="T1"/>channel.'):
        said += [
            aggregation.text
            async for aggregation in words.aggregate(chunk)
            if aggregation.type == WordsAndGestures.TEXT
        ]
    rest = await words.flush()

    assert " ".join([*said, rest.text if rest else ""]).split() == [
        "Okay,",
        "that's",
        "the",
        "channel.",
    ]
