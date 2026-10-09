"""The frames the control plane sends to the extension.

Control-plane messages are schema-validated data. They carry no code, selectors,
CSS, or expressions for the extension to interpret: a visual command names an
exact registered target revision and a typed gesture, and nothing else is
representable.

Each builder returns the frame's kind and payload. The socket stamps the
envelope, allocates the monotonic sequence, and persists the frame, so sequence
allocation lives in exactly one place.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.envelope import JsonValue
from app.extension.protocol import SCHEMA_VERSION

__all__ = [
    "OutgoingFrame",
    "binding_state",
    "cancel",
    "character_state",
    "control_action_result",
    "controls_state",
    "heartbeat",
    "preferences_state",
    "readiness",
    "recovery_state",
    "visual_command",
]


@dataclass(frozen=True, slots=True)
class OutgoingFrame:
    """One frame body before the socket stamps its envelope and sequence."""

    kind: str
    payload: Mapping[str, JsonValue]
    sequenced: bool

    def envelope(
        self, *, message_id: UUID, sent_at: datetime, sequence: int | None
    ) -> dict[str, JsonValue]:
        """Render the wire frame. An unsequenced frame carries no `sequence`."""
        frame: dict[str, JsonValue] = {
            "schema_version": SCHEMA_VERSION,
            "kind": self.kind,
            "message_id": str(message_id),
            "sent_at": _rfc3339(sent_at),
            "payload": dict(self.payload),
        }
        if self.sequenced:
            if sequence is None:
                raise ValueError(f"frame {self.kind!r} requires a sequence")
            frame["sequence"] = sequence
        return frame


def binding_state(
    *,
    binding_revision: int,
    session_id: UUID,
    status: str,
    client_id: UUID | None,
    extension_binding_id: UUID | None,
    document_attachment_id: UUID | None,
    preview_runtime_allocation_id: UUID | None,
    reason: str | None,
) -> OutgoingFrame:
    """The authoritative binding of one Interview Session to one document."""
    payload: dict[str, JsonValue] = {
        "binding_revision": binding_revision,
        "session_id": str(session_id),
        "status": status,
    }
    if status == "bound":
        if (
            client_id is None
            or extension_binding_id is None
            or document_attachment_id is None
            or preview_runtime_allocation_id is None
        ):
            raise ValueError("a bound binding_state names every bound identity")
        payload["client_id"] = str(client_id)
        payload["extension_binding_id"] = str(extension_binding_id)
        payload["document_attachment_id"] = str(document_attachment_id)
        payload["preview_runtime_allocation_id"] = str(preview_runtime_allocation_id)
    else:
        if reason is None:
            raise ValueError(f"a {status!r} binding_state names its reason")
        payload["reason"] = reason
    return OutgoingFrame(kind="binding_state", payload=payload, sequenced=True)


def readiness(
    *,
    readiness_revision: int,
    session_id: UUID,
    extension_readiness: str,
    combined_readiness: str,
    blockers: Sequence[str],
) -> OutgoingFrame:
    """Readiness is a revisioned control-plane record; the extension renders it."""
    return OutgoingFrame(
        kind="readiness",
        payload={
            "readiness_revision": readiness_revision,
            "session_id": str(session_id),
            "extension_readiness": extension_readiness,
            "combined_readiness": combined_readiness,
            "blockers": list(dict.fromkeys(blockers))[:16],
        },
        sequenced=True,
    )


def character_state(
    *,
    character_revision: int,
    session_id: UUID,
    state: str,
    gaze_x: float,
    gaze_y: float,
    speech_energy: float,
    motion_scale: float,
) -> OutgoingFrame:
    """The semantic character state both surfaces render; older revisions lose."""
    return OutgoingFrame(
        kind="character_state",
        payload={
            "character_revision": character_revision,
            "session_id": str(session_id),
            "state": state,
            "gaze_x": gaze_x,
            "gaze_y": gaze_y,
            "speech_energy": speech_energy,
            "motion_scale": motion_scale,
        },
        sequenced=True,
    )


def controls_state(
    *, session_id: UUID, session_control_revision: int, mute_state: str, pause_state: str
) -> OutgoingFrame:
    """The session's own mute and pause state, which the pills show whoever changed it."""
    return OutgoingFrame(
        kind="controls_state",
        payload={
            "session_id": str(session_id),
            "session_control_revision": session_control_revision,
            "mute_state": mute_state,
            "pause_state": pause_state,
        },
        sequenced=True,
    )


def visual_command(
    *,
    command_id: UUID,
    client_id: UUID,
    session_id: UUID,
    extension_binding_id: UUID,
    document_attachment_id: UUID,
    expected_lifecycle_revision: int,
    deadline_at: datetime,
    command: Mapping[str, JsonValue],
) -> OutgoingFrame:
    """One browser command with its authority references and its deadline."""
    return OutgoingFrame(
        kind="visual_command",
        payload={
            "command_id": str(command_id),
            "client_id": str(client_id),
            "session_id": str(session_id),
            "extension_binding_id": str(extension_binding_id),
            "document_attachment_id": str(document_attachment_id),
            "expected_lifecycle_revision": expected_lifecycle_revision,
            "deadline_at": _rfc3339(deadline_at),
            "command": dict(command),
        },
        sequenced=True,
    )


def cancel(*, command_id: UUID, reason: str) -> OutgoingFrame:
    """Cancel one routed command by its idempotency boundary."""
    return OutgoingFrame(
        kind="cancel",
        payload={"command_id": str(command_id), "reason": reason},
        sequenced=True,
    )


def control_action_result(
    *,
    action_id: UUID,
    session_id: UUID,
    disposition: str,
    session_control_revision: int,
    mute_state: str,
    pause_state: str,
    reason: str | None,
) -> OutgoingFrame:
    """The returned session-control revision the pills reflect."""
    payload: dict[str, JsonValue] = {
        "action_id": str(action_id),
        "session_id": str(session_id),
        "disposition": disposition,
        "session_control_revision": session_control_revision,
        "mute_state": mute_state,
        "pause_state": pause_state,
    }
    if reason is not None:
        payload["reason"] = reason
    return OutgoingFrame(kind="control_action_result", payload=payload, sequenced=True)


def recovery_state(
    *,
    disposition: str,
    next_sequence: int,
    session_id: UUID | None,
    protocol_version: str,
    reason: str | None,
) -> OutgoingFrame:
    """The answer to a resume request: resumed, restarted, or refused."""
    payload: dict[str, JsonValue] = {
        "disposition": disposition,
        "next_sequence": next_sequence,
        "protocol_version": protocol_version,
    }
    if session_id is not None:
        payload["session_id"] = str(session_id)
    if reason is not None:
        payload["reason"] = reason
    return OutgoingFrame(kind="recovery_state", payload=payload, sequenced=True)


def preferences_state(
    *,
    preferences_revision: int,
    visual_guidance_enabled: bool,
    agent_cursor_enabled: bool,
) -> OutgoingFrame:
    """Global action preferences stay control-plane owned."""
    return OutgoingFrame(
        kind="preferences_state",
        payload={
            "preferences_revision": preferences_revision,
            "visual_guidance_enabled": visual_guidance_enabled,
            "agent_cursor_enabled": agent_cursor_enabled,
        },
        sequenced=True,
    )


def heartbeat(*, acknowledged_sequence: int) -> OutgoingFrame:
    """The unsequenced keepalive, sent only while a session needs the socket."""
    return OutgoingFrame(
        kind="heartbeat",
        payload={"acknowledged_sequence": acknowledged_sequence},
        sequenced=False,
    )


def _rfc3339(value: datetime) -> str:
    """Render an instant as the RFC 3339 UTC form the protocol requires."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
