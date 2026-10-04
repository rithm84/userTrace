"""Pause, resume, and terminate on a live Server SDK stream.

Pause stops session-correlated export; resume restores it and readiness returns
only on fresh evidence; terminate ends the stream after a bounded flush and a
terminal stream report. Each one is recorded before it is delivered and carries
its own command ID, which the SDK echoes in its ``command_ack``.

The command reaches the control-plane instance holding that stream's socket. A
stream with no socket on this instance is refused with the stream named, because
a pause that was never delivered must not look applied.

Contract: docs/arch-components/server-sdk.md#readiness-health-and-failure
"""

from typing import Literal
from uuid import UUID

from app.db.pool import Database
from app.ingestion.control_hub import ServerControlHub
from app.ingestion.control_protocol import (
    PauseMessage,
    PauseReason,
    ResumeMessage,
    ServerMessage,
    TerminateMessage,
    TerminateReason,
)
from app.ingestion.server_activation import load_server_activation
from app.ingestion.server_health import mark_command_delivered, record_control_command
from app.logging import get_logger

__all__ = ["ControlCommandKind", "send_control_command"]

_log = get_logger(__name__)

type ControlCommandKind = Literal["pause", "resume", "terminate"]


def _frame(
    *, kind: ControlCommandKind, command_id: UUID, reason: str
) -> ServerMessage:
    """Build the control frame for one command kind and its reason."""
    match kind:
        case "pause":
            return PauseMessage(
                command_id=command_id, reason=_pause_reason(reason)
            )
        case "resume":
            return ResumeMessage(command_id=command_id)
        case "terminate":
            return TerminateMessage(
                command_id=command_id, reason=_terminate_reason(reason)
            )


def _pause_reason(reason: str) -> PauseReason:
    """Validate a pause reason against the closed set the contract defines."""
    allowed: tuple[PauseReason, ...] = (
        "session_paused",
        "policy_hold",
        "ingestion_backpressure",
        "operator_stop",
    )
    for candidate in allowed:
        if candidate == reason:
            return candidate
    raise ValueError(f"'{reason}' is not a pause reason")


def _terminate_reason(reason: str) -> TerminateReason:
    """Validate a terminate reason against the closed set the contract defines."""
    allowed: tuple[TerminateReason, ...] = (
        "session_ended",
        "activation_superseded",
        "credential_revoked",
        "client_case_stop",
    )
    for candidate in allowed:
        if candidate == reason:
            return candidate
    raise ValueError(f"'{reason}' is not a terminate reason")


async def send_control_command(
    database: Database,
    *,
    hub: ServerControlHub,
    activation_id: UUID,
    kind: ControlCommandKind,
    reason: str,
) -> UUID:
    """Record and deliver one control command, and return its command ID."""
    activation = await load_server_activation(database, activation_id=activation_id)
    async with database.transaction() as conn:
        command_id = await record_control_command(
            conn,
            client_id=activation.client_id,
            activation_id=activation.activation_id,
            server_stream_id=activation.server_stream_id,
            kind=kind,
            reason=reason,
        )
    await hub.deliver(
        activation.server_stream_id,
        _frame(kind=kind, command_id=command_id, reason=reason),
    )
    await mark_command_delivered(database, command_id=command_id)
    _log.info(
        "server_control_command_sent",
        client_id=str(activation.client_id),
        activation_id=str(activation_id),
        server_stream_id=str(activation.server_stream_id),
        kind=kind,
        reason=reason,
    )
    return command_id
