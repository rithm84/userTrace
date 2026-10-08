"""The Daily adapter behind the kernel's voice port.

FastAPI validates the originating user, the current Interview Session, and the
connection revision before a request reaches this adapter. Each connection gets
its own private Daily room; the participant's browser receives the room's URL and
a meeting token that admits it to that room only, and the agent joins the same
room from the worker with its own token. The Daily API key never leaves the
backend, and neither token is logged.
"""

from datetime import timedelta
from types import TracebackType
from typing import Self

from app.connectors.daily import DAILY_API_BASE_URL, DailyRooms
from app.interview.settings import VoiceSettings
from app.logging import get_logger
from app.ports.voice import VoiceConnectionRequest, VoiceConnectionResult, VoiceRejoinRequest

__all__ = ["DailyVoiceAdapter", "build_voice_adapter"]

_log = get_logger(__name__)
_PARTICIPANT_NAME = "participant"


class DailyVoiceAdapter:
    """Implements `app.ports.voice.VoicePort` over Daily rooms."""

    def __init__(self, *, rooms: DailyRooms, room_lifetime: timedelta) -> None:
        self._rooms = rooms
        self._room_lifetime = room_lifetime

    async def __aenter__(self) -> Self:
        """Enter the connector's HTTP scope."""
        await self._rooms.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the connector's HTTP transport."""
        await self._rooms.__aexit__(exc_type, exc, traceback)

    @property
    def rooms(self) -> DailyRooms:
        """The connector the voice agent joins and the cleanup Job deletes rooms with."""
        return self._rooms

    async def open_connection(self, request: VoiceConnectionRequest) -> VoiceConnectionResult:
        """Create the connection's room and the participant's token for it."""
        room = await self._rooms.create_room(lifetime=self._room_lifetime)
        token = await self._rooms.meeting_token(
            room_name=room.name,
            expires_at=room.expires_at,
            user_name=_PARTICIPANT_NAME,
            user_id=str(request.participant_user_id),
            owner=False,
        )
        _log.info(
            "voice_room_created",
            client_id=str(request.client_id),
            interview_session_id=str(request.interview_session_id),
            voice_run_id=str(request.voice_run_id),
            participant_connection_id=str(request.participant_connection_id),
            provider_session_ref=room.name,
        )
        return VoiceConnectionResult(
            room_url=room.url,
            room_expires_at=room.expires_at,
            participant_token=token,
            provider_session_ref=room.name,
        )

    async def rejoin_connection(self, request: VoiceRejoinRequest) -> str:
        """Remove the participant's stale browser from the room and admit them again."""
        user_id = str(request.participant_user_id)
        await self._rooms.eject(room_name=request.provider_session_ref, user_id=user_id)
        token = await self._rooms.meeting_token(
            room_name=request.provider_session_ref,
            expires_at=request.room_expires_at,
            user_name=_PARTICIPANT_NAME,
            user_id=user_id,
            owner=False,
        )
        _log.info(
            "voice_room_rejoined",
            client_id=str(request.client_id),
            interview_session_id=str(request.interview_session_id),
            provider_session_ref=request.provider_session_ref,
        )
        return token


def build_voice_adapter(*, settings: VoiceSettings) -> DailyVoiceAdapter:
    """Build the adapter over the configured Daily account."""
    return DailyVoiceAdapter(
        rooms=DailyRooms(
            api_key=settings.daily_api_key,
            base_url=DAILY_API_BASE_URL,
            request_timeout_seconds=settings.request_timeout_seconds,
        ),
        room_lifetime=timedelta(seconds=settings.room_lifetime_seconds),
    )
