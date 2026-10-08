"""Acknowledging out loud what the participant just did in the preview, when it matters.

The activity feed tells the agent quietly what the person does in the preview. Some
of it the person expects the agent to notice at once: the screen they said they
would open has come up, they finished what the agent asked them to do, or what they
were waiting for appeared. Whether a moment is one of those is Jev's judgement over
the conversation and the activity (`VoiceJudge.acknowledgement`); nothing here
reads the activity's words. When Jev says yes, the feed sends that moment as
speakable context, asking the agent for a few words, instead of quietly.

The application enforces only when an acknowledgement may be spoken at all:

- A moment is the activity one read of the feed finds, and it is acknowledged at
  most once. Activity held while someone was talking is past, so it goes quietly.
- Nobody has the floor (`Floor`): the agent is not talking or running a tool,
  and the participant has paused.
- Acknowledgements are at least `_SPACING_SECONDS` apart, so a run of moments is
  never narrated.
- The decision arrives within `_DECISION_SECONDS` of the read and the floor is
  still free then; a late acknowledgement is worse than none.
"""

import asyncio
from collections.abc import Sequence
from typing import Final

from app.interview.voice.channel import Floor, VoiceSubject
from app.interview.voice.judge import VoiceJudge
from app.logging import get_logger

__all__ = ["Acknowledgements"]

_log = get_logger(__name__)

#: The agent starts speaking within about a second of being asked to, so a decision
#: within this keeps the acknowledgement within about two seconds of the moment.
_DECISION_SECONDS: Final[float] = 1.2
_SPACING_SECONDS: Final[float] = 15.0


class Acknowledgements:
    """Decides, moment by moment, whether the agent acknowledges the participant's activity."""

    def __init__(
        self,
        *,
        subject: VoiceSubject,
        judge: VoiceJudge,
        floor: Floor,
    ) -> None:
        self._subject = subject
        self._judge = judge
        self._floor = floor
        self._last_spoken: float | None = None

    async def worth_saying(self, activity: Sequence[str], *, read_at: float) -> bool:
        """Whether the moment read at loop time ``read_at`` is acknowledged out loud now.

        A True answer counts as spoken: the caller sends the moment speakably.
        """
        loop = asyncio.get_running_loop()
        if not self._may_speak(loop.time()):
            return False
        try:
            async with asyncio.timeout(read_at + _DECISION_SECONDS - loop.time()):
                worth = await self._judge.acknowledgement(subject=self._subject, activity=activity)
        except TimeoutError:
            _log.info(
                "voice_acknowledgement_late",
                interview_session_id=str(self._subject.interview_session_id),
                decision_seconds=round(loop.time() - read_at, 3),
            )
            return False
        decided = loop.time()
        spoken = worth and self._may_speak(decided)
        _log.info(
            "voice_acknowledgement_decided",
            interview_session_id=str(self._subject.interview_session_id),
            worth=worth,
            spoken=spoken,
            decision_seconds=round(decided - read_at, 3),
        )
        if spoken:
            self._last_spoken = decided
        return spoken

    def _may_speak(self, now: float) -> bool:
        return self._floor.free() and (
            self._last_spoken is None or now - self._last_spoken >= _SPACING_SECONDS
        )
