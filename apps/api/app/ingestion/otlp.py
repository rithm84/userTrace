"""OTLP/HTTP decoding and normalization for the server edge.

The edge accepts binary Protobuf with gzip and nothing else. Compression ratio,
size, span count, nesting depth, attribute count, array length, and string length
are all checked before allocation or normalization, and the platform exclusion
layer runs over every attribute key before a span becomes a canonical record.

A full success acknowledges the complete batch. uTrace never emits partial
success: if any span makes the batch invalid, the request fails as a whole so the
server SDK can fail the stream explicitly.

Contracts: docs/arch-components/telemetry-and-evidence.md#validation-and-normalization
           docs/arch-components/server-sdk.md#span-model-and-semantic-schema
"""

import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status
from pydantic import BaseModel, ConfigDict, Field

from app.envelope import JsonValue
from app.ingestion.errors import (
    ClockQualityError,
    PayloadLimitError,
    SourceValidationError,
)
from app.ingestion.limits import (
    MAX_ARRAY_LENGTH,
    MAX_ATTRIBUTE_COUNT,
    MAX_ATTRIBUTE_VALUE_LENGTH,
    MAX_COMPRESSION_RATIO,
    SERVER_MAX_REQUEST_BYTES,
    SERVER_MAX_SPANS,
)
from app.ingestion.never_collect import assert_server_attribute_allowed

__all__ = [
    "ENVELOPE_HEADER_PREFIX",
    "ExportBatchEnvelope",
    "NormalizedSpan",
    "decode_export_request",
    "decompress",
    "envelope_from_headers",
    "full_success_body",
]

#: Header names are the schema members with `_` replaced by `-`.
ENVELOPE_HEADER_PREFIX: Final[str] = "x-utrace-"

_SEMVER = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"

_SPAN_KIND_NAMES: Final[Mapping[int, str]] = {
    Span.SpanKind.SPAN_KIND_UNSPECIFIED: "unspecified",
    Span.SpanKind.SPAN_KIND_INTERNAL: "internal",
    Span.SpanKind.SPAN_KIND_SERVER: "server",
    Span.SpanKind.SPAN_KIND_CLIENT: "client",
    Span.SpanKind.SPAN_KIND_PRODUCER: "producer",
    Span.SpanKind.SPAN_KIND_CONSUMER: "consumer",
}

_STATUS_NAMES: Final[Mapping[int, str]] = {
    Status.StatusCode.STATUS_CODE_UNSET: "unset",
    Status.StatusCode.STATUS_CODE_OK: "ok",
    Status.StatusCode.STATUS_CODE_ERROR: "error",
}

_ZERO_SPAN_ID = "0" * 16

#: An hour is already far outside any product operation the pilot observes.
_MAX_SPAN_DURATION_NANOS: Final[int] = 3_600_000_000_000


class ExportBatchEnvelope(BaseModel):
    """The batch envelope the server SDK carries outside the OTLP span payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    activation_id: str = Field(min_length=36, max_length=36)
    server_stream_id: str = Field(min_length=36, max_length=36)
    service_instance_id: str = Field(min_length=36, max_length=36)
    batch_id: str = Field(min_length=36, max_length=36)
    batch_sequence: int = Field(ge=1)
    span_count: int = Field(ge=1, le=SERVER_MAX_SPANS)
    sdk_version: str = Field(min_length=1, max_length=40)
    contract_major: int = Field(ge=1)
    manifest_version: int = Field(ge=1)
    policy_version: str = Field(pattern=_SEMVER)
    utrace_schema_version: str = Field(pattern=_SEMVER)
    semantic_convention_version: str = Field(pattern=_SEMVER)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class NormalizedSpan:
    """One canonical server span. Source identities and times are preserved."""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    span_name: str
    span_kind: str
    start_time_unix_nano: int
    end_time_unix_nano: int
    status_code: str
    scope_name: str
    scope_version: str | None
    service_name: str
    service_namespace: str
    service_version: str
    deployment_environment_name: str
    attributes: Mapping[str, JsonValue]
    links: Sequence[Mapping[str, JsonValue]]
    span_events: Sequence[Mapping[str, JsonValue]]
    registered_operation: str | None
    http_route: str | None
    http_status_code: int | None


def envelope_from_headers(headers: Mapping[str, str]) -> ExportBatchEnvelope:
    """Read the batch envelope from its ``x-utrace-*`` request headers."""
    fields: dict[str, str] = {}
    for name in ExportBatchEnvelope.model_fields:
        header = f"{ENVELOPE_HEADER_PREFIX}{name.replace('_', '-')}"
        value = headers.get(header)
        if value is None:
            raise SourceValidationError(
                field=header, reason="the batch envelope header is missing"
            )
        fields[name] = value
    return ExportBatchEnvelope.model_validate(fields)


def decompress(body: bytes, content_encoding: str | None) -> bytes:
    """Decompress a gzip body within bounds, or refuse.

    The compression ratio is checked against the decompressed bound as the body
    is inflated, so a compression bomb is refused before it is allocated.
    """
    if content_encoding is None or content_encoding.lower() != "gzip":
        raise SourceValidationError(
            field="content-encoding",
            reason="the server edge accepts gzip-compressed OTLP protobuf only",
        )
    if len(body) > SERVER_MAX_REQUEST_BYTES:
        raise PayloadLimitError(
            limit_name="compressed_request_bytes",
            observed=len(body),
            limit=SERVER_MAX_REQUEST_BYTES,
        )
    decompressor = zlib.decompressobj(wbits=zlib.MAX_WBITS | 16)
    inflated: bytes = decompressor.decompress(body, SERVER_MAX_REQUEST_BYTES + 1)
    if len(inflated) > SERVER_MAX_REQUEST_BYTES:
        raise PayloadLimitError(
            limit_name="decompressed_request_bytes",
            observed=len(inflated),
            limit=SERVER_MAX_REQUEST_BYTES,
        )
    if len(inflated) > len(body) * MAX_COMPRESSION_RATIO:
        raise PayloadLimitError(
            limit_name="compression_ratio",
            observed=len(inflated) // max(len(body), 1),
            limit=MAX_COMPRESSION_RATIO,
        )
    return inflated


def _attribute_value(value: AnyValue, location: str) -> JsonValue:
    """Convert one OTLP attribute value, refusing an unbounded or nested shape."""
    which = value.WhichOneof("value")
    match which:
        case "string_value":
            if len(value.string_value) > MAX_ATTRIBUTE_VALUE_LENGTH:
                raise PayloadLimitError(
                    limit_name=f"{location}.string_length",
                    observed=len(value.string_value),
                    limit=MAX_ATTRIBUTE_VALUE_LENGTH,
                )
            return value.string_value
        case "bool_value":
            return value.bool_value
        case "int_value":
            return value.int_value
        case "double_value":
            return value.double_value
        case "array_value":
            if len(value.array_value.values) > MAX_ARRAY_LENGTH:
                raise PayloadLimitError(
                    limit_name=f"{location}.array_length",
                    observed=len(value.array_value.values),
                    limit=MAX_ARRAY_LENGTH,
                )
            return [
                _attribute_value(item, f"{location}[{index}]")
                for index, item in enumerate(value.array_value.values)
            ]
        case _:
            raise SourceValidationError(
                field=location,
                reason=f"attribute value kind '{which}' is not exported by uTrace",
            )


def _attributes(
    pairs: Sequence[KeyValue], location: str
) -> Mapping[str, JsonValue]:
    """Convert an OTLP attribute list after the platform exclusion check."""
    if len(pairs) > MAX_ATTRIBUTE_COUNT:
        raise PayloadLimitError(
            limit_name=f"{location}.attribute_count",
            observed=len(pairs),
            limit=MAX_ATTRIBUTE_COUNT,
        )
    converted: dict[str, JsonValue] = {}
    for pair in pairs:
        assert_server_attribute_allowed(pair.key, location)
        converted[pair.key] = _attribute_value(pair.value, f"{location}.{pair.key}")
    return converted


def _resource_field(
    attributes: Mapping[str, JsonValue], key: str
) -> str:
    """Read one required resource attribute as a string."""
    value = attributes.get(key)
    if not isinstance(value, str) or len(value) == 0:
        raise SourceValidationError(
            field=f"resource.{key}", reason="is missing from the OTLP resource"
        )
    return value


def _hex(raw: bytes, width: int, field: str) -> str:
    """Render a trace or span identifier, refusing an all-zero or wrong-width one."""
    if len(raw) != width:
        raise SourceValidationError(
            field=field, reason=f"must be {width} bytes, got {len(raw)}"
        )
    rendered = raw.hex()
    if int(rendered, 16) == 0:
        raise SourceValidationError(field=field, reason="must not be all zero")
    return rendered


def _span_times(span: Span) -> tuple[int, int]:
    """Validate one span's interval; an impossible duration is a clock failure."""
    if span.end_time_unix_nano < span.start_time_unix_nano:
        raise ClockQualityError(
            measurement="span_interval", reason="ends before it starts"
        )
    if span.end_time_unix_nano - span.start_time_unix_nano > _MAX_SPAN_DURATION_NANOS:
        raise ClockQualityError(
            measurement="span_duration",
            reason="exceeds the longest product operation uTrace observes",
        )
    return span.start_time_unix_nano, span.end_time_unix_nano


def decode_export_request(
    body: bytes, *, declared_span_count: int
) -> tuple[NormalizedSpan, ...]:
    """Decode and normalize one OTLP export request into canonical spans.

    Every span in the request has to pass. One invalid span makes the whole batch
    invalid, because a partially accepted batch would leave the trace ambiguously
    incomplete.
    """
    request = ExportTraceServiceRequest()
    try:
        request.ParseFromString(body)
    except DecodeError as error:
        raise SourceValidationError(
            field="body", reason=f"is not a decodable OTLP export request: {error}"
        ) from None

    spans: list[NormalizedSpan] = []
    for resource_spans in request.resource_spans:
        resource = _attributes(resource_spans.resource.attributes, "resource")
        service_name = _resource_field(resource, "service.name")
        service_namespace = _resource_field(resource, "service.namespace")
        service_version = _resource_field(resource, "service.version")
        environment = _resource_field(resource, "deployment.environment.name")
        for scope_spans in resource_spans.scope_spans:
            scope_name = scope_spans.scope.name
            if len(scope_name) == 0:
                raise SourceValidationError(
                    field="scope.name", reason="every exported span names its scope"
                )
            for span in scope_spans.spans:
                spans.append(
                    _normalize_span(
                        span,
                        scope_name=scope_name,
                        scope_version=scope_spans.scope.version or None,
                        service_name=service_name,
                        service_namespace=service_namespace,
                        service_version=service_version,
                        environment=environment,
                    )
                )
                if len(spans) > SERVER_MAX_SPANS:
                    raise PayloadLimitError(
                        limit_name="spans_per_request",
                        observed=len(spans),
                        limit=SERVER_MAX_SPANS,
                    )
    if len(spans) != declared_span_count:
        raise SourceValidationError(
            field=f"{ENVELOPE_HEADER_PREFIX}span-count",
            reason=(
                f"declares {declared_span_count} spans, the payload carries {len(spans)}"
            ),
        )
    return tuple(spans)


def _normalize_span(
    span: Span,
    *,
    scope_name: str,
    scope_version: str | None,
    service_name: str,
    service_namespace: str,
    service_version: str,
    environment: str,
) -> NormalizedSpan:
    """Normalize one OTLP span without inventing meaning it did not carry."""
    attributes = _attributes(span.attributes, "span.attributes")
    parent = span.parent_span_id.hex() if len(span.parent_span_id) > 0 else None
    if parent == _ZERO_SPAN_ID:
        parent = None
    start, end = _span_times(span)
    operation = attributes.get("utrace.operation.name")
    route = attributes.get("http.route")
    status = attributes.get("http.response.status_code")
    return NormalizedSpan(
        trace_id=_hex(span.trace_id, 16, "span.trace_id"),
        span_id=_hex(span.span_id, 8, "span.span_id"),
        parent_span_id=parent,
        span_name=span.name,
        span_kind=_SPAN_KIND_NAMES[span.kind],
        start_time_unix_nano=start,
        end_time_unix_nano=end,
        status_code=_STATUS_NAMES[span.status.code],
        scope_name=scope_name,
        scope_version=scope_version,
        service_name=service_name,
        service_namespace=service_namespace,
        service_version=service_version,
        deployment_environment_name=environment,
        attributes=attributes,
        links=[
            {
                "trace_id": _hex(link.trace_id, 16, "link.trace_id"),
                "span_id": _hex(link.span_id, 8, "link.span_id"),
                "attributes": dict(_attributes(link.attributes, "link.attributes")),
            }
            for link in span.links
        ],
        span_events=[
            {
                "name": event.name,
                "time_unix_nano": event.time_unix_nano,
                "attributes": dict(_attributes(event.attributes, "event.attributes")),
            }
            for event in span.events
        ],
        registered_operation=operation if isinstance(operation, str) else None,
        http_route=route if isinstance(route, str) else None,
        http_status_code=status if isinstance(status, int) else None,
    )


def full_success_body() -> bytes:
    """Return the OTLP response that acknowledges a complete batch.

    An empty ``partial_success`` is what a full success looks like on the wire.
    uTrace never populates it: the batch is accepted whole or refused whole.
    """
    body: bytes = ExportTraceServiceResponse().SerializeToString()
    return body
