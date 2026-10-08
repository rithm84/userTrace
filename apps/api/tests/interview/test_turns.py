"""When the agent's speech is heard, as the sideband's turn detector works it out."""

from app.interview.character import CharacterState
from app.interview.voice.turns import TurnDetector

_LOUD = 0.5
_CHUNK_BYTES = 4800
"""100 ms of 24 kHz PCM16."""


def _speak(turns: TurnDetector, *, from_ms: int, to_ms: int, now: float) -> None:
    """Deliver generated speech for `from_ms`..`to_ms` on the service timeline, all at `now`."""
    for start in range(from_ms, to_ms, 100):
        turns.agent_audio(
            level=_LOUD, byte_length=_CHUNK_BYTES, start_ms=start, end_ms=start + 100, now=now
        )


def _pause(turns: TurnDetector, *, from_ms: int, now: float) -> CharacterState | None:
    """Deliver the silence after a turn and return the state change it ends on."""
    change = None
    for start in range(from_ms, from_ms + 800, 100):
        change = (
            turns.agent_audio(
                level=0.0, byte_length=_CHUNK_BYTES, start_ms=start, end_ms=start + 100, now=now
            )
            or change
        )
    return change


def test_a_reply_after_the_participant_talks_over_the_agent_is_heard_from_then() -> None:
    """Audio cut off by an interruption is never heard, so it does not delay the next turn."""
    turns = TurnDetector()
    # Ten seconds of speech generated in an instant; the participant cuts in a second later.
    _speak(turns, from_ms=0, to_ms=10_000, now=0.0)
    assert turns.agent_heard_until == 10.0
    turns.agent_interrupted(now=1.0)
    assert _pause(turns, from_ms=10_000, now=1.0) is CharacterState.LISTENING
    assert turns.agent_heard_until == 1.0

    # The agent's one-second reply plays from when it arrives, not after the cut-off nine.
    _speak(turns, from_ms=10_800, to_ms=11_800, now=2.0)
    assert turns.agent_heard_until == 3.0
