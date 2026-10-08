"""Voice session port.

The control plane authorizes the GPT-Live handshake and binds the returned
provider session to the current ``voice_run_id``. The adapter performs the
OpenAI API call with the server-side API key; the browser never selects a model
or holds a provider credential.
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

__all__ = ["VoiceConnectionRequest", "VoiceConnectionResult", "VoicePort"]


@dataclass(frozen=True, slots=True)
class VoiceConnectionRequest:
    """An authorized request to open one provider voice connection."""

    client_id: UUID
    interview_session_id: UUID
    voice_run_id: UUID
    participant_connection_id: UUID
    sdp_offer: str
    instructions: str
    """The rendered session instructions; uTrace content the adapter passes through."""


@dataclass(frozen=True, slots=True)
class VoiceConnectionResult:
    """What the adapter returns once the provider session exists."""

    sdp_answer: str
    provider_session_ref: str


class VoicePort(Protocol):
    """Implemented by the provider-adapters workstream."""

    async def open_connection(
        self, request: VoiceConnectionRequest
    ) -> VoiceConnectionResult:
        """Create the backend-authorized provider session and return its SDP answer."""
        ...
