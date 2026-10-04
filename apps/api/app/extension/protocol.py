"""Runtime validation of the frames the extension sends.

These models mirror `packages/utrace-contracts/schemas/extension-protocol/`.
Every boundary uses strict versioned schemas: a frame that does not validate is
refused rather than partially interpreted, because the socket is the one place
an installed client reaches control-plane authority.

Nothing here decides authority. The models say what a frame *is*; the binding,
readiness, command, and control modules say what the control plane does with it.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

__all__ = [
    "SCHEMA_VERSION",
    "BindingOfferFrame",
    "CommandResultFrame",
    "ControlAckFrame",
    "ControlActionFrame",
    "HeartbeatFrame",
    "OutboundFrame",
    "PageProof",
    "PageProofClaims",
    "PreferencesUpdateFrame",
    "ProtocolError",
    "ReadinessReportFrame",
    "RecoveryRequestFrame",
    "TabLocator",
    "TargetReference",
    "parse_outbound_frame",
]

SCHEMA_VERSION = 1

_PROTOCOL_VERSION = r"^1\.[0-9]{1,3}$"
_EXTENSION_VERSION = r"^[0-9]+(\.[0-9]+){0,3}$"
_REASON_CODE = r"^[a-z][a-z0-9_]{2,63}$"
_OPAQUE_ID = r"^[A-Za-z0-9_.:-]{1,128}$"
_BASE64URL = r"^[A-Za-z0-9_-]{16,4096}$"
_ORIGIN = r"^https?://[^/?#]+$"

def _require_uuid7(value: UUID) -> UUID:
    """Every first-class uTrace identifier is a UUIDv7, so version 4 is refused."""
    if value.version != 7:
        raise ValueError(f"expected a UUIDv7 identifier, got version {value.version}")
    return value


type Uuid7 = Annotated[UUID, AfterValidator(_require_uuid7)]
type ProtocolVersion = Annotated[str, Field(pattern=_PROTOCOL_VERSION)]
type ExtensionVersion = Annotated[str, Field(pattern=_EXTENSION_VERSION)]
type ReasonCode = Annotated[str, Field(pattern=_REASON_CODE)]
type OpaqueId = Annotated[str, Field(pattern=_OPAQUE_ID)]
type Base64Url = Annotated[str, Field(pattern=_BASE64URL)]
type Origin = Annotated[str, Field(pattern=_ORIGIN)]
type Revision = Annotated[int, Field(ge=0)]
type Sequence1 = Annotated[int, Field(ge=1)]


class ProtocolError(ValueError):
    """A frame the control socket cannot accept.

    ``reason`` is the closed code the socket reports; ``detail`` is redacted
    actionable text for an operator. Neither carries customer content.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


class _Frame(BaseModel):
    """Immutable, closed base for every extension-protocol model."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class TabLocator(_Frame):
    """A Chrome-local locator. It is never a durable product identity."""

    tab_id: Annotated[int, Field(ge=0)]
    frame_id: Literal[0]


class TargetReference(_Frame):
    """One registered structured target at an exact revision."""

    target_id: OpaqueId
    target_revision: Revision


class PageProofClaims(_Frame):
    """The claim set inside a page proof's signed payload.

    The extension decodes it locally to match the document before forwarding.
    It is never authority on its own: the control plane verifies the signature
    over the exact payload bytes and reads the claims from there.
    """

    client_id: Uuid7
    session_id: Uuid7
    preview_environment_id: Uuid7
    preview_runtime_allocation_id: Uuid7
    origin: Origin
    document_attachment_id: Uuid7
    document_nonce: OpaqueId
    sdk_protocol_version: ProtocolVersion
    issued_at: datetime
    expires_at: datetime


class PageProof(_Frame):
    """The embedded SDK's signed document proof, forwarded exactly as issued.

    A detached Ed25519 signature over base64url JSON payload bytes, which is the
    `signed_envelope` shape telemetry ingestion issues at document activation.
    """

    algorithm: Literal["ed25519"]
    key_id: OpaqueId
    payload_b64url: Annotated[
        str, Field(min_length=16, max_length=8192, pattern=r"^[A-Za-z0-9_-]+$")
    ]
    signature_b64url: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{86}$")]


class BindingOfferPayloadOffer(_Frame):
    """The single-use offer the session tab obtained from the control plane."""

    schema_version: Literal[1]
    offer_token: Base64Url
    session_id: Uuid7
    client_id: Uuid7
    extension_installation_id: Uuid7
    preview_runtime_allocation_id: Uuid7
    protocol_version: ProtocolVersion
    expires_at: datetime


class OfferRequest(_Frame):
    """A first binding consumes the session tab's single-use offer."""

    kind: Literal["offer"]
    offer: BindingOfferPayloadOffer


class RebindRequest(_Frame):
    """Focusing another valid tab transfers the existing binding instead."""

    kind: Literal["rebind"]
    session_id: Uuid7
    extension_binding_id: Uuid7


type BindingRequest = Annotated[OfferRequest | RebindRequest, Field(discriminator="kind")]


class BindingOfferPayload(_Frame):
    """Everything one binding request carries."""

    request: BindingRequest
    page_proof: PageProof
    tab_locator: TabLocator
    document_origin: Origin


class BindingOfferFrame(_Frame):
    """`binding_offer`: bind one preview document to this Interview Session."""

    schema_version: Literal[1]
    kind: Literal["binding_offer"]
    message_id: Uuid7
    sent_at: datetime
    payload: BindingOfferPayload


class CommandResultPayload(_Frame):
    """One canonical terminal outcome for one visual Command."""

    command_id: Uuid7
    outcome: Literal[
        "applied", "rejected", "failed", "stale_target", "canceled", "timed_out"
    ]
    reason: ReasonCode | None = None
    extension_binding_id: Uuid7
    document_attachment_id: Uuid7
    observed_targets: Annotated[Sequence[TargetReference], Field(max_length=12)]
    completed_at: datetime


class CommandResultFrame(_Frame):
    """`command_result`: what the extension observed when it ran a command."""

    schema_version: Literal[1]
    kind: Literal["command_result"]
    message_id: Uuid7
    sent_at: datetime
    payload: CommandResultPayload


class ControlActionPayload(_Frame):
    """A Mute, Unmute, Pause, Resume, or End request from the orb's pills."""

    action_id: Uuid7
    session_id: Uuid7
    action: Literal["mute", "unmute", "pause", "resume", "end"]
    expected_session_control_revision: Revision
    requested_at: datetime
    locally_confirmed: bool


class ControlActionFrame(_Frame):
    """`control_action`: Return to session is browser-local and never appears here."""

    schema_version: Literal[1]
    kind: Literal["control_action"]
    message_id: Uuid7
    sent_at: datetime
    payload: ControlActionPayload


class RecoveryRequestPayload(_Frame):
    """The resume point and the commands whose outcome is still unknown."""

    installation_id: Uuid7
    protocol_version: ProtocolVersion
    extension_version: ExtensionVersion
    resume_from_sequence: Revision
    session_id: Uuid7 | None = None
    pending_command_ids: Annotated[Sequence[UUID], Field(max_length=32)]


class RecoveryRequestFrame(_Frame):
    """`recovery_request`: sent first on every connection."""

    schema_version: Literal[1]
    kind: Literal["recovery_request"]
    message_id: Uuid7
    sent_at: datetime
    payload: RecoveryRequestPayload


class ReadinessReportPayload(_Frame):
    """A local fact that affects readiness. The control plane still owns it."""

    session_id: Uuid7
    report: Literal["withdrawn", "restored"]
    reason: ReasonCode
    document_attachment_id: Uuid7 | None = None
    observed_at: datetime


class ReadinessReportFrame(_Frame):
    """`readiness_report`: a removed host, a lost bridge, a withdrawn permission."""

    schema_version: Literal[1]
    kind: Literal["readiness_report"]
    message_id: Uuid7
    sent_at: datetime
    payload: ReadinessReportPayload


class PreferencesUpdatePayload(_Frame):
    """A requested change to the control-plane-owned action preferences."""

    expected_preferences_revision: Revision
    visual_guidance_enabled: bool
    agent_cursor_enabled: bool


class PreferencesUpdateFrame(_Frame):
    """`preferences_update`: the settings page asks; the control plane decides."""

    schema_version: Literal[1]
    kind: Literal["preferences_update"]
    message_id: Uuid7
    sent_at: datetime
    payload: PreferencesUpdatePayload


class ControlAckPayload(_Frame):
    """The highest contiguous control sequence the extension processed."""

    acknowledged_sequence: Sequence1


class ControlAckFrame(_Frame):
    """`control_ack`: reconnection resumes from this sequence."""

    schema_version: Literal[1]
    kind: Literal["control_ack"]
    message_id: Uuid7
    sent_at: datetime
    payload: ControlAckPayload


class HeartbeatPayload(_Frame):
    """The unsequenced keepalive's acknowledgement position."""

    acknowledged_sequence: Revision


class HeartbeatFrame(_Frame):
    """`heartbeat`: both directions, unsequenced."""

    schema_version: Literal[1]
    kind: Literal["heartbeat"]
    message_id: Uuid7
    sent_at: datetime
    payload: HeartbeatPayload


type OutboundFrame = Annotated[
    BindingOfferFrame
    | CommandResultFrame
    | ControlActionFrame
    | RecoveryRequestFrame
    | ReadinessReportFrame
    | PreferencesUpdateFrame
    | ControlAckFrame
    | HeartbeatFrame,
    Field(discriminator="kind"),
]


class _OutboundAdapter(BaseModel):
    """Single-field wrapper so the discriminated union validates in one place."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    frame: OutboundFrame


def parse_outbound_frame(payload: object) -> OutboundFrame:
    """Validate one frame the extension sent, or refuse it with a closed reason."""
    try:
        return _OutboundAdapter.model_validate({"frame": payload}).frame
    except ValidationError as error:
        raise ProtocolError(
            "invalid_outbound_frame",
            f"the extension sent a frame with {error.error_count()} validation errors",
        ) from error
