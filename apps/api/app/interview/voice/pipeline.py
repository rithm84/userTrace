"""The voice agent's pipeline pieces Pipecat does not supply.

Pipecat (`pipecat-ai`) supplies the conversation mechanics: Deepgram Flux proposes
each turn's start and end, eager end of turn starts the answer before the turn is
committed and withdraws it if the person keeps talking, a user turn interrupts the
agent, and the speech engine's word timestamps keep only the words that were played
in the context. These are the pieces uTrace adds:

- `BargeInStrategy`: while the agent talks, a backchannel ("mm-hmm", "yeah") or the
  agent's own voice leaking back into the microphone does not interrupt it; other
  words do.
- `ScreenState`: the controls on the participant's screen and the change's
  progress, appended to each request as an `<application>` block.
- `WordsAndGestures`: splits the agent's gesture marks (`<c id="T5"/>`) out of its
  text and passes the rest to the speech engine a whole word at a time.
- `ClauseStartTTS`: starts each reply's speech at its first clause.
- `PlaybackTap`: sees what the participant hears as it plays, and sends each
  gesture with its word.
- `MicGate`: tracks the participant's microphone level for echo detection and
  drops their audio while the Pause control has muted it.

The measurements behind these choices are in the
[voice and reasoning model measurements](../../../../../docs/arch-components/evaluation-and-observability.md#voice-and-reasoning-model-measurements).
"""

import array
import difflib
import math
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Final

from pipecat.frames.frames import (
    AggregatedTextFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EagerTranscriptionFrame,
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMContextFrame,
    ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext, LLMContextMessage
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.elevenlabs.dialogue.tts import (
    ElevenLabsDialogueTTSService,
    ElevenLabsDialogueTTSSettings,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start import ExternalUserTurnStartStrategy
from pipecat.turns.user_stop import EagerUserTurnStopStrategy
from pipecat.utils.text.base_text_aggregator import Aggregation, AggregationType
from pipecat.utils.text.pattern_pair_aggregator import (
    MatchAction,
    PatternMatch,
    PatternPairAggregator,
)
from pipecat.utils.time import time_now_iso8601

from app.interview.ports import RegisteredTarget
from app.interview.voice.pointing import target_option
from app.logging import get_logger

__all__ = [
    "BargeInStrategy",
    "ClauseStartTTS",
    "FluxSTT",
    "MicGate",
    "NearEnd",
    "OneSpeculation",
    "PlaybackTap",
    "Screen",
    "ScreenState",
    "ScreenView",
    "SpokenWords",
    "WordsAndGestures",
    "strip_marks",
]

_log = get_logger(__name__)

#: RMS of full scale at which microphone audio counts as speech.
SPEECH_LEVEL: Final[float] = 0.01
#: Words someone says to show they are listening, not to take the floor. Fillers such
#: as "um" or "oh" are left out: they usually begin a real interruption.
_BACKCHANNEL_WORDS: Final[frozenset[str]] = frozenset(
    [
        "yeah",
        "yep",
        "yes",
        "yup",
        "ya",
        "right",
        "okay",
        "ok",
        "sure",
        "cool",
        "nice",
        "gotcha",
        "got",
        "it",
        "totally",
        "exactly",
        "true",
        "alright",
        "all",
    ]
)
#: mm, mhm, mhmm, mm-hmm, hmm, uh-huh.
_HUM: Final[re.Pattern[str]] = re.compile(r"^(m+(-?h*m+)*|h+m+|u+h+-?h+u+h+)$")
_ECHO_WINDOW_SECONDS: Final[float] = 4.0
#: Of the words heard, the share that replay the agent's recent words in order.
_ECHO_SHARE: Final[float] = 0.6
#: Flux proposes a turn a few hundred ms into speech; how far back to look for the
#: participant's own speech-level audio.
_NEAR_END_LEAD_SECONDS: Final[float] = 1.0
_MARK: Final[re.Pattern[str]] = re.compile(r'<c id="[^"]*"/>')


def strip_marks(text: str) -> str:
    """The agent's words without its gesture marks, as the participant heard them."""
    return " ".join(_MARK.sub(" ", text).split())


def _words_of(text: str) -> list[str]:
    # The model writes typographic apostrophes ("I’m") where Flux writes plain ones.
    return re.findall(r"[a-z0-9']+(?:-[a-z0-9']+)*", text.lower().replace("’", "'"))


def _is_backchannel_word(word: str) -> bool:
    return word in _BACKCHANNEL_WORDS or _HUM.match(word) is not None


def _similar(heard: str, said: str) -> bool:
    """Whether a transcribed word could be a mishearing of one the agent said ("Shore" for "short")."""
    return heard == said or difflib.SequenceMatcher(None, heard, said).ratio() >= 0.8


def _echoed_share(heard: list[str], said: list[str]) -> float:
    """Share of `heard` that replays `said` in order (a fuzzy longest common subsequence).

    Echo replays the agent's words in sequence; a person who repeats a few of them
    ("where's the save button?") does so inside words of their own.
    """
    previous = [0] * (len(said) + 1)
    for word in heard:
        current = [0]
        for index, spoken in enumerate(said):
            current.append(
                previous[index] + 1
                if _similar(word, spoken)
                else max(previous[index + 1], current[index])
            )
        previous = current
    return previous[-1] / len(heard)


class SpokenWords:
    """What the participant recently heard the agent say, for recognizing echo."""

    def __init__(self) -> None:
        self._words: deque[tuple[float, str]] = deque(maxlen=200)

    def add(self, text: str) -> None:
        now = time.time()
        self._words.extend((now, word) for word in _words_of(text))

    def recent(self, seconds: float) -> list[str]:
        """The words the agent spoke in the last `seconds`, in order."""
        cutoff = time.time() - seconds
        return [word for at, word in self._words if at >= cutoff]


class NearEnd:
    """When the participant's own microphone last carried speech-level audio.

    Echo of the agent reaches the microphone far quieter than the participant's own
    voice once the browser has cancelled most of it, so a barge-in needs
    speech-level audio at the microphone, not only a transcript.
    """

    def __init__(self) -> None:
        self.last_loud: float | None = None

    def loud_since(self, at: float) -> bool:
        return self.last_loud is not None and self.last_loud >= at


class MicGate(FrameProcessor):
    """Tracks the participant's microphone level, and drops it while muted."""

    def __init__(self, *, near_end: NearEnd) -> None:
        super().__init__()
        self._near_end = near_end
        self.muted = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            if self.muted:
                return
            samples = array.array("h", frame.audio)
            if (
                samples
                and math.sqrt(sum(x * x for x in samples) / len(samples)) / 32768 >= SPEECH_LEVEL
            ):
                self._near_end.last_loud = time.time()
        await self.push_frame(frame, direction)


class FluxSTT(DeepgramFluxSTTService):
    """Flux, also publishing the in-turn transcript it sends as interim transcriptions."""

    async def _handle_start_of_turn(self, transcript: str) -> None:
        await super()._handle_start_of_turn(transcript)
        await self._push_interim(transcript)

    async def _handle_update(self, transcript: str) -> None:
        await super()._handle_update(transcript)
        await self._push_interim(transcript)

    async def _push_interim(self, transcript: str) -> None:
        if transcript:
            await self.push_frame(
                InterimTranscriptionFrame(transcript, self._user_id, time_now_iso8601())
            )


class OneSpeculation(EagerUserTurnStopStrategy):
    """Answers each predicted end of turn once, even when Flux revises the prediction.

    Flux can report a second eager end of turn inside one turn, such as the same
    words with a period added. Pipecat 1.12 then speculates again without
    withdrawing the first speculation, so both responses run and both are spoken
    once the turn is confirmed. A revision the match policy reads as the same words
    keeps the speculation already running; any other revision withdraws it first,
    as a resumed turn does. The speculation in flight and the turn's committed text
    are only held privately by the base strategy.
    """

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, EagerTranscriptionFrame) and self._speculation is not None:
            revised = self._text + frame.text
            if self.match_policy.matches(self._speculation.text, revised):
                return ProcessFrameResult.CONTINUE
            await self._take_speculation()
            await self.trigger_user_turn_speculation_cancelled()
        return await super().process_frame(frame)


class BargeInStrategy(ExternalUserTurnStartStrategy):
    """Opens a user turn from Flux's proposals, protecting the agent's speech.

    While the agent is silent, a proposal opens the turn at once (and cancels any
    answer still being generated). While it is talking, the utterance stays pending
    and is classified as its transcript arrives:

    - "words" open the turn and interrupt the agent;
    - a backchannel lets the agent keep talking, unless the agent finishes first,
      when it may be the answer to a question and opens the turn;
    - echo (no speech-level audio at the participant's microphone during the
      utterance, or words that replay what the agent just said) never opens it.

    Flux can fold echo and the participant's own words into one turn, so once part
    of a pending transcript has been judged, only the words after it are judged
    next, against the microphone activity since. A pending utterance that Flux ends
    without opening the turn is dropped from the transcript.
    """

    def __init__(self, *, spoken: SpokenWords, near_end: NearEnd) -> None:
        super().__init__(enable_interruptions=True)
        self._spoken = spoken
        self._near_end = near_end
        self._agent_speaking = False
        self._segment_since = 0.0  # when the words not yet judged began
        self._clear_pending()

    def _clear_pending(self) -> None:
        self._pending_since: float | None = None
        self._heard = ""
        self._judged_words = 0  # leading words of `_heard` already judged not to take the floor
        self._last_category = "empty"

    async def handle_user_turn_started(self) -> None:
        self._clear_pending()

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, BotStartedSpeakingFrame):
            self._agent_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._agent_speaking = False
            if self._pending_since is not None:
                await self._decide()
        elif isinstance(frame, ProposedUserStartedSpeakingFrame):
            if self._agent_speaking:
                self._clear_pending()
                self._pending_since = self._segment_since = time.time()
            else:
                await self._open("idle")
            return ProcessFrameResult.STOP
        elif (
            isinstance(frame, InterimTranscriptionFrame | TranscriptionFrame)
            and self._pending_since is not None
        ):
            # Deciding on the transcription itself, before the stop strategies see it,
            # keeps its text in the turn that opens.
            self._heard = frame.text
            await self._decide()
        elif (
            isinstance(frame, ProposedUserStoppedSpeakingFrame) and self._pending_since is not None
        ):
            category = self._kind(self._heard) if self._judged_words == 0 else self._last_category
            _log.info("voice_utterance_ignored", heard=self._heard, category=category)
            self._clear_pending()
            await self.trigger_reset_aggregation()
            return ProcessFrameResult.CONTINUE
        return await super().process_frame(frame)

    async def _decide(self) -> None:
        words = _words_of(self._heard)
        category = self._kind(" ".join(words[self._judged_words :]))
        if category == "words":
            await self._open("barge_in" if self._agent_speaking else "agent_finished")
        elif not self._agent_speaking and (
            category == "backchannel"
            or (category == "empty" and self._last_category == "backchannel")
        ):
            # The agent finished first: a backchannel may be the answer to its question.
            await self._open("agent_finished")
        elif category != "empty":
            self._last_category = category
            self._judged_words = len(words)
            self._segment_since = time.time()

    def _kind(self, text: str) -> str:
        words = _words_of(text)
        if not words:
            return "empty"
        if self._pending_since is not None and not self._near_end.loud_since(
            self._segment_since - _NEAR_END_LEAD_SECONDS
        ):
            return "echo"
        if all(_is_backchannel_word(word) for word in words):
            return "backchannel"
        if _echoed_share(words, self._spoken.recent(_ECHO_WINDOW_SECONDS)) >= _ECHO_SHARE:
            return "echo"
        return "words"

    async def _open(self, reason: str) -> None:
        pending_ms = (
            None
            if self._pending_since is None
            else round((time.time() - self._pending_since) * 1000)
        )
        _log.info("voice_user_turn_opened", reason=reason, heard=self._heard, pending_ms=pending_ms)
        self._clear_pending()
        await self.trigger_user_turn_started()


@dataclass(frozen=True, slots=True)
class Screen:
    """The participant's screen as the application describes it to the agent."""

    targets: tuple[RegisteredTarget, ...]
    agreed: str | None
    """The change agreed with the participant, once one is recorded."""
    checks: tuple[str, ...]
    """The agreed checks, in order; the agent and its tools number them from 1."""
    progress: str | None

    def target(self, handle: str) -> RegisteredTarget | None:
        """The control a gesture mark names, if it is on the current screen."""
        if not handle.startswith("T") or not handle[1:].isdigit():
            return None
        index = int(handle[1:])
        return self.targets[index - 1] if 1 <= index <= len(self.targets) else None

    def block(self) -> str:
        lines = ["Controls on their screen:"]
        if self.targets:
            lines += [
                f"T{index}: {target_option(target)}"
                for index, target in enumerate(self.targets, start=1)
            ]
        else:
            lines.append("none reported yet")
        if self.agreed:
            lines.append(f"Agreed change: {self.agreed}")
            lines += [
                f"Agreed check {number}: {check}"
                for number, check in enumerate(self.checks, start=1)
            ]
        if self.progress:
            lines.append(f"Progress of the change: {self.progress}")
        return "<application>\n" + "\n".join(lines) + "\n</application>"


@dataclass(slots=True)
class ScreenView:
    """The screen the agent last saw a block for, so its marks resolve against it."""

    current: Screen = field(
        default_factory=lambda: Screen(targets=(), agreed=None, checks=(), progress=None)
    )


class ScreenState(FrameProcessor):
    """Appends the current `<application>` block to the last user message of each request.

    The conversation's own context is left untouched; the model sees the block on
    the request it is answering. `read` returns the screen as it is now.
    """

    def __init__(self, *, read: Callable[[], Awaitable[Screen]], view: ScreenView) -> None:
        super().__init__()
        self._read = read
        self._view = view

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            self._view.current = await self._read()
            frame.context = _with_block(frame.context, self._view.current.block())
        await self.push_frame(frame, direction)


def _with_block(context: LLMContext, block: str) -> LLMContext:
    messages: list[LLMContextMessage] = list(context.get_messages())
    last = messages[-1] if messages else None
    if (
        isinstance(last, dict)
        and last.get("role") == "user"
        and isinstance(last.get("content"), str)
    ):
        messages[-1] = {"role": "user", "content": f"{last['content']}\n{block}"}
    else:
        messages.append({"role": "user", "content": block})
    return LLMContext(messages=messages, tools=context.tools, tool_choice=context.tool_choice)


class WordsAndGestures(PatternPairAggregator):
    """Splits `<c id="T5"/>` marks out of the agent's text as "gesture" aggregations, and
    passes the rest on a whole word at a time.

    Token mode sends text to the speech engine as the model writes it instead of
    holding each sentence; releasing only up to the last space keeps the engine from
    splitting a word.
    """

    TEXT: Final[str] = "words"
    GESTURE: Final[str] = "gesture"

    def __init__(self) -> None:
        super().__init__(aggregation_type=AggregationType.TOKEN)
        self.add_pattern(
            type=self.GESTURE,
            start_pattern='<c id="',
            end_pattern='"/>',
            action=MatchAction.AGGREGATE,
        )
        self._words = ""

    async def aggregate(self, text: str) -> AsyncIterator[PatternMatch]:
        async for aggregation in super().aggregate(text):
            if aggregation.type == AggregationType.TOKEN:
                # full_match keeps the whitespace that `text` strips before a mark.
                self._words += (
                    aggregation.full_match
                    if isinstance(aggregation, PatternMatch)
                    else aggregation.text
                )
                cut = max(self._words.rfind(" "), self._words.rfind("\n")) + 1
                if re.search(r"[^\d\s][,.;:!?]$", self._words):
                    cut = len(self._words)  # a word ending in punctuation is complete ("Mm-hmm.")
                if cut:
                    chunk, self._words = self._words[:cut], self._words[cut:]
                    if chunk.strip():  # the speech engine adds the space after a chunk itself
                        yield PatternMatch(content=chunk, type=self.TEXT, full_match=chunk)
            else:
                if self._words:
                    chunk, self._words = self._words, ""
                    yield PatternMatch(content=chunk, type=self.TEXT, full_match=chunk)
                yield aggregation

    async def flush(self) -> Aggregation | None:
        words, self._words = self._words, ""  # super().flush() resets, which clears them
        rest = await super().flush()
        text = words + (
            rest.full_match if isinstance(rest, PatternMatch) else rest.text if rest else ""
        )
        return Aggregation(text=text, type=self.TEXT) if text.strip() else None

    async def handle_interruption(self) -> None:
        self._words = ""
        await super().handle_interruption()

    async def reset(self) -> None:
        self._words = ""
        await super().reset()


class ClauseStartTTS(ElevenLabsDialogueTTSService):
    """Starts each reply's speech at its first clause.

    Eleven v4 Turbo holds text until roughly 40 characters have arrived; flushing
    once, at the first clause boundary of a reply, starts speech without waiting for
    more text. Later text streams normally.
    """

    def __init__(
        self,
        *,
        api_key: str,
        sample_rate: int,
        settings: ElevenLabsDialogueTTSSettings,
        skip_aggregator_types: list[str],
    ) -> None:
        super().__init__(
            api_key=api_key,
            sample_rate=sample_rate,
            settings=settings,
            skip_aggregator_types=skip_aggregator_types,
        )
        self._started: set[str] = set()

    async def _send_text(self, text: str, context_id: str) -> None:
        await super()._send_text(text, context_id)
        if context_id not in self._started and re.search(r"[,.;:!?]\s*$", text):
            self._started.add(context_id)
            await self.flush_audio(context_id)


class PlaybackTap(FrameProcessor):
    """Sees what the participant hears, as it plays: each word, and each gesture with its word.

    A gesture mark reaches this point once the audio before it has been handed to
    the transport, which can be well before that audio plays. Each gesture is
    therefore sent with the next word, whose text frame the output transport
    releases on its playout clock. The mark frame waits with it, so the context
    records only gestures that were shown; an interruption drops both. Each word is
    handed to `heard`, without marks, as it starts playing.
    """

    def __init__(
        self,
        *,
        spoken: SpokenWords,
        view: ScreenView,
        gesture: Callable[[RegisteredTarget], Coroutine[object, object, None]],
        heard: Callable[[str], Coroutine[object, object, None]],
    ) -> None:
        super().__init__()
        self._spoken = spoken
        self._view = view
        self._gesture = gesture
        self._heard = heard
        self._waiting: list[AggregatedTextFrame] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, TTSTextFrame):
                for mark in self._waiting:
                    handle = mark.text.strip()
                    target = self._view.current.target(handle)
                    if target is None:
                        _log.warning("voice_gesture_unknown_handle", handle=handle)
                    else:
                        # Dispatching writes durable records; the words behind it keep flowing.
                        self.create_task(self._gesture(target), f"gesture:{handle}")
                    await self.push_frame(mark, direction)
                self._waiting.clear()
                # The model's own words this frame plays: the speech engine's reported
                # word can differ from them, and its reports can come out of order.
                said = strip_marks(frame.raw_text or frame.text)
                if said:
                    self._spoken.add(said)
                    self.create_task(self._heard(said), "heard")
            elif (
                isinstance(frame, AggregatedTextFrame)
                and frame.aggregated_by == WordsAndGestures.GESTURE
            ):
                self._waiting.append(frame)
                return
            elif isinstance(frame, InterruptionFrame) and self._waiting:
                _log.info(
                    "voice_gestures_dropped",
                    handles=[mark.text.strip() for mark in self._waiting],
                )
                self._waiting.clear()
        await self.push_frame(frame, direction)
