"""Who is talking, from the loudness of the call's audio in both directions.

The character should change the moment someone starts or stops talking. Transcript
fragments arrive one to two seconds after speech begins, so they are too late for
that. The sideband already receives both audio streams as they flow: the caller's
input reflected before the model consumes it, and the model's generated output,
both 24 kHz PCM16 mono. A chunk's loudness is measured at decode time and the audio
is discarded; this turns those levels into turn changes.

A turn needs sustained sound, not a click: the participant starts talking after
120 ms of speech-level input, the agent starts on its first loud output, and
either has finished after 700 ms of quiet, at which point the agent is listening.

The model generates its audio faster than it plays, so on a long turn the last
chunk arrives seconds before the participant hears it. Each agent turn is
anchored where its first audible chunk arrived; its end is when its last speech
plays out on that clock (`agent_heard_until`), not when generation stopped.

`Floor` gathers who has the floor for an application turn that needs it free,
such as acknowledging what the participant just did.
"""

import asyncio
from dataclasses import dataclass
from typing import Final

from app.interview.character import CharacterState

__all__ = ["Floor", "TurnDetector"]

#: Root-mean-square level, on a 0 to 1 scale, above which a chunk is speech (-40 dBFS).
_SPEECH_LEVEL: Final[float] = 0.01
_ONSET_MS: Final[float] = 120.0
_PAUSE_MS: Final[float] = 700.0
_BYTES_PER_MS: Final[float] = 48.0
"""24 kHz, two bytes a sample, one channel."""


class TurnDetector:
    """Tracks one connection's turns and reports each change of who is talking."""

    def __init__(self) -> None:
        self._participant_loud_ms = 0.0
        self._participant_quiet_ms = 0.0
        self._participant_speaking = False
        self._participant_spoke_at: float | None = None
        self._agent_loud_ms = 0.0
        self._agent_quiet_ms = 0.0
        self._agent_speaking = False
        self._anchor: tuple[float, int] = (0.0, 0)
        self._last_loud_end_ms = 0


    @property
    def participant_speaking(self) -> bool:
        """Whether the participant is mid-turn: from their onset until a pause."""
        return self._participant_speaking

    @property
    def participant_spoke_at(self) -> float | None:
        """When, on the caller's monotonic clock, the participant's speech was last heard."""
        return self._participant_spoke_at

    def participant_audio(
        self, *, level: float, byte_length: int, now: float
    ) -> CharacterState | None:
        """Return `listening` when the participant has just started talking."""
        duration = byte_length / _BYTES_PER_MS
        if level < _SPEECH_LEVEL:
            self._participant_loud_ms = 0.0
            if self._participant_speaking:
                self._participant_quiet_ms += duration
                self._participant_speaking = self._participant_quiet_ms < _PAUSE_MS
            return None
        self._participant_quiet_ms = 0.0
        before = self._participant_loud_ms
        self._participant_loud_ms += duration
        if self._participant_loud_ms >= _ONSET_MS:
            self._participant_spoke_at = now
        if before < _ONSET_MS <= self._participant_loud_ms:
            self._participant_speaking = True
            return CharacterState.LISTENING
        return None

    @property
    def agent_heard_until(self) -> float:
        """When, on the caller's monotonic clock, the agent's last speech finishes playing."""
        anchored_at, anchor_ms = self._anchor
        return anchored_at + (self._last_loud_end_ms - anchor_ms) / 1000

    def agent_interrupted(self, *, now: float) -> None:
        """The participant talked over the agent, which stops its audio now.

        What it had generated beyond this moment is never heard, so its speech ends
        here, and the next turn plays from now rather than after the cut-off audio.
        """
        self._anchor = (now, self._last_loud_end_ms)

    def agent_audio(
        self, *, level: float, byte_length: int, start_ms: int, end_ms: int, now: float
    ) -> CharacterState | None:
        """Return `speaking` when the agent starts talking, `listening` once it stops.

        `start_ms` and `end_ms` place the chunk on the service's audio timeline and
        `now` is when it arrived, on a monotonic clock.
        """
        duration = byte_length / _BYTES_PER_MS
        if level >= _SPEECH_LEVEL:
            self._agent_quiet_ms = 0.0
            if self._agent_loud_ms == 0.0 and not self._agent_speaking:
                # A turn plays after whatever the previous one has left to play.
                self._anchor = (max(now, self.agent_heard_until), start_ms)
            self._last_loud_end_ms = end_ms
            if self._agent_speaking:
                return None
            self._agent_loud_ms += duration
            if self._agent_loud_ms < _ONSET_MS:
                return None
            self._agent_loud_ms = 0.0
            self._agent_speaking = True
            return CharacterState.SPEAKING
        self._agent_loud_ms = 0.0
        if not self._agent_speaking:
            return None
        self._agent_quiet_ms += duration
        if self._agent_quiet_ms < _PAUSE_MS:
            return None
        self._agent_speaking = False
        return CharacterState.LISTENING


@dataclass(frozen=True, slots=True)
class Floor:
    """Who has the floor, as the sideband keeps it: each event is set while that party does not.

    The agent holds it while its audio plays and from a delegation until the turn
    that speaks its answer ends; the participant holds it from their onset until a
    pause.
    """

    agent_quiet: asyncio.Event
    no_delegation: asyncio.Event
    participant_quiet: asyncio.Event

    def free(self) -> bool:
        """Whether nobody has the floor right now."""
        return (
            self.agent_quiet.is_set()
            and self.no_delegation.is_set()
            and self.participant_quiet.is_set()
        )
