"""An `AgentChannel` that records what the application tells the voice agent.

The cue and feed tests run the production cue functions against it: what the agent
is told, and when, is what they check. A live call's agent is covered by the opt-in
realtime tests (`realtime_harness`).
"""

import asyncio

__all__ = ["RecordingAgent"]


class RecordingAgent:
    """Implements `app.interview.voice.channel.AgentChannel` by recording each call."""

    def __init__(self) -> None:
        self.said: list[str] = []
        self.noted: list[str] = []
        self.muted: list[bool] = []
        self.spoke = asyncio.Event()
        self.told = asyncio.Event()

    async def say(self, content: str) -> None:
        self.said.append(content)
        self.spoke.set()
        self.told.set()

    async def note(self, content: str) -> None:
        self.noted.append(content)
        self.told.set()

    async def set_input_muted(self, *, muted: bool) -> None:
        self.muted.append(muted)
