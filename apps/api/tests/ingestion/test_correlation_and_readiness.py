"""Correlation of one real product operation, and the readiness it produces."""

from __future__ import annotations

import secrets
import time
from functools import partial
from uuid import UUID

from opentelemetry.proto.trace.v1.trace_pb2 import Span

from app.db.pool import Database
from app.ids import uuid7
from app.ingestion.correlation import correlate_trace
from app.ingestion.jobs import evaluate_readiness
from app.ingestion.readiness import current_readiness
from app.settings import Settings
from tests.ingestion.activation_client import activate_document
from tests.ingestion.browser_client import BrowserExporter, observation, request_started
from tests.ingestion.factories import (
    MANIFEST_VERSION,
    POLICY_VERSION,
    REDACTION_VERSION,
    SEMANTIC_CONVENTION_VERSION,
    UTRACE_SCHEMA_VERSION,
    PreviewFixture,
    create_preview,
    front_door_credential,
    start_server_activation,
)
from tests.ingestion.keys import SigningKeys
from tests.ingestion.otlp_client import SpanSpec, export_batch, serialize
from tests.supabase_auth import SignedInUser


async def test_a_real_browser_client_parent_joins_its_server_child_and_database_hop(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """One product operation correlates end to end, and readiness follows it."""
    preview = await create_preview(
        telemetry_database,
        name=f"correlate-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    document = await activate_document(
        ingest_server,
        service_token=settings.front_door_service_token,
        session_credential=front_door_credential(settings, preview),
        public_preview_host=preview.origin_host,
        document_origin=preview.document_origin,
        document_nonce=secrets.token_hex(24),
        sdk_installation_id="shopalytics-browser-sdk",
        public_key=keys.attestation_public,
    )
    browser_activation_id = UUID(str(document.attestation["attestation_id"]))
    attachment = UUID(str(document.attestation["document_attachment_id"]))
    browser_stream_id = uuid7()
    runtime_id = uuid7()
    trace_id = secrets.token_hex(16)
    client_span_id = secrets.token_hex(8)

    exporter = BrowserExporter(
        ingest_websocket_url,
        token=document.token,
        scope={
            "client_id": str(preview.case.client_id),
            "client_user_id": str(preview.case.client_user_id),
            "customer_account_id": str(preview.case.customer_account_id),
            "session_id": str(preview.interview_session_id),
            "preview_environment_id": str(preview.preview_environment_id),
            "preview_runtime_allocation_id": str(preview.preview_runtime_allocation_id),
            "browser_stream_id": str(browser_stream_id),
            "schema_version": "1",
            "policy_version": POLICY_VERSION,
            "redaction_version": REDACTION_VERSION,
        },
        browser_stream_id=browser_stream_id,
        runtime_id=runtime_id,
        document_attachment_id=attachment,
    )
    async with exporter:
        await exporter.open_stream(resume_from=0)
        await exporter.send_batch(
            [
                observation(
                    observation_type="baseline.snapshot",
                    runtime_id=runtime_id,
                    document_attachment_id=attachment,
                    source_sequence=1,
                    export_sequence=1,
                    payload={
                        "baseline_id": str(uuid7()),
                        "reason": "activation",
                        "route": {
                            "route_template": "/dashboard/conversion",
                            "unknown_route": False,
                            "segment_count": 2,
                            "has_query": False,
                            "has_fragment": False,
                            "params": [],
                        },
                        "landmarks": [],
                        "targets": [],
                        "surfaces": ["conversion_dashboard"],
                        "entities": [],
                        "node_budget_exhausted": False,
                    },
                    trace_id=None,
                    span_id=None,
                    target_id=None,
                    occurred_at=None,
                ),
                observation(
                    observation_type="request.started",
                    runtime_id=runtime_id,
                    document_attachment_id=attachment,
                    source_sequence=2,
                    export_sequence=2,
                    payload=request_started("req_saved_view"),
                    trace_id=trace_id,
                    span_id=client_span_id,
                    target_id=None,
                    occurred_at=None,
                ),
            ],
            batch_id=None,
        )

    activation_id, server_stream_id, token, _entry = await start_server_activation(
        telemetry_database, preview=preview, keys=keys
    )
    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select service_instance_id from public.telemetry_activations where id = %s",
            (activation_id,),
        )
        activation_row = await cur.fetchone()
    assert activation_row is not None

    server_span_id = secrets.token_hex(8)
    database_span_id = secrets.token_hex(8)
    start = time.time_ns()
    body = serialize(
        [
            SpanSpec(
                name="POST /api/saved-views",
                kind=Span.SpanKind.SPAN_KIND_SERVER,
                trace_id=trace_id,
                span_id=server_span_id,
                parent_span_id=client_span_id,
                start_unix_nano=start,
                end_unix_nano=start + 18_000_000,
                scope_name="@opentelemetry/instrumentation-http",
                attributes={
                    "http.route": "/api/saved-views",
                    "http.request.method": "POST",
                    "http.response.status_code": 201,
                    "utrace.operation.name": "saved_views.create",
                },
            ),
            SpanSpec(
                name="INSERT saved_views",
                kind=Span.SpanKind.SPAN_KIND_CLIENT,
                trace_id=trace_id,
                span_id=database_span_id,
                parent_span_id=server_span_id,
                start_unix_nano=start + 2_000_000,
                end_unix_nano=start + 9_000_000,
                scope_name="@opentelemetry/instrumentation-pg",
                attributes={"db.system.name": "postgresql", "db.operation.name": "INSERT"},
            ),
        ],
        service_name="shopalytics-api",
        service_namespace="shopalytics",
        service_version="2026.09.20",
        environment="preview",
    )
    response = await export_batch(
        ingest_server,
        token=token,
        activation_id=activation_id,
        server_stream_id=server_stream_id,
        service_instance_id=UUID(str(activation_row["service_instance_id"])),
        batch_sequence=1,
        body=body,
        span_count=2,
        policy_version=POLICY_VERSION,
        utrace_schema_version=UTRACE_SCHEMA_VERSION,
        semantic_convention_version=SEMANTIC_CONVENTION_VERSION,
        manifest_version=MANIFEST_VERSION,
        batch_id=None,
    )
    assert response.status_code == 200

    projection = await correlate_trace(
        telemetry_database,
        client_id=preview.case.client_id,
        activation_id=browser_activation_id,
        trace_id=trace_id,
    )
    assert projection.status == "complete"
    assert "browser_client_span" in projection.observed_components
    assert "server_span" in projection.observed_components
    assert "required_hop:database" in projection.observed_components
    assert projection.gaps == ()

    # Rerunning the same derivation is idempotent: the projection keeps its
    # revision because nothing about its content changed.
    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select revision from public.correlation_projections where trace_id = %s",
            (trace_id,),
        )
        before = await cur.fetchone()
    await correlate_trace(
        telemetry_database,
        client_id=preview.case.client_id,
        activation_id=browser_activation_id,
        trace_id=trace_id,
    )
    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select revision from public.correlation_projections where trace_id = %s",
            (trace_id,),
        )
        after = await cur.fetchone()
    assert before is not None
    assert after is not None
    assert before["revision"] == after["revision"]

    kinds = await evaluate_readiness(
        telemetry_database,
        client_id=preview.case.client_id,
        activation_id=browser_activation_id,
    )
    assert kinds == ("browser", "server", "correlation")
    for kind in kinds:
        projection_row = await current_readiness(
            telemetry_database, activation_id=browser_activation_id, kind=kind
        )
        assert projection_row is not None
        assert projection_row.ready is True
        assert projection_row.revision == 1

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select event_type, payload from public.events
            where stream_id = %s and event_type like 'telemetry_%%'
            order by sequence
            """,
            (preview.interview_session_id,),
        )
        events = await cur.fetchall()
    assert [str(row["event_type"]) for row in events] == [
        "telemetry_browser_readiness_reported",
        "telemetry_server_readiness_reported",
        "telemetry_correlation_readiness_reported",
    ]
    assert all("payload" in row for row in events)
    assert all(
        "observation_id" not in str(row["payload"]) for row in events
    ), "readiness facts carry derived state, never raw telemetry"


async def test_readiness_revisions_are_monotonic_per_activation(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Each evaluation appends the next revision and never rewrites an earlier one."""
    preview = await create_preview(
        telemetry_database,
        name=f"readiness-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    document = await activate_document(
        ingest_server,
        service_token=settings.front_door_service_token,
        session_credential=front_door_credential(settings, preview),
        public_preview_host=preview.origin_host,
        document_origin=preview.document_origin,
        document_nonce=secrets.token_hex(24),
        sdk_installation_id="shopalytics-browser-sdk",
        public_key=keys.attestation_public,
    )
    activation_id = UUID(str(document.attestation["attestation_id"]))

    revisions: list[int] = []
    for _ in range(3):
        await evaluate_readiness(
            telemetry_database,
            client_id=preview.case.client_id,
            activation_id=activation_id,
        )
        projection = await current_readiness(
            telemetry_database, activation_id=activation_id, kind="browser"
        )
        assert projection is not None
        revisions.append(projection.revision)
    assert revisions == [1, 2, 3]

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select revision, ready, failures from public.telemetry_readiness_projections
            where activation_id = %s and kind = 'browser' order by revision
            """,
            (activation_id,),
        )
        rows = await cur.fetchall()
    assert [int(str(row["revision"])) for row in rows] == [1, 2, 3]
    # Without a browser stream the path is not ready, and the reason is explicit.
    assert all(row["ready"] is False for row in rows)
    assert all(
        any(str(failure["code"]) == "no_browser_stream" for failure in row["failures"])
        for row in rows
    )


async def _open_document(
    *,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    preview: PreviewFixture,
) -> tuple[UUID, BrowserExporter]:
    """Attest one document the participant opened and return its exporter."""
    document = await activate_document(
        ingest_server,
        service_token=settings.front_door_service_token,
        session_credential=front_door_credential(settings, preview),
        public_preview_host=preview.origin_host,
        document_origin=preview.document_origin,
        document_nonce=secrets.token_hex(24),
        sdk_installation_id="shopalytics-browser-sdk",
        public_key=keys.attestation_public,
    )
    browser_stream_id = uuid7()
    return UUID(str(document.attestation["attestation_id"])), BrowserExporter(
        ingest_websocket_url,
        token=document.token,
        scope={
            "client_id": str(preview.case.client_id),
            "client_user_id": str(preview.case.client_user_id),
            "customer_account_id": str(preview.case.customer_account_id),
            "session_id": str(preview.interview_session_id),
            "preview_environment_id": str(preview.preview_environment_id),
            "preview_runtime_allocation_id": str(preview.preview_runtime_allocation_id),
            "browser_stream_id": str(browser_stream_id),
            "schema_version": "1",
            "policy_version": POLICY_VERSION,
            "redaction_version": REDACTION_VERSION,
        },
        browser_stream_id=browser_stream_id,
        runtime_id=uuid7(),
        document_attachment_id=UUID(str(document.attestation["document_attachment_id"])),
    )


def _baseline(exporter: BrowserExporter) -> dict[str, object]:
    return observation(
        observation_type="baseline.snapshot",
        runtime_id=exporter.runtime_id,
        document_attachment_id=exporter.document_attachment_id,
        source_sequence=1,
        export_sequence=1,
        payload={
            "baseline_id": str(uuid7()),
            "reason": "activation",
            "route": {
                "route_template": "/dashboard/conversion",
                "unknown_route": False,
                "segment_count": 2,
                "has_query": False,
                "has_fragment": False,
                "params": [],
            },
            "landmarks": [],
            "targets": [],
            "surfaces": ["conversion_dashboard"],
            "entities": [],
            "node_budget_exhausted": False,
        },
        trace_id=None,
        span_id=None,
        target_id=None,
        occurred_at=None,
    )


async def test_readiness_follows_the_current_document_and_its_complete_trace(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A document the participant left, and a later trace with fewer hops, keep readiness."""
    preview = await create_preview(
        telemetry_database,
        name=f"current-document-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    open_document = partial(
        _open_document,
        settings=settings,
        keys=keys,
        ingest_server=ingest_server,
        ingest_websocket_url=ingest_websocket_url,
        preview=preview,
    )
    _, earlier = await open_document()
    async with earlier:
        await earlier.open_stream(resume_from=0)
        await earlier.send_batch([_baseline(earlier)], batch_id=None)
        await earlier.end_stream(reason="pagehide", terminal=1)

    current_activation_id, current = await open_document()
    # The data request joins every required hop; the page load after it reaches
    # the server alone, as a product page that reads no data does.
    traces = {"data_request": secrets.token_hex(16), "page_load": secrets.token_hex(16)}
    client_spans = {name: secrets.token_hex(8) for name in traces}
    async with current:
        await current.open_stream(resume_from=0)
        await current.send_batch(
            [
                _baseline(current),
                *(
                    observation(
                        observation_type="request.started",
                        runtime_id=current.runtime_id,
                        document_attachment_id=current.document_attachment_id,
                        source_sequence=sequence,
                        export_sequence=sequence,
                        payload=request_started(f"req_{name}"),
                        trace_id=traces[name],
                        span_id=client_spans[name],
                        target_id=None,
                        occurred_at=None,
                    )
                    for sequence, name in ((2, "data_request"), (3, "page_load"))
                ),
            ],
            batch_id=None,
        )

    activation_id, server_stream_id, token, _entry = await start_server_activation(
        telemetry_database, preview=preview, keys=keys
    )
    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select service_instance_id from public.telemetry_activations where id = %s",
            (activation_id,),
        )
        activation_row = await cur.fetchone()
    assert activation_row is not None
    start = time.time_ns()
    server_spans = {name: secrets.token_hex(8) for name in traces}
    spans = [
        SpanSpec(
            name="POST /api/saved-views",
            kind=Span.SpanKind.SPAN_KIND_SERVER,
            trace_id=traces[name],
            span_id=server_spans[name],
            parent_span_id=client_spans[name],
            start_unix_nano=start,
            end_unix_nano=start + 18_000_000,
            scope_name="@opentelemetry/instrumentation-http",
            attributes={
                "http.route": "/api/saved-views",
                "http.request.method": "POST",
                "http.response.status_code": 200,
            },
        )
        for name in traces
    ]
    spans.append(
        SpanSpec(
            name="SELECT saved_views",
            kind=Span.SpanKind.SPAN_KIND_CLIENT,
            trace_id=traces["data_request"],
            span_id=secrets.token_hex(8),
            parent_span_id=server_spans["data_request"],
            start_unix_nano=start + 2_000_000,
            end_unix_nano=start + 9_000_000,
            scope_name="@opentelemetry/instrumentation-pg",
            attributes={"db.system.name": "postgresql", "db.operation.name": "SELECT"},
        )
    )
    response = await export_batch(
        ingest_server,
        token=token,
        activation_id=activation_id,
        server_stream_id=server_stream_id,
        service_instance_id=UUID(str(activation_row["service_instance_id"])),
        batch_sequence=1,
        body=serialize(
            spans,
            service_name="shopalytics-api",
            service_namespace="shopalytics",
            service_version="2026.09.20",
            environment="preview",
        ),
        span_count=len(spans),
        policy_version=POLICY_VERSION,
        utrace_schema_version=UTRACE_SCHEMA_VERSION,
        semantic_convention_version=SEMANTIC_CONVENTION_VERSION,
        manifest_version=MANIFEST_VERSION,
        batch_id=None,
    )
    assert response.status_code == 200

    for name, status in (("data_request", "complete"), ("page_load", "partial")):
        projection = await correlate_trace(
            telemetry_database,
            client_id=preview.case.client_id,
            activation_id=current_activation_id,
            trace_id=traces[name],
        )
        assert projection.status == status

    kinds = await evaluate_readiness(
        telemetry_database,
        client_id=preview.case.client_id,
        activation_id=current_activation_id,
    )
    for kind in kinds:
        current_projection = await current_readiness(
            telemetry_database, activation_id=current_activation_id, kind=kind
        )
        assert current_projection is not None
        assert current_projection.ready is True, kind
