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
        self._agent_loud_ms = 0.0
        self._agent_quiet_ms = 0.0
        self._agent_speaking = False

    @property
    def agent_speaking(self) -> bool:
        """Whether the agent is mid-turn, so nothing should be appended to its context."""
        return self._agent_speaking

    @property
    def participant_speaking(self) -> bool:
        """Whether the participant is mid-turn: from their onset until a pause."""
        return self._participant_speaking

    def participant_audio(self, *, level: float, byte_length: int) -> CharacterState | None:
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
        if before < _ONSET_MS <= self._participant_loud_ms:
            self._participant_speaking = True
            return CharacterState.LISTENING
        return None

    def agent_audio(self, *, level: float, byte_length: int) -> CharacterState | None:
        """Return `speaking` when the agent starts talking, `listening` once it stops."""
        duration = byte_length / _BYTES_PER_MS
        if level >= _SPEECH_LEVEL:
            self._agent_quiet_ms = 0.0
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
