"""The two response shapes the preview front door accepts.

The door validates every answer against a published contract: a 200 must match
its schema, and a 400, 401, 403, 404, 409 or 410 must be a typed preview denial.
Anything else is a contract violation and the door shows the participant the
"uTrace is broken" surface, so these two helpers are the only way this area
answers the door.

A denial never explains itself further than its reason code. The door decides
which surface a participant sees from that code alone, and a longer message would
be a description of another session's state on a public surface.
"""

from collections.abc import Mapping
from typing import Literal

from fastapi.responses import JSONResponse

__all__ = ["DenialReason", "preview_denial", "preview_json"]

DENIAL_SCHEMA_VERSION = "utrace_preview_denial_v1"

type DenialReason = Literal[
    "unknown_host",
    "unknown_handoff_code",
    "handoff_consumed",
    "handoff_expired",
    "host_mismatch",
    "unauthorized_session",
    "session_ended",
    "route_expired",
    "activation_rejected",
]

_NO_STORE: Mapping[str, str] = {"cache-control": "no-store"}


def preview_json(*, status: int, payload: Mapping[str, object]) -> JSONResponse:
    """Return one contract-shaped answer to the front door."""
    return JSONResponse(status_code=status, content=dict(payload), headers=dict(_NO_STORE))


def preview_denial(*, status: int, reason: DenialReason) -> JSONResponse:
    """Return one typed refusal the front door can map to a participant surface."""
    return JSONResponse(
        status_code=status,
        content={"schema_version": DENIAL_SCHEMA_VERSION, "reason": reason},
        headers=dict(_NO_STORE),
    )
