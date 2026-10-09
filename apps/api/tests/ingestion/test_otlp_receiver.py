"""The server edge: full success only, and the trace-scope rules that protect it."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from uuid import UUID

from opentelemetry.proto.trace.v1.trace_pb2 import Span

from app.db.pool import Database
from app.ids import uuid7
from tests.ingestion.factories import (
    MANIFEST_VERSION,
    POLICY_VERSION,
    SEMANTIC_CONVENTION_VERSION,
    UTRACE_SCHEMA_VERSION,
    PreviewFixture,
    create_preview,
    start_server_activation,
)
from tests.ingestion.keys import SigningKeys
from tests.ingestion.otlp_client import SpanSpec, export_batch, serialize
from tests.supabase_auth import SignedInUser


@dataclass(frozen=True, slots=True)
class ActiveServerStream:
    """One activated service process ready to export."""

    preview: PreviewFixture
    activation_id: UUID
    server_stream_id: UUID
    service_instance_id: UUID
    token: str
    preview_entry_token: str


async def _activate_server(
    database: Database, keys: SigningKeys, *, name: str, authorizing_auth_user_id: UUID
) -> ActiveServerStream:
    """Create a preview and activate one server service against it."""
    preview = await create_preview(
        database, name=name, authorizing_auth_user_id=authorizing_auth_user_id
    )
    activation_id, server_stream_id, token, entry_token = await start_server_activation(
        database, preview=preview, keys=keys
    )
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select service_instance_id from public.telemetry_activations where id = %s",
            (activation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AssertionError("the server activation was not committed")
    return ActiveServerStream(
        preview=preview,
        activation_id=activation_id,
        server_stream_id=server_stream_id,
        service_instance_id=UUID(str(row["service_instance_id"])),
        token=token,
        preview_entry_token=entry_token,
    )


def _now_nanos() -> int:
    """Return a start time inside the window the clock-quality checks allow."""
    return time.time_ns()


def _server_span(trace_id: str, span_id: str, parent: str | None) -> SpanSpec:
    """Build one inbound SERVER span for the registered route."""
    start = _now_nanos()
    return SpanSpec(
        name="POST /api/saved-views",
        kind=Span.SpanKind.SPAN_KIND_SERVER,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent,
        start_unix_nano=start,
        end_unix_nano=start + 12_000_000,
        scope_name="@opentelemetry/instrumentation-http",
        attributes={
            "http.route": "/api/saved-views",
            "http.request.method": "POST",
            "http.response.status_code": 201,
            "utrace.operation.name": "saved_views.create",
        },
    )


async def _post(
    base_url: str, stream: ActiveServerStream, spans: list[SpanSpec], *, sequence: int,
    batch_id: UUID | None,
) -> tuple[int, bytes]:
    """Serialize and post one batch, returning the status and body."""
    body = serialize(
        spans,
        service_name="shopalytics-api",
        service_namespace="shopalytics",
        service_version="2026.09.20",
        environment="preview",
    )
    response = await export_batch(
        base_url,
        token=stream.token,
        activation_id=stream.activation_id,
        server_stream_id=stream.server_stream_id,
        service_instance_id=stream.service_instance_id,
        batch_sequence=sequence,
        body=body,
        span_count=len(spans),
        policy_version=POLICY_VERSION,
        utrace_schema_version=UTRACE_SCHEMA_VERSION,
        semantic_convention_version=SEMANTIC_CONVENTION_VERSION,
        manifest_version=MANIFEST_VERSION,
        batch_id=batch_id,
    )
    return response.status_code, response.content


async def test_a_valid_batch_is_a_full_success_with_no_partial_success(
    telemetry_database: Database,
    keys: SigningKeys,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A success acknowledges the complete batch and commits every span."""
    stream = await _activate_server(
        telemetry_database,
        keys,
        name=f"otlp-ok-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    trace_id = secrets.token_hex(16)
    status, body = await _post(
        ingest_server,
        stream,
        [_server_span(trace_id, secrets.token_hex(8), None)],
        sequence=1,
        batch_id=None,
    )
    assert status == 200
    # An empty ExportTraceServiceResponse is a full success on the wire.
    assert body == b""

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select span_kind, http_route, registered_operation, status_code "
            "from public.server_spans where activation_id = %s",
            (stream.activation_id,),
        )
        spans = await cur.fetchall()
        await cur.execute(
            "select outcome, stage, record_count, batch_sequence, raw_artifact_id "
            "from public.ingestion_receipts where activation_id = %s",
            (stream.activation_id,),
        )
        receipts = await cur.fetchall()
        await cur.execute(
            "select accepted_through_batch_sequence from public.server_streams "
            "where id = %s",
            (stream.server_stream_id,),
        )
        server_stream = await cur.fetchone()
    assert len(spans) == 1
    assert spans[0]["span_kind"] == "server"
    assert spans[0]["http_route"] == "/api/saved-views"
    assert spans[0]["registered_operation"] == "saved_views.create"
    assert len(receipts) == 1
    assert receipts[0]["outcome"] == "accepted"
    assert receipts[0]["stage"] == "indexed"
    assert receipts[0]["raw_artifact_id"] is not None
    assert server_stream is not None
    assert server_stream["accepted_through_batch_sequence"] == 1


async def test_the_batch_that_fills_a_hole_carries_the_watermark_past_later_batches(
    telemetry_database: Database,
    keys: SigningKeys,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Concurrent exports can commit out of order: 1, 3, 4, then 2 accepts through 4."""
    stream = await _activate_server(
        telemetry_database,
        keys,
        name=f"otlp-order-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )

    async def accepted_through() -> tuple[int, int]:
        async with telemetry_database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "select accepted_through_batch_sequence, highest_received_batch_sequence "
                "from public.server_streams where id = %s",
                (stream.server_stream_id,),
            )
            row = await cur.fetchone()
        assert row is not None
        return (
            int(row["accepted_through_batch_sequence"]),
            int(row["highest_received_batch_sequence"]),
        )

    for sequence in (1, 3, 4):
        status, _ = await _post(
            ingest_server,
            stream,
            [_server_span(secrets.token_hex(16), secrets.token_hex(8), None)],
            sequence=sequence,
            batch_id=None,
        )
        assert status == 200
    assert await accepted_through() == (1, 4)

    status, _ = await _post(
        ingest_server,
        stream,
        [_server_span(secrets.token_hex(16), secrets.token_hex(8), None)],
        sequence=2,
        batch_id=None,
    )
    assert status == 200
    assert await accepted_through() == (4, 4)


async def test_one_invalid_span_rejects_the_whole_batch(
    telemetry_database: Database,
    keys: SigningKeys,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A partially acceptable batch is refused whole, so the trace stays unambiguous."""
    stream = await _activate_server(
        telemetry_database,
        keys,
        name=f"otlp-reject-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    trace_id = secrets.token_hex(16)
    good = _server_span(trace_id, secrets.token_hex(8), None)
    start = _now_nanos()
    forbidden = SpanSpec(
        name="pg.query",
        kind=Span.SpanKind.SPAN_KIND_CLIENT,
        trace_id=trace_id,
        span_id=secrets.token_hex(8),
        parent_span_id=good.span_id,
        start_unix_nano=start,
        end_unix_nano=start + 2_000_000,
        scope_name="@opentelemetry/instrumentation-pg",
        attributes={"db.query.text": "select * from saved_views"},
    )
    status, _ = await _post(
        ingest_server, stream, [good, forbidden], sequence=1, batch_id=None
    )
    assert status == 400

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as total from public.server_spans where activation_id = %s",
            (stream.activation_id,),
        )
        spans = await cur.fetchone()
        await cur.execute(
            "select count(*) as total from public.ingestion_receipts "
            "where activation_id = %s",
            (stream.activation_id,),
        )
        receipts = await cur.fetchone()
    assert spans is not None
    assert receipts is not None
    assert spans["total"] == 0
    assert receipts["total"] == 0


async def test_an_exact_batch_retry_creates_no_new_evidence(
    telemetry_database: Database,
    keys: SigningKeys,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Retries preserve the batch ID and span identities; ingestion deduplicates."""
    stream = await _activate_server(
        telemetry_database,
        keys,
        name=f"otlp-retry-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    batch_id = uuid7()
    spans = [_server_span(secrets.token_hex(16), secrets.token_hex(8), None)]
    first, _ = await _post(ingest_server, stream, spans, sequence=1, batch_id=batch_id)
    retry, _ = await _post(ingest_server, stream, spans, sequence=1, batch_id=batch_id)
    assert first == 200
    assert retry == 200

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as total from public.ingestion_receipts "
            "where activation_id = %s",
            (stream.activation_id,),
        )
        receipts = await cur.fetchone()
        await cur.execute(
            "select count(*) as total from public.server_spans where activation_id = %s",
            (stream.activation_id,),
        )
        stored = await cur.fetchone()
    assert receipts is not None
    assert stored is not None
    assert receipts["total"] == 1
    assert stored["total"] == 1


async def test_a_trace_id_from_another_activation_is_an_integrity_violation(
    telemetry_database: Database,
    keys: SigningKeys,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A trace ID is scoped by its activation, never a cross-session join."""
    first = await _activate_server(
        telemetry_database,
        keys,
        name=f"otlp-scope-a-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    second = await _activate_server(
        telemetry_database,
        keys,
        name=f"otlp-scope-b-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    shared_trace = secrets.token_hex(16)
    accepted, _ = await _post(
        ingest_server,
        first,
        [_server_span(shared_trace, secrets.token_hex(8), None)],
        sequence=1,
        batch_id=None,
    )
    assert accepted == 200

    refused, _ = await _post(
        ingest_server,
        second,
        [_server_span(shared_trace, secrets.token_hex(8), None)],
        sequence=1,
        batch_id=None,
    )
    assert refused == 409

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as total from public.server_spans where activation_id = %s",
            (second.activation_id,),
        )
        stored = await cur.fetchone()
    assert stored is not None
    assert stored["total"] == 0


async def test_a_credential_from_the_browser_edge_is_refused(
    telemetry_database: Database,
    keys: SigningKeys,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A credential minted for one edge cannot write on the other."""
    stream = await _activate_server(
        telemetry_database,
        keys,
        name=f"otlp-lane-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    body = serialize(
        [_server_span(secrets.token_hex(16), secrets.token_hex(8), None)],
        service_name="shopalytics-api",
        service_namespace="shopalytics",
        service_version="2026.09.20",
        environment="preview",
    )
    response = await export_batch(
        ingest_server,
        token=secrets.token_urlsafe(32),
        activation_id=stream.activation_id,
        server_stream_id=stream.server_stream_id,
        service_instance_id=stream.service_instance_id,
        batch_sequence=1,
        body=body,
        span_count=1,
        policy_version=POLICY_VERSION,
        utrace_schema_version=UTRACE_SCHEMA_VERSION,
        semantic_convention_version=SEMANTIC_CONVENTION_VERSION,
        manifest_version=MANIFEST_VERSION,
        batch_id=None,
    )
    assert response.status_code == 401


async def test_an_envelope_that_claims_another_activation_is_refused(
    telemetry_database: Database,
    keys: SigningKeys,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """The envelope's claims must agree with the authenticated activation."""
    stream = await _activate_server(
        telemetry_database,
        keys,
        name=f"otlp-envelope-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    body = serialize(
        [_server_span(secrets.token_hex(16), secrets.token_hex(8), None)],
        service_name="shopalytics-api",
        service_namespace="shopalytics",
        service_version="2026.09.20",
        environment="preview",
    )
    response = await export_batch(
        ingest_server,
        token=stream.token,
        activation_id=uuid7(),
        server_stream_id=stream.server_stream_id,
        service_instance_id=stream.service_instance_id,
        batch_sequence=1,
        body=body,
        span_count=1,
        policy_version=POLICY_VERSION,
        utrace_schema_version=UTRACE_SCHEMA_VERSION,
        semantic_convention_version=SEMANTIC_CONVENTION_VERSION,
        manifest_version=MANIFEST_VERSION,
        batch_id=None,
    )
    assert response.status_code == 409


async def test_reusing_a_span_identity_with_new_content_fails_the_server_stream(
    telemetry_database: Database,
    keys: SigningKeys,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Both versions stay diagnostics; ingestion does not choose the later arrival."""
    stream = await _activate_server(
        telemetry_database,
        keys,
        name=f"otlp-reuse-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    trace_id = secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    first = _server_span(trace_id, span_id, None)
    accepted, _ = await _post(
        ingest_server, stream, [first], sequence=1, batch_id=None
    )
    assert accepted == 200

    conflicting = SpanSpec(
        name=first.name,
        kind=first.kind,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=None,
        start_unix_nano=first.start_unix_nano,
        end_unix_nano=first.end_unix_nano + 5_000_000,
        scope_name=first.scope_name,
        attributes=dict(first.attributes),
    )
    refused, _ = await _post(
        ingest_server, stream, [conflicting], sequence=2, batch_id=None
    )
    assert refused == 409

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select identity_kind, identity_value, first_content_sha256,
                   conflicting_content_sha256
            from public.telemetry_identity_conflicts where source_stream_id = %s
            """,
            (stream.server_stream_id,),
        )
        conflicts = await cur.fetchall()
        await cur.execute(
            "select state, clean from public.server_streams where id = %s",
            (stream.server_stream_id,),
        )
        server_stream = await cur.fetchone()
        await cur.execute(
            "select end_time_unix_nano from public.server_spans "
            "where activation_id = %s and span_id = %s",
            (stream.activation_id, span_id),
        )
        kept = await cur.fetchone()
        await cur.execute(
            "select code, readiness_consequence from public.ingestion_failures "
            "where activation_id = %s",
            (stream.activation_id,),
        )
        failures = await cur.fetchall()
    assert len(conflicts) == 1
    assert conflicts[0]["identity_kind"] == "span_id"
    assert (
        conflicts[0]["first_content_sha256"]
        != conflicts[0]["conflicting_content_sha256"]
    )
    assert server_stream is not None
    assert server_stream["state"] == "failed"
    assert server_stream["clean"] is False
    assert kept is not None
    assert kept["end_time_unix_nano"] == first.end_unix_nano
    assert len(failures) == 1
    assert failures[0]["code"] == "duplicate_conflict"
    assert failures[0]["readiness_consequence"] == "fail_stream"


async def test_a_refused_batch_reports_a_typed_control_fact(
    telemetry_database: Database,
    keys: SigningKeys,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A schema refusal leaves a safe failure record, never a product value."""
    stream = await _activate_server(
        telemetry_database,
        keys,
        name=f"otlp-fact-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    start = _now_nanos()
    forbidden = SpanSpec(
        name="pg.query",
        kind=Span.SpanKind.SPAN_KIND_CLIENT,
        trace_id=secrets.token_hex(16),
        span_id=secrets.token_hex(8),
        parent_span_id=None,
        start_unix_nano=start,
        end_unix_nano=start + 1_000_000,
        scope_name="@opentelemetry/instrumentation-pg",
        attributes={"db.statement": "select 1"},
    )
    status, _ = await _post(
        ingest_server, stream, [forbidden], sequence=1, batch_id=None
    )
    assert status == 400

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select code, stage, detail, readiness_consequence "
            "from public.ingestion_failures where activation_id = %s",
            (stream.activation_id,),
        )
        failures = await cur.fetchall()
    assert len(failures) == 1
    assert failures[0]["code"] == "unsafe_data_detected"
    assert failures[0]["readiness_consequence"] == "withdraw_readiness"
    assert "select 1" not in str(failures[0]["detail"])
