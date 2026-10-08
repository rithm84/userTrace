"""Voice session port.

The control plane authorizes a voice connection and binds the call the adapter
creates to the connection's ``voice_run_id``. The adapter holds the provider
credential; the browser receives only what admits it to its own call.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

__all__ = ["VoiceConnectionRequest", "VoiceConnectionResult", "VoicePort"]


@dataclass(frozen=True, slots=True)
class VoiceConnectionRequest:
    """An authorized request to open one voice call."""

    client_id: UUID
    interview_session_id: UUID
    voice_run_id: UUID
    participant_connection_id: UUID


@dataclass(frozen=True, slots=True)
class VoiceConnectionResult:
    """What the adapter returns once the call exists."""

    room_url: str
    room_expires_at: datetime
    participant_token: str
    """Admits the participant's browser to this call only, until the call expires."""
    provider_session_ref: str


class VoicePort(Protocol):
    """Opens the call a voice connection runs in."""

    async def open_connection(self, request: VoiceConnectionRequest) -> VoiceConnectionResult:
        """Create the call and return what admits the participant to it."""
        ...
