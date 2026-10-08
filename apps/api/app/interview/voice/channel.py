"""How the application talks to the voice agent, and who has the floor.

The agent's conversation is a text model's context: the application adds to it in
`<application>` blocks, which the agent's instructions describe. `say` adds a block
and has the agent speak from it now; `note` adds one the agent uses later without
speaking. Neither interrupts the agent: callers that must not talk over it wait for
`Floor.agent_quiet` first.
"""

import asyncio
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

__all__ = ["AgentChannel", "Floor", "VoiceSubject"]


@dataclass(frozen=True, slots=True)
class VoiceSubject:
    """The connection one voice agent run serves."""

    client_id: UUID
    product_signal_id: UUID
    interview_session_id: UUID
    voice_connection_id: UUID


class AgentChannel(Protocol):
    """What the application may do to the agent's conversation."""

    async def say(self, content: str) -> None:
        """Add `content` to the conversation and have the agent speak from it now."""
        ...

    async def note(self, content: str) -> None:
        """Add `content` to the conversation for the agent to use when it next speaks."""
        ...

    async def set_input_muted(self, *, muted: bool) -> None:
        """Stop or resume hearing the participant, for the Pause control."""
        ...


@dataclass(frozen=True, slots=True)
class Floor:
    """Who has the floor: each event is set while that party does not hold it.

    The agent holds it from a request to speak, or the start of its answer, until
    that answer's audio stops playing, and while one of its tools runs; the
    participant holds it from the start of their turn until its end.
    """

    agent_quiet: asyncio.Event
    participant_quiet: asyncio.Event

    def free(self) -> bool:
        """Whether nobody has the floor right now."""
        return self.agent_quiet.is_set() and self.participant_quiet.is_set()
