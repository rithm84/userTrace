"""The `WS /v1/server-sdk/control` message contract.

These models are the Python half of
``packages/utrace-contracts/schemas/server-activation/server-control-client-message.v1``
and ``server-control-server-message.v1``. Every model is closed and strict: an
unknown frame type, an unknown field, or an out-of-range value fails the frame
rather than being ignored, because the control channel is how a prepared preview
proves it can be observed.

Contracts: docs/arch-components/server-sdk.md#configuration-identity-and-credentials
           docs/arch-components/server-sdk.md#readiness-health-and-failure
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

__all__ = [
    "ActivateMessage",
    "ActivatedMessage",
    "ClientMessage",
    "CommandAckMessage",
    "ControlErrorMessage",
    "HealthAckMessage",
    "HealthMessage",
    "PauseMessage",
    "ResumeMessage",
    "RotateMessage",
    "RotatedMessage",
    "ServerActivationRecord",
    "ServerMessage",
    "ServerSdkFailure",
    "ServerSdkHealthReport",
    "ServiceCredential",
    "StreamReportMessage",
    "TerminateMessage",
    "decode_client_message",
]

SERVICE_KEY_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"
_SEMVER = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"

type LifecycleState = Literal[
    "dormant", "activating", "ready", "paused", "recovering", "failed", "terminated"
]
type ProbeHop = Literal[
    "server", "downstream_http", "database", "cache", "queue", "custom_protocol"
]
type PauseReason = Literal[
    "session_paused", "policy_hold", "ingestion_backpressure", "operator_stop"
]
type TerminateReason = Literal[
    "session_ended", "activation_superseded", "credential_revoked", "client_case_stop"
]
type FailureCode = Literal[
    "invalid_activation",
    "credential_rejected",
    "incompatible_runtime_or_contract",
    "duplicate_provider_or_hook",
    "required_adapter_missing",
    "resource_mismatch",
    "redaction_or_schema_failure",
    "propagation_failure",
    "unauthorized_destination",
    "ingestion_unavailable",
    "partial_acceptance",
    "acknowledgement_integrity_failure",
    "buffer_exhaustion",
    "unclean_stream_end",
    "route_not_registered",
    "manifest_signature_invalid",
    "control_connection_lost",
]


class WireModel(BaseModel):
    """Base for every control frame: closed, frozen, and strictly typed."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SdkIdentity(WireModel):
    """The SDK build presenting itself on the handshake."""

    version: str = Field(min_length=1, max_length=40)
    contract_major: int = Field(ge=1)
    capabilities: tuple[str, ...] = Field(max_length=32)


class RuntimeIdentity(WireModel):
    """The Node process the SDK is installed in."""

    node_version: str = Field(min_length=1, max_length=40)
    platform: str = Field(min_length=1, max_length=40)
    arch: str = Field(min_length=1, max_length=40)
    pid: int = Field(ge=1)


class ServerSdkFailure(WireModel):
    """One safe, operational-metadata-only failure the SDK reported."""

    code: FailureCode
    detected_at: datetime
    retry: Literal["never", "after_delay", "after_condition_change"]
    readiness_consequence: Literal[
        "none", "withdraw_readiness", "fail_stream", "terminate_stream"
    ]
    detail: Mapping[str, str | float | int | bool] = Field(max_length=32)
    batch_id: UUID | None = None
    batch_sequence: int | None = Field(default=None, ge=1)
    trace_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class AdapterStatus(WireModel):
    """Whether one manifest-required instrumentation adapter is installed."""

    adapter: Literal["node_http", "undici", "pg", "express", "fastify", "next_server"]
    required: bool
    installed: bool
    module_version: str | None = Field(default=None, max_length=40)


class BufferStatus(WireModel):
    """The bounded memory-only export queue's utilization and watermarks."""

    queued_batches: int = Field(ge=0)
    queued_spans: int = Field(ge=0)
    max_queue_batches: int = Field(ge=1)
    max_queue_spans: int = Field(ge=1)
    utilization: float = Field(ge=0, le=1)
    high_water_reached: bool
    last_produced_sequence: int = Field(ge=0)
    last_acknowledged_sequence: int = Field(ge=0)


class ProbeResult(WireModel):
    """The end-to-end propagation probe's current state."""

    state: Literal[
        "not_started",
        "awaiting_spans",
        "awaiting_acknowledgement",
        "passed",
        "failed",
    ]
    probe_id: UUID | None = None
    observed_hops: tuple[ProbeHop, ...] = Field(max_length=32)
    required_hops: tuple[ProbeHop, ...] = Field(max_length=32)
    failure_reason: (
        Literal[
            "duplicate_server_span",
            "missing_hop",
            "rejected_field",
            "unexpected_destination",
            "acknowledgement_timeout",
        ]
        | None
    ) = None


class ServerSdkHealthReport(WireModel):
    """Operational metadata only. Never product content or a rejected value."""

    reported_at: datetime
    sdk_version: str = Field(min_length=1, max_length=40)
    contract_major: int = Field(ge=1)
    node_version: str = Field(min_length=1, max_length=40)
    service_key: str = Field(pattern=SERVICE_KEY_PATTERN, max_length=120)
    service_instance_id: UUID
    server_stream_id: UUID
    activation_id: UUID | None = None
    manifest_version: int | None = Field(default=None, ge=1)
    policy_version: str | None = Field(default=None, pattern=_SEMVER)
    utrace_schema_version: str | None = Field(default=None, pattern=_SEMVER)
    semantic_convention_version: str | None = Field(default=None, pattern=_SEMVER)
    lifecycle_state: LifecycleState
    capabilities: tuple[str, ...] = Field(max_length=32)
    adapters: tuple[AdapterStatus, ...] = Field(max_length=16)
    buffer: BufferStatus
    credential_expires_at: datetime | None = None
    probe_result: ProbeResult
    failures: tuple[ServerSdkFailure, ...] = Field(max_length=32)


class ActivateMessage(WireModel):
    """Exchange the one-time bootstrap credential for an observation activation."""

    type: Literal["activate"]
    request_id: UUID
    bootstrap_credential: str = Field(min_length=1, max_length=4096)
    service_name: str = Field(pattern=SERVICE_KEY_PATTERN, max_length=120)
    server_stream_id: UUID
    service_instance_id: UUID
    sdk: SdkIdentity
    runtime: RuntimeIdentity


class RotateMessage(WireModel):
    """Ask for fresh short-lived service-instance credentials."""

    type: Literal["rotate"]
    request_id: UUID
    server_stream_id: UUID


class HealthMessage(WireModel):
    """One health report and its acknowledgement request."""

    type: Literal["health"]
    request_id: UUID
    report: ServerSdkHealthReport


class CommandAckMessage(WireModel):
    """The lifecycle state one pause, resume, or terminate command reached."""

    type: Literal["command_ack"]
    command_id: UUID
    lifecycle_state: LifecycleState


class StreamReportMessage(WireModel):
    """The terminal stream report after a bounded flush, or after a failure."""

    type: Literal["stream_report"]
    request_id: UUID
    server_stream_id: UUID
    clean: bool
    last_produced_sequence: int = Field(ge=0)
    last_acknowledged_sequence: int = Field(ge=0)
    failures: tuple[ServerSdkFailure, ...] = Field(max_length=32)


type ClientMessage = (
    ActivateMessage
    | RotateMessage
    | HealthMessage
    | CommandAckMessage
    | StreamReportMessage
)

_CLIENT_MESSAGE = TypeAdapter[ClientMessage](
    Annotated[ClientMessage, Field(discriminator="type")]
)


def decode_client_message(payload: object) -> ClientMessage:
    """Decode one frame the SDK sent, or raise ``pydantic.ValidationError``."""
    return _CLIENT_MESSAGE.validate_python(payload)


class ServiceCredential(WireModel):
    """A short-lived, write-only credential for the ingestion edge."""

    token: str = Field(min_length=1, max_length=4096)
    expires_at: str


class ResourceIdentity(WireModel):
    """Standard OpenTelemetry resource identity for one service process."""

    service_name: str = Field(min_length=1, max_length=120)
    service_namespace: str = Field(min_length=1, max_length=120)
    service_version: str = Field(min_length=1, max_length=120)
    deployment_environment_name: str = Field(min_length=1, max_length=120)


class IngestEndpoint(WireModel):
    """Where the exporter sends, and the token the preview gate compares."""

    traces_path: Literal["/v1/ingest/otlp/v1/traces"] = "/v1/ingest/otlp/v1/traces"
    preview_entry_token: str = Field(min_length=16, max_length=512)


class PropagationProbeSpec(WireModel):
    """The smoke workflow the SDK must observe end to end before readiness."""

    probe_id: UUID
    route_template: str = Field(min_length=1, max_length=200)
    method: Literal[
        "GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"
    ]
    required_hops: tuple[ProbeHop, ...] = Field(min_length=1, max_length=32)


class ServerActivationRecord(WireModel):
    """The activation record the control plane returns to one service process."""

    activation_id: UUID
    client_id: UUID
    client_user_id: UUID
    customer_account_id: UUID
    session_id: UUID
    preview_environment_id: UUID
    preview_runtime_allocation_id: UUID
    preview_version_id: UUID | None = None
    server_stream_id: UUID
    service_instance_id: UUID
    service_key: str = Field(pattern=SERVICE_KEY_PATTERN, max_length=120)
    resource: ResourceIdentity
    credential: ServiceCredential
    ingest: IngestEndpoint
    limits: Mapping[str, object]
    probe: PropagationProbeSpec
    signed_manifest: Mapping[str, object]


class ActivatedMessage(WireModel):
    """The activation reply."""

    type: Literal["activated"] = "activated"
    request_id: UUID
    activation: ServerActivationRecord


class RotatedMessage(WireModel):
    """The rotation reply. The activation and its stream are unchanged."""

    type: Literal["rotated"] = "rotated"
    request_id: UUID
    credential: ServiceCredential


class HealthAckMessage(WireModel):
    """The acknowledgement of one persisted health report."""

    type: Literal["health_ack"] = "health_ack"
    request_id: UUID


class PauseMessage(WireModel):
    """Stop session-correlated export until a resume arrives."""

    type: Literal["pause"] = "pause"
    command_id: UUID
    reason: PauseReason


class ResumeMessage(WireModel):
    """Resume export after a pause. Readiness returns on fresh evidence."""

    type: Literal["resume"] = "resume"
    command_id: UUID


class TerminateMessage(WireModel):
    """End the stream after a bounded flush and a terminal stream report."""

    type: Literal["terminate"] = "terminate"
    command_id: UUID
    reason: TerminateReason


class ControlErrorMessage(WireModel):
    """A typed refusal, correlated to its request when one caused it."""

    type: Literal["error"] = "error"
    request_id: UUID | None = None
    failure: ServerSdkFailure


type ServerMessage = (
    ActivatedMessage
    | RotatedMessage
    | HealthAckMessage
    | PauseMessage
    | ResumeMessage
    | TerminateMessage
    | ControlErrorMessage
)
