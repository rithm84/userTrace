"""The Server SDK control channel.

``WS /v1/server-sdk/control`` carries activation, credential rotation, health,
pause, resume, and termination for one prepared-preview service process. The
handshake presents the one-time bootstrap credential the prepared-environment
runtime injected; nothing else on this socket is product telemetry.

The companion HTTP route is how the control plane puts a pause, resume, or
terminate on a live stream. It is a service-to-service route: the session
controller that owns a Pause has no socket of its own, and a participant never
reaches this surface.

Contract: docs/arch-components/server-sdk.md#configuration-identity-and-credentials
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, WebSocket
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import CurrentDatabase, FrontDoorService
from app.db.pool import Database
from app.ingestion.control_channel import ServerControlSession
from app.ingestion.control_commands import send_control_command
from app.ingestion.control_hub import require_control_hub
from app.logging import get_logger

__all__ = ["router"]

_log = get_logger(__name__)

router = APIRouter(prefix="/v1/server-sdk", tags=["ingestion"])


def _websocket_database(websocket: WebSocket) -> Database:
    """Return the process-wide connector for a WebSocket route."""
    database = websocket.app.state.database
    if not isinstance(database, Database):
        raise TypeError("the application was assembled without a database connector")
    return database


def _websocket_lane_key(websocket: WebSocket) -> str:
    """Return the lane signing key the bootstrap credential is verified with."""
    settings = websocket.app.state.settings
    signing_key = getattr(settings, "lane_signing_key", None)
    if not isinstance(signing_key, str):
        raise TypeError("the application was assembled without settings")
    return signing_key


@router.websocket("/control")
async def server_sdk_control(websocket: WebSocket) -> None:
    """Carry the Server SDK's activation and control protocol."""
    session = ServerControlSession(
        websocket,
        database=_websocket_database(websocket),
        hub=require_control_hub(websocket.app),
        lane_signing_key=_websocket_lane_key(websocket),
    )
    await session.run()


class ControlCommandRequest(BaseModel):
    """One pause, resume, or terminate for a live server stream."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["utrace-server-control-command-request-v1"]
    activation_id: UUID
    kind: Literal["pause", "resume", "terminate"]
    reason: str = Field(min_length=1, max_length=64)


class ControlCommandResponse(BaseModel):
    """The recorded command's identity, which the SDK echoes in its ack."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["utrace-server-control-command-v1"] = (
        "utrace-server-control-command-v1"
    )
    command_id: UUID


@router.post("/control-commands", response_model=ControlCommandResponse)
async def issue_control_command(
    request: Request,
    service: FrontDoorService,
    database: CurrentDatabase,
    body: ControlCommandRequest,
) -> ControlCommandResponse:
    """Record and deliver one control command on the named activation's stream."""
    command_id = await send_control_command(
        database,
        hub=require_control_hub(request.app),
        activation_id=body.activation_id,
        kind=body.kind,
        reason=body.reason,
    )
    _log.info(
        "server_control_command_requested",
        activation_id=str(body.activation_id),
        kind=body.kind,
        command_id=str(command_id),
    )
    return ControlCommandResponse(command_id=command_id)
