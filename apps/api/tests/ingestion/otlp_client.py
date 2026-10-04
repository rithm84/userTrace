"""A minimal OTLP exporter that reproduces the server SDK's batch headers exactly.

`@utrace/server-sdk` serializes spans with `ProtobufTraceSerializer`, hashes the
uncompressed body, gzips it, and posts it to `POST /v1/ingest/otlp/v1/traces`
with the batch envelope in `x-utrace-*` headers and the credential in the
standard Authorization header. This client does the same thing from Python
against the real route, so the wire contract is exercised rather than described.
"""

from __future__ import annotations

import gzip
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from uuid import UUID

import httpx
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, InstrumentationScope, KeyValue
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import (
    ResourceSpans,
    ScopeSpans,
    Span,
    Status,
)

from app.ids import uuid7

__all__ = ["SpanSpec", "export_batch", "serialize"]

SDK_VERSION = "0.1.0"
CONTRACT_MAJOR = 1


@dataclass(frozen=True, slots=True)
class SpanSpec:
    """One span to serialize, in the shape the adapters would produce."""

    name: str
    kind: int
    trace_id: str
    span_id: str
    parent_span_id: str | None
    start_unix_nano: int
    end_unix_nano: int
    scope_name: str
    attributes: Mapping[str, str | int | bool] = field(default_factory=dict)
    status_code: int = Status.StatusCode.STATUS_CODE_UNSET


def _value(raw: str | int | bool) -> AnyValue:
    """Convert one attribute value to its OTLP representation."""
    if isinstance(raw, bool):
        return AnyValue(bool_value=raw)
    if isinstance(raw, int):
        return AnyValue(int_value=raw)
    return AnyValue(string_value=raw)


def serialize(
    spans: Sequence[SpanSpec],
    *,
    service_name: str,
    service_namespace: str,
    service_version: str,
    environment: str,
) -> bytes:
    """Serialize spans into one OTLP export request body."""
    by_scope: dict[str, list[SpanSpec]] = {}
    for spec in spans:
        by_scope.setdefault(spec.scope_name, []).append(spec)
    request = ExportTraceServiceRequest(
        resource_spans=[
            ResourceSpans(
                resource=Resource(
                    attributes=[
                        KeyValue(key="service.name", value=_value(service_name)),
                        KeyValue(
                            key="service.namespace", value=_value(service_namespace)
                        ),
                        KeyValue(key="service.version", value=_value(service_version)),
                        KeyValue(
                            key="deployment.environment.name", value=_value(environment)
                        ),
                    ]
                ),
                scope_spans=[
                    ScopeSpans(
                        scope=InstrumentationScope(name=scope_name, version="1.0.0"),
                        spans=[
                            Span(
                                trace_id=bytes.fromhex(spec.trace_id),
                                span_id=bytes.fromhex(spec.span_id),
                                parent_span_id=(
                                    bytes.fromhex(spec.parent_span_id)
                                    if spec.parent_span_id is not None
                                    else b""
                                ),
                                name=spec.name,
                                kind=spec.kind,
                                start_time_unix_nano=spec.start_unix_nano,
                                end_time_unix_nano=spec.end_unix_nano,
                                attributes=[
                                    KeyValue(key=key, value=_value(value))
                                    for key, value in spec.attributes.items()
                                ],
                                status=Status(code=spec.status_code),
                            )
                            for spec in scope_specs
                        ],
                    )
                    for scope_name, scope_specs in by_scope.items()
                ],
            )
        ]
    )
    return request.SerializeToString()


async def export_batch(
    base_url: str,
    *,
    token: str,
    activation_id: UUID,
    server_stream_id: UUID,
    service_instance_id: UUID,
    batch_sequence: int,
    body: bytes,
    span_count: int,
    policy_version: str,
    utrace_schema_version: str,
    semantic_convention_version: str,
    manifest_version: int,
    batch_id: UUID | None,
) -> httpx.Response:
    """Post one immutable batch with the exact envelope headers the SDK sends."""
    envelope = {
        "activation-id": str(activation_id),
        "server-stream-id": str(server_stream_id),
        "service-instance-id": str(service_instance_id),
        "batch-id": str(batch_id or uuid7()),
        "batch-sequence": str(batch_sequence),
        "span-count": str(span_count),
        "sdk-version": SDK_VERSION,
        "contract-major": str(CONTRACT_MAJOR),
        "manifest-version": str(manifest_version),
        "policy-version": policy_version,
        "utrace-schema-version": utrace_schema_version,
        "semantic-convention-version": semantic_convention_version,
        "content-sha256": sha256(body).hexdigest(),
    }
    headers = {f"x-utrace-{name}": value for name, value in envelope.items()}
    headers["authorization"] = f"Bearer {token}"
    headers["content-type"] = "application/x-protobuf"
    headers["content-encoding"] = "gzip"
    headers["user-agent"] = f"utrace-server-sdk/{SDK_VERSION} contract/{CONTRACT_MAJOR}"
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        return await client.post(
            "/v1/ingest/otlp/v1/traces", content=gzip.compress(body), headers=headers
        )
