"""The agent's proposal mark, driven with the frames the model streams.

The agent writes each change it proposes as a mark in the reply that proposes it.
Through the agent's own pipeline pieces and Pipecat's assistant aggregator, the mark
must never reach the speech engine, however the model splits it into tokens; it
must be recorded once, as soon as it is whole, without holding up the words after
it; and it must be kept for the agent's context, which the agent writes once the
reply's turn ends. A mark a barge-in cuts off, or one
that is not a JSON object, records nothing, and the words around it are spoken.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.tests.utils import SleepFrame, run_test

from app.interview.voice.channel import Floor
from app.interview.voice.pipeline import Listening, ProposalMarks, strip_marks
from app.interview.voice.turn_taking import AgentTurn, Answering

PROPOSAL: dict[str, object] = {
    "proposed_outcome": "Save the filter set under a name {you} choose and reopen it.",
    "workflow": "saved_conversion_views",
    "criteria": [
        {"statement": "Reopening a saved view restores every \"saved\" filter.", "observability": "observed"}
    ],
}
MARK = "<proposal>" + json.dumps(PROPOSAL) + "</proposal>"


def _turn(heard: str, *answer: str) -> list[Frame]:
    """The participant's words, then the model's answer to them as it streams."""
    return [
        LLMMessagesAppendFrame(messages=[{"role": "user", "content": heard}], run_llm=False),
        SleepFrame(0.05),
        LLMFullResponseStartFrame(),
        *(LLMTextFrame(text) for text in answer),
        LLMFullResponseEndFrame(),
        SleepFrame(0.05),
    ]


def _tokens(text: str, size: int) -> list[str]:
    """`text` as the model might stream it, `size` characters at a time."""
    return [text[index : index + size] for index in range(0, len(text), size)]


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


class _Recorder:
    """Stands in for recording a proposal; `release` lets each recording finish."""

    def __init__(self, *, hold: bool = False) -> None:
        self.started: list[Mapping[str, object]] = []
        self.finished: list[Mapping[str, object]] = []
        self.release = asyncio.Event()
        if not hold:
            self.release.set()

    async def __call__(self, arguments: Mapping[str, object]) -> None:
        self.started.append(arguments)
        await self.release.wait()
        self.finished.append(arguments)


async def _run(
    frames: Sequence[Frame], recorder: _Recorder, *, between: FrameProcessor | None = None
) -> tuple[str, list[str], list[str]]:
    """Run the model's frames through the agent's pieces; return what reached the
    speech engine, the agent's answers as its context records them, and the marks
    kept for its context."""
    floor = Floor(
        agent_quiet=asyncio.Event(), participant_quiet=asyncio.Event(), agent_spoke=asyncio.Event()
    )
    floor.participant_quiet.set()
    context = LLMContext()
    speech = _SpeechEngine()
    marks = ProposalMarks(record=recorder)
    await run_test(
        Pipeline(
            [
                marks,
                Listening(context=context),
                Answering(turn=AgentTurn(floor)),
                speech,
                *([between] if between is not None else []),
                LLMContextAggregatorPair(context).assistant(),
            ]
        ),
        frames_to_send=frames,
    )
    recorded = [
        str(message["content"])
        for message in context.get_messages()
        if isinstance(message, dict) and message.get("role") == "assistant"
    ]
    return "".join(speech.spoken), recorded, marks.take_marks()


async def test_a_mark_split_across_tokens_is_recorded_once_and_never_spoken() -> None:
    words = (
        'I suggest named saved views for <c id="T2"/>that set. Once ready, you\'ll save one '
        "and reopen it. "
    )
    reply = words + MARK + " Does that work?"
    for size in (1, 3, 7, len(reply)):
        recorder = _Recorder()
        spoken, recorded, kept = await _run(
            _turn("It's for Mondays.", *_tokens(reply, size)), recorder
        )

        assert "proposal" not in spoken and "{" not in spoken, size
        assert spoken == words + " Does that work?", size
        assert recorder.finished == [PROPOSAL], size
        # The context holds what was said; the mark is kept for the agent to add.
        assert recorded == [words + " Does that work?"], size
        assert kept == [MARK], size
        assert strip_marks(words + MARK) == strip_marks(words), size


async def test_cursor_and_listening_marks_still_reach_their_own_handling() -> None:
    recorder = _Recorder()
    spoken, recorded, kept = await _run(
        [
            *_turn("Okay.", "<", "lis", "ten/>"),
            *_turn("Where is it?", "It's ", "<", 'c id="T5"/>', "right ", "here."),
        ],
        recorder,
    )

    assert spoken == 'It\'s <c id="T5"/>right here.'
    assert recorded == ["<listen/>", 'It\'s <c id="T5"/>right here.']
    assert recorder.started == [] and kept == []


async def test_a_mark_without_its_closing_tag_still_lets_the_question_be_spoken() -> None:
    recorder = _Recorder()
    unclosed = "<proposal>" + json.dumps(PROPOSAL)
    spoken, _, kept = await _run(
        _turn("Sounds good.", "Named saved views. ", *_tokens(unclosed, 5), " Does that work?"),
        recorder,
    )

    assert spoken == "Named saved views.  Does that work?"
    assert recorder.finished == [PROPOSAL]
    assert kept == [MARK]


async def test_a_mark_that_is_not_a_json_object_records_nothing_and_is_never_spoken() -> None:
    recorder = _Recorder()
    spoken, recorded, kept = await _run(
        [
            *_turn("Yes.", "Named views. ", "<proposal>", "save views", "</proposal>", " Okay?"),
            *_turn("Yes.", "Named views. ", "<proposal>", '{"broken": ', "}}</proposal>", " Okay?"),
        ],
        recorder,
    )

    assert spoken == "Named views.  Okay?" * 2
    assert recorder.started == [] and kept == []
    assert all("proposal" not in message for message in recorded)


async def test_a_mark_cut_off_by_a_barge_in_or_the_reply_end_records_nothing() -> None:
    recorder = _Recorder()
    half = MARK[: len(MARK) // 2]
    spoken, _, kept = await _run(
        [
            LLMFullResponseStartFrame(),
            LLMTextFrame("Named saved views. "),
            *(LLMTextFrame(token) for token in _tokens(half, 4)),
            InterruptionFrame(),
            SleepFrame(0.05),
            *_turn("Wait, also export it.", "Named views. ", *_tokens(half, 6)),
            # The next reply is not taken for the rest of a mark.
            *_turn("Hello?", "Sorry, does that work?"),
        ],
        recorder,
    )

    assert recorder.started == [] and kept == []
    assert "proposal" not in spoken and "{" not in spoken
    assert spoken.endswith("Sorry, does that work?")


async def test_recording_runs_in_the_background_without_holding_the_words_after_it() -> None:
    recorder = _Recorder(hold=True)

    class _Release(FrameProcessor):
        """Lets the recording finish only once the reply has ended downstream."""

        async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, LLMFullResponseEndFrame):
                assert recorder.started == [PROPOSAL] and recorder.finished == []
                recorder.release.set()
            await self.push_frame(frame, direction)

    spoken, _, _ = await _run(
        _turn("Yes.", "Named saved views. ", MARK, " Does that work?"), recorder, between=_Release()
    )

    assert spoken == "Named saved views.  Does that work?"
    assert recorder.finished == [PROPOSAL]


async def test_each_reply_that_proposes_records_its_own_mark() -> None:
    second = {**PROPOSAL, "proposed_outcome": "Keep saved views, at the far right of the date row."}
    recorder = _Recorder()
    _, _, kept = await _run(
        [
            *_turn("Yes.", "Named views. ", MARK, " Okay?"),
            *_turn(
                "Move it right.",
                "Moving them right. ",
                "<proposal>" + json.dumps(second) + "</proposal>",
                " Okay?",
            ),
        ],
        recorder,
    )

    assert recorder.finished == [PROPOSAL, second]
    assert kept == [MARK, "<proposal>" + json.dumps(second) + "</proposal>"]


async def test_a_mark_whole_before_a_barge_in_is_still_kept_for_the_context() -> None:
    """Its draft was already started, so the agent must see that it proposed it."""
    recorder = _Recorder()
    spoken, _, kept = await _run(
        [
            LLMFullResponseStartFrame(),
            LLMTextFrame("Named saved views. "),
            LLMTextFrame(MARK),
            LLMTextFrame(" Does that"),
            InterruptionFrame(),
            SleepFrame(0.05),
        ],
        recorder,
    )

    assert recorder.finished == [PROPOSAL]
    assert kept == [MARK]
    assert "proposal" not in spoken
