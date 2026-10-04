"""The single "not yet wired" helper.

A route that exists and is authorized but whose behaviour belongs to a later
workstream fails explicitly, naming the capability and its owner. It never
returns a plausible stub: a silent stub would let a caller build against
behaviour that does not exist.
"""

from fastapi import WebSocket

from app.errors import NotYetWiredError
from app.logging import get_logger

__all__ = ["close_unwired_socket"]

_log = get_logger(__name__)

_POLICY_VIOLATION = 1011


async def close_unwired_socket(
    websocket: WebSocket, *, capability: str, owning_workstream: str
) -> None:
    """Accept, report the unwired capability once, and close the socket."""
    error = NotYetWiredError(
        capability=capability, owning_workstream=owning_workstream
    )
    await websocket.accept()
    await websocket.send_json(
        {
            "error": type(error).__name__,
            "detail": str(error),
            "capability": capability,
            "owning_workstream": owning_workstream,
        }
    )
    _log.info(
        "websocket_not_yet_wired",
        path=websocket.url.path,
        capability=capability,
        owning_workstream=owning_workstream,
    )
    await websocket.close(code=_POLICY_VIOLATION, reason="not yet wired")
