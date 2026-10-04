"""Reaching one live Server SDK control socket.

A control WebSocket is connection-affine while it is open: the process holding
it is the only one that can put a frame on it. The hub is that process's index
of the sockets it holds, keyed by ``server_stream_id``.

It holds no durable truth. The command row is written before delivery and the
acknowledgement is written when the SDK answers; the hub only decides which
socket a command can go out on. A command for a stream this process does not
hold is refused rather than dropped, because a silently dropped pause would
leave a paused case still exporting.

Contract: docs/arch-components/telemetry-and-evidence.md#ingress-topology-and-ownership
"""

from typing import Protocol
from uuid import UUID

from fastapi import FastAPI

from app.ingestion.control_protocol import ServerMessage
from app.ingestion.errors import IntegrityViolationError
from app.logging import get_logger

__all__ = ["ControlConnection", "ServerControlHub", "require_control_hub"]

_log = get_logger(__name__)

_ATTRIBUTE = "server_control_hub"


class ControlConnection(Protocol):
    """One open control socket, as the hub needs to use it.

    Delivery is the whole surface. A terminate is a frame like any other: the
    SDK flushes, sends its terminal stream report, and closes its own socket, so
    the control plane never closes one out from under it.
    """

    async def deliver(self, message: ServerMessage) -> None:
        """Send one control frame over this socket."""
        ...


class ServerControlHub:
    """The control sockets one process currently holds.

    A stream keeps a list rather than a single entry, in registration order. A
    reconnect arrives before the previous socket has always noticed it is gone,
    so delivery uses the newest live connection and a closing socket removes only
    itself. A single well-behaved service has exactly one entry throughout.
    """

    def __init__(self) -> None:
        self._connections: dict[UUID, list[ControlConnection]] = {}

    def register(self, server_stream_id: UUID, connection: ControlConnection) -> None:
        """Index one open socket as the stream's newest control connection."""
        live = self._connections.setdefault(server_stream_id, [])
        if connection in live:
            live.remove(connection)
        live.append(connection)

    def unregister(
        self, server_stream_id: UUID, connection: ControlConnection
    ) -> None:
        """Forget one socket once it has closed, leaving any others in place."""
        live = self._connections.get(server_stream_id)
        if live is None:
            return
        if connection in live:
            live.remove(connection)
        if len(live) == 0:
            del self._connections[server_stream_id]

    def holds(self, server_stream_id: UUID) -> bool:
        """Whether this process currently holds a socket for that stream."""
        return server_stream_id in self._connections

    async def deliver(self, server_stream_id: UUID, message: ServerMessage) -> None:
        """Put one command on the stream's newest socket, or refuse with it named."""
        live = self._connections.get(server_stream_id)
        connection = live[-1] if live is not None and len(live) > 0 else None
        if connection is None:
            raise IntegrityViolationError(
                identity_kind="server_stream_id",
                identity_value=str(server_stream_id),
                reason="has no control connection on this control-plane instance",
            )
        await connection.deliver(message)
        _log.info(
            "server_control_command_delivered",
            server_stream_id=str(server_stream_id),
            message_type=message.type,
        )


def require_control_hub(application: FastAPI) -> ServerControlHub:
    """Return the process's control hub, creating it on first use."""
    cached = getattr(application.state, _ATTRIBUTE, None)
    if isinstance(cached, ServerControlHub):
        return cached
    hub = ServerControlHub()
    setattr(application.state, _ATTRIBUTE, hub)
    return hub
