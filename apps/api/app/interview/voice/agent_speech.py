"""Knowing when the agent has said more, so a judgement can wait for its words.

GPT-Live announces a delegation before the words it speaks while delegating are
transcribed: "Let me show you the segment filter" arrives a few hundred
milliseconds after the delegation does. A judgement that needs those words waits
here for the next of them instead of polling.
"""

import asyncio

__all__ = ["AgentSpeech"]


class AgentSpeech:
    """Counts the agent's transcript fragments as the sideband records them."""

    def __init__(self) -> None:
        self._heard = 0
        self._changed = asyncio.Condition()

    @property
    def heard(self) -> int:
        """How many agent fragments have been recorded so far."""
        return self._heard

    async def said(self) -> None:
        """Note one more recorded agent fragment and wake anything waiting for it."""
        async with self._changed:
            self._heard += 1
            self._changed.notify_all()

    async def wait_beyond(self, heard: int, *, timeout: float) -> bool:
        """Wait until more than ``heard`` fragments are recorded; False if time ran out."""
        try:
            async with asyncio.timeout(timeout):
                async with self._changed:
                    await self._changed.wait_for(lambda: self._heard > heard)
        except TimeoutError:
            return False
        return True
