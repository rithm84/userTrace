"""Browser ingestion wire models.

These mirror ``packages/utrace-contracts/schemas/browser-observation/`` one field
at a time: ``ingest-client-frame.v1``, ``ingest-server-frame.v1``,
``observation-scope.v1``, and ``observation-envelope.v1``. The contracts package
publishes no Python bindings for the ``browser-observation`` area yet, so the
models live here and ``tests/ingestion/test_frame_contracts.py`` replays the
shared fixtures and the JSON Schemas against them.

Unknown record types, versions, attributes, enum values, and required fields fail
validation. Every model is closed to extra fields.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from app.ingestion.errors import SourceValidationError

__all__ = [
    "AckFrame",
    "ClientFrame",
    "ErrorFrame",
    "FlowControlFrame",
    "ObservationBatchFrame",
    "ObservationEnvelope",
    "ObservationScope",
    "ServerFrame",
    "StreamAcceptedFrame",
    "StreamEndFrame",
    "StreamOpenFrame",
    "decode_client_frame",
]

HEX_TRACE_ID = r"^[0-9a-f]{32}$"
HEX_SPAN_ID = r"^[0-9a-f]{16}$"

ObservationType = Literal[
    "baseline.snapshot",
    "baseline.chunk",
    "interaction.activation",
    "interaction.focus_change",
    "input.metadata",
    "form.submission",
    "route.change",
    "request.started",
    "request.completed",
    "error.frontend",
    "performance.measurement",
    "product.milestone",
    "target.registry_change",
    "dom.change",
    "observation.gap",
]


class WireModel(BaseModel):
    """Base for every wire model: closed, frozen, and strictly typed."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ObservationScope(WireModel):
    """Repeated envelope fields, encoded once in the transport batch header."""

    client_id: UUID
    client_user_id: UUID
    customer_account_id: UUID
    session_id: UUID
    preview_environment_id: UUID
    preview_runtime_allocation_id: UUID
    preview_version_id: UUID | None = None
    browser_stream_id: UUID
    schema_version: Literal["1"]
    policy_version: str = Field(min_length=1)
    redaction_version: str = Field(min_length=1)


class ObservationEnvelope(WireModel):
    """Per-record envelope. The typed payload is selected by ``observation_type``."""

    observation_id: UUID
    observation_type: ObservationType
    runtime_id: UUID
    document_attachment_id: UUID
    frame_id: str = Field(min_length=1)
    source_sequence: int = Field(ge=1)
    export_sequence: int = Field(ge=1)
    occurred_at: datetime
    monotonic_offset: float = Field(ge=0)
    route_revision: int = Field(ge=1)
    visibility_state: Literal["visible", "hidden"]
    target_id: str | None = Field(default=None, min_length=1, max_length=128)
    target_revision: int | None = Field(default=None, ge=1)
    trace_id: str | None = Field(default=None, pattern=HEX_TRACE_ID)
    span_id: str | None = Field(default=None, pattern=HEX_SPAN_ID)
    payload: dict[str, object]

    @model_validator(mode="after")
    def check_dependent_fields(self) -> "ObservationEnvelope":
        """Enforce the schema's ``dependentRequired`` pairs."""
        if self.target_revision is not None and self.target_id is None:
            raise ValueError("target_revision requires target_id")
        if self.span_id is not None and self.trace_id is None:
            raise ValueError("span_id requires trace_id")
        return self


class StreamOpenFrame(WireModel):
    """Opens or resumes one browser stream."""

    kind: Literal["stream.open"]
    schema_version: Literal["1"]
    protocol_version: Literal["1"]
    browser_stream_id: UUID
    runtime_id: UUID
    document_attachment_id: UUID
    scope: ObservationScope
    resume_from_export_sequence: int = Field(ge=0)


class ObservationBatchFrame(WireModel):
    """One immutable batch of already-redacted observations."""

    kind: Literal["observation.batch"]
    batch_id: UUID
    browser_stream_id: UUID
    first_export_sequence: int = Field(ge=1)
    last_export_sequence: int = Field(ge=1)
    observations: list[ObservationEnvelope] = Field(min_length=1)

    @model_validator(mode="after")
    def check_declared_range(self) -> "ObservationBatchFrame":
        """The declared range must match the records the batch actually carries."""
        if self.last_export_sequence < self.first_export_sequence:
            raise ValueError("last_export_sequence precedes first_export_sequence")
        sequences = [record.export_sequence for record in self.observations]
        if min(sequences) != self.first_export_sequence:
            raise ValueError("first_export_sequence is not the batch's lowest sequence")
        if max(sequences) != self.last_export_sequence:
            raise ValueError("last_export_sequence is not the batch's highest sequence")
        if len(set(sequences)) != len(sequences):
            raise ValueError("the batch repeats an export_sequence")
        return self


class StreamEndFrame(WireModel):
    """A graceful stream end records a terminal sequence."""

    kind: Literal["stream.end"]
    browser_stream_id: UUID
    terminal_export_sequence: int = Field(ge=0)
    reason: Literal["pagehide", "terminated", "paused"]


ClientFrame = Annotated[
    StreamOpenFrame | ObservationBatchFrame | StreamEndFrame,
    Field(discriminator="kind"),
]


class StreamAcceptedFrame(WireModel):
    """Ingestion accepted the stream and reports where its acknowledgement stands."""

    kind: Literal["stream.accepted"] = "stream.accepted"
    browser_stream_id: UUID
    accepted_through_export_sequence: int = Field(ge=0)


class AckFrame(WireModel):
    """Acknowledgement of the highest contiguous accepted ``export_sequence``."""

    kind: Literal["ack"] = "ack"
    browser_stream_id: UUID
    accepted_through_export_sequence: int = Field(ge=0)


class FlowControlFrame(WireModel):
    """Explicit backpressure. It never advances the acknowledgement."""

    kind: Literal["flow_control"] = "flow_control"
    state: Literal["pause", "resume"]
    retry_after_ms: int = Field(ge=0)


class ErrorFrame(WireModel):
    """A typed failure. It carries no rejected product value."""

    kind: Literal["error"] = "error"
    code: str = Field(min_length=1)
    message: str
    retryable: bool
    batch_id: UUID | None = None


ServerFrame = StreamAcceptedFrame | AckFrame | FlowControlFrame | ErrorFrame


_CLIENT_FRAME_ADAPTER: TypeAdapter[StreamOpenFrame | ObservationBatchFrame | StreamEndFrame] = (
    TypeAdapter(ClientFrame)
)


def decode_client_frame(
    text: str,
) -> StreamOpenFrame | ObservationBatchFrame | StreamEndFrame:
    """Decode one client frame from its wire text, failing closed.

    Validation runs in strict JSON mode: a string still becomes a UUID or an
    RFC 3339 timestamp, because JSON has no such types, but a string never
    becomes an integer and an unknown field is never ignored.
    """
    try:
        return _CLIENT_FRAME_ADAPTER.validate_json(text, strict=True)
    except ValidationError as error:
        first = error.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "frame"
        raise SourceValidationError(field=location, reason=first["msg"]) from None
