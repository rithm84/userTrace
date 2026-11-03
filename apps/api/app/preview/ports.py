"""The narrow port the preview area consumes for observation readiness.

Whether the Server SDK inside a preview runtime activated, and whether the
browser, server, and correlation observation paths are ready, is not something
this area can observe. The preview area installs the runtime's bootstrap
credential when it claims the allocation (``app.preview.activation``) and
restarts the runtime; the runtime's own SDK then opens the control socket and
creates the activation. The telemetry-ingestion stream is what sees that
handshake and maintains the readiness projections, and this port is the only way
the preview readiness runner learns either answer.

Only ``server`` can be true at the moment a route is committed: the browser and
correlation answers need the participant's own traffic, which cannot arrive
before the route that carries it exists.
"""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

__all__ = [
    "ObservationReadinessPort",
    "ServerSdkActivation",
    "TelemetryReadiness",
]


class ServerSdkActivation(BaseModel):
    """The activation the runtime's own Server SDK committed, as observed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    activated: bool
    service_name: str
    manifest_trust_anchor_key_id: str
    sdk_version: str
    activated_at: datetime | None
    preview_entry_token: str | None
    """The token the front door attaches as ``x-utrace-preview-entry``.

    Present once the runtime's Server SDK has activated; ``None`` while it has
    not, because a route record must not carry an entry token nobody issued.
    """
    detail: str
    """Why activation is incomplete, when ``activated`` is false. Empty otherwise."""


class TelemetryReadiness(BaseModel):
    """The ingestion stream's readiness projection for one Interview Session.

    The three kinds are the ones ``telemetry_readiness_projections`` records:
    the embedded browser SDK's observation path, the Server SDK's span path, and
    the browser-to-server trace correlation that joins them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    browser_ready: bool
    server_ready: bool
    correlation_ready: bool
    observed_at: datetime
    detail: str
    """Why a kind is not ready. Empty when all three are."""


class ObservationReadinessPort(Protocol):
    """Server SDK activation and telemetry readiness for one preview runtime."""

    async def activation(
        self,
        *,
        client_id: UUID,
        interview_session_id: UUID,
        preview_runtime_allocation_id: UUID,
    ) -> ServerSdkActivation:
        """Return the activation the runtime committed, or raise a typed failure.

        An allocation whose SDK has not activated is reported as
        ``activated=false`` with the reason. It is never an exception: not yet
        activated is an ordinary state of a runtime that has just restarted.
        """
        ...

    async def readiness(
        self, *, client_id: UUID, interview_session_id: UUID
    ) -> TelemetryReadiness:
        ...
