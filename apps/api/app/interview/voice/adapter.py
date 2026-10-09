"""The Daily adapter behind the kernel's voice port.

FastAPI validates the originating user, the current Interview Session, and the
connection revision before a request reaches this adapter. Each connection gets
its own private Daily room; the participant's browser receives the room's URL and
a meeting token that admits it to that room only, and the voice agent, started for
the connection's Job (`start_agent`), joins the same room with its own token. The
Daily API key never leaves the backend, and neither token is logged.

The agent runs on Pipecat Cloud (`app.connectors.pipecat_cloud`); a local run of
the API runs it in its own process instead (`VoiceAgentRuntime.IN_PROCESS`).
"""

from datetime import timedelta
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from app.connectors.daily import DAILY_API_BASE_URL, DailyRooms
from app.connectors.pipecat_cloud import PIPECAT_CLOUD_PUBLIC_API_URL, PipecatCloudAgents
from app.interview.settings import VoiceAgentRuntime, VoiceSettings, get_pipecat_cloud_settings
from app.logging import get_logger
from app.ports.voice import VoiceConnectionRequest, VoiceConnectionResult, VoiceRejoinRequest

__all__ = ["DailyVoiceAdapter", "VoiceAgents", "build_daily_rooms", "build_voice_adapter"]

_log = get_logger(__name__)
_PARTICIPANT_NAME = "participant"


class VoiceAgents(Protocol):
    """Where the voice agent for one connection's Job is started."""

    async def start(self, *, voice_agent_job_id: UUID) -> None:
        """Start an agent session that claims and runs the named Job."""
        ...

    async def aclose(self) -> None:
        """Release what starting sessions holds open."""
        ...


class DailyVoiceAdapter:
    """Implements `app.ports.voice.VoicePort` over Daily rooms and an agent host."""

    def __init__(
        self,
        *,
        rooms: DailyRooms,
        room_lifetime: timedelta,
        agents: VoiceAgents,
    ) -> None:
        self._rooms = rooms
        self._room_lifetime = room_lifetime
        self._agents = agents

    async def __aenter__(self) -> Self:
        """Enter the connectors' HTTP scopes."""
        await self._rooms.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the connectors' HTTP transports."""
        await self._rooms.__aexit__(exc_type, exc, traceback)
        await self._agents.aclose()

    async def start_agent(self, *, voice_agent_job_id: UUID) -> None:
        """Start the voice agent for a connection's newly queued Job."""
        await self._agents.start(voice_agent_job_id=voice_agent_job_id)

    @property
    def rooms(self) -> DailyRooms:
        """The Daily connector this adapter creates and rejoins rooms through."""
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


def build_daily_rooms(*, settings: VoiceSettings) -> DailyRooms:
    """The connector over the configured Daily account."""
    return DailyRooms(
        api_key=settings.daily_api_key,
        base_url=DAILY_API_BASE_URL,
        request_timeout_seconds=settings.request_timeout_seconds,
    )


def build_voice_adapter(*, settings: VoiceSettings) -> DailyVoiceAdapter:
    """Build the adapter over the configured Daily account and agent runtime."""
    rooms = build_daily_rooms(settings=settings)
    lifetime = timedelta(seconds=settings.room_lifetime_seconds)
    if settings.agent_runtime is VoiceAgentRuntime.PIPECAT_CLOUD:
        cloud = get_pipecat_cloud_settings()
        return DailyVoiceAdapter(
            rooms=rooms,
            room_lifetime=lifetime,
            agents=PipecatCloudAgents(
                api_key=cloud.public_api_key,
                agent_name=cloud.agent_name,
                base_url=PIPECAT_CLOUD_PUBLIC_API_URL,
                request_timeout_seconds=settings.request_timeout_seconds,
            ),
        )
    # Imported here so the deployed API, which starts agents on Pipecat Cloud, never
    # loads the agent's pipeline.
    from app.interview.voice.cloud_session import InProcessVoiceAgents

    return DailyVoiceAdapter(rooms=rooms, room_lifetime=lifetime, agents=InProcessVoiceAgents())
