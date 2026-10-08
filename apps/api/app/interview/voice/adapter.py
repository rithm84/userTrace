"""The GPT-Live adapter behind the kernel's voice port.

FastAPI validates the originating user, the current Interview Session, and the
connection revision before an SDP offer reaches this adapter. The adapter then
chooses the pinned GPT-Live model, the voice, the session instructions, and
`delegation: {type: "client"}` server-side: browser input selects no endpoint,
model, voice, or privileged session configuration.

The OpenAI API key stays server-side. uTrace participant authentication
authorizes setup rather than authenticating the browser to OpenAI, and the
browser never holds a provider credential.

Session descriptions and transport credentials never enter a log or model
context, so nothing here records an offer or an answer.
"""

from types import TracebackType
from typing import Self

from app.connectors.openai_live import OPENAI_API_BASE_URL, GptLiveSessions
from app.interview.settings import OpenAIVoiceSettings
from app.logging import get_logger
from app.ports.voice import VoiceConnectionRequest, VoiceConnectionResult

__all__ = ["GptLiveVoiceAdapter", "build_voice_adapter"]

_log = get_logger(__name__)

class GptLiveVoiceAdapter:
    """Implements `app.ports.voice.VoicePort` over the GPT-Live connector."""

    def __init__(self, *, sessions: GptLiveSessions, voice: str) -> None:
        self._sessions = sessions
        self._voice = voice

    async def __aenter__(self) -> Self:
        """Enter the connector's HTTP scope."""
        await self._sessions.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the connector's HTTP transport."""
        await self._sessions.__aexit__(exc_type, exc, traceback)

    @property
    def sessions(self) -> GptLiveSessions:
        """The connector the sideband worker attaches and closes sessions with."""
        return self._sessions

    async def open_connection(
        self, request: VoiceConnectionRequest
    ) -> VoiceConnectionResult:
        """Create the backend-authorized provider session and return its answer."""
        handle = await self._sessions.create_session(
            offer_sdp=request.sdp_offer, instructions=request.instructions, voice=self._voice
        )
        _log.info(
            "voice_provider_session_created",
            client_id=str(request.client_id),
            interview_session_id=str(request.interview_session_id),
            voice_run_id=str(request.voice_run_id),
            participant_connection_id=str(request.participant_connection_id),
            provider_session_ref=handle.provider_session_id,
            model=handle.model,
        )
        return VoiceConnectionResult(
            sdp_answer=handle.answer_sdp, provider_session_ref=handle.provider_session_id
        )

def build_voice_adapter(*, settings: OpenAIVoiceSettings) -> GptLiveVoiceAdapter:
    """Build the adapter over the pinned GPT-Live model on the OpenAI API."""
    sessions = GptLiveSessions(
        api_key=settings.api_key,
        base_url=OPENAI_API_BASE_URL,
        model=settings.model,
        request_timeout_seconds=settings.request_timeout_seconds,
    )
    return GptLiveVoiceAdapter(sessions=sessions, voice=settings.voice)
