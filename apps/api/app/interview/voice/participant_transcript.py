"""Whether the participant's transcript reaches the end of what they said.

GPT-Live transcribes the caller in fragments, sometimes seconds behind their
audio, and can drop the end of a question it has already acted on: it delegates
on "…which one is the" and the rest never arrives. A delegation judged from those
words answers the question before. Each fragment's `end_ms` and the delegation's
`offset_ms` share the session timeline, and the caller's reflected audio shows
when they last spoke, so the gap between their last transcribed word and their
last speech is the part of the question missing from the transcript.
"""

import asyncio
from typing import Final

__all__ = ["ParticipantTranscript", "spoke_until_ms"]

#: How far the transcript may end before the participant's last speech and still be
#: complete: the quiet before a pause still registers as speech for a moment.
_COMPLETE_WITHIN_MS: Final[int] = 600


def spoke_until_ms(*, offset_ms: int | None, delegated_at: float, spoke_at: float | None) -> int | None:
    """Where the participant's last speech before a delegation sits on the session timeline.

    `offset_ms` is where the delegation sits and `delegated_at` is when it arrived;
    their speech, last heard at `spoke_at`, is placed by how long before the
    delegation it arrived. `None` when either is unknown.
    """
    if offset_ms is None or spoke_at is None:
        return None
    return offset_ms - round((delegated_at - spoke_at) * 1000)


class ParticipantTranscript:
    """How far the participant's recorded transcript reaches on the session timeline."""

    def __init__(self) -> None:
        self._until_ms = 0
        self._changed = asyncio.Condition()

    @property
    def until_ms(self) -> int:
        """Where the recorded transcript ends on the session timeline."""
        return self._until_ms

    async def transcribed(self, *, end_ms: int) -> None:
        """Note one recorded participant fragment and wake anything waiting for it."""
        async with self._changed:
            self._until_ms = max(self._until_ms, end_ms)
            self._changed.notify_all()

    def give_up(self, *, until_ms: int | None) -> None:
        """Treat what was said up to `until_ms` as transcribed: the rest is not coming."""
        if until_ms is not None:
            self._until_ms = max(self._until_ms, until_ms)

    def missing_ms(self, *, spoke_until: int | None) -> int:
        """How much of what the participant said up to `spoke_until` is untranscribed."""
        if spoke_until is None:
            return 0
        gap = spoke_until - self._until_ms
        return gap if gap > _COMPLETE_WITHIN_MS else 0

    async def wait_complete(self, *, spoke_until: int | None, timeout: float) -> int:
        """Wait for the rest of what was said; return how much is still missing then."""
        try:
            async with asyncio.timeout(timeout), self._changed:
                await self._changed.wait_for(
                    lambda: self.missing_ms(spoke_until=spoke_until) == 0
                )
        except TimeoutError:
            return self.missing_ms(spoke_until=spoke_until)
        return 0
