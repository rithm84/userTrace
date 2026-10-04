"""Correlation and readiness schedule themselves from the receipt-commit path.

A receipt that commits without its derivation scheduled is a durable record
nothing will ever act on, so the Job is written in the same transaction as the
receipt. These tests drive the real ingress edges and then read the Job rows and
the outbox, which is the only place that atomicity is observable.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from uuid import UUID

from opentelemetry.proto.trace.v1.trace_pb2 import Span

from app.db.pool import Database
from app.ids import uuid7
from app.ingestion.jobs import CORRELATE_TRACE, EVALUATE_READINESS
from app.settings import Settings
from tests.ingestion.activation_client import activate_document
from tests.ingestion.browser_client import BrowserExporter, observation
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


async def _jobs(
    database: Database, *, job_type: str
) -> list[Mapping[str, object]]:
    """Return every Job of one type with its outbox row's state."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select jobs.id, jobs.state, jobs.input, jobs.concurrency_key,
                   outbox.state as outbox_state
            from public.jobs
            left join public.outbox on outbox.job_id = jobs.id
            where jobs.job_type = %s
            order by jobs.created_at
            """,
            (job_type,),
        )
        return list(await cur.fetchall())


async def _browser_stream(
    *,
    ingest_server: str,
    ingest_websocket_url: str,
    settings: Settings,
    keys: SigningKeys,
    preview: PreviewFixture,
    traces: tuple[tuple[str, str], ...],
) -> UUID:
    """Open a real browser stream and export one completion per named trace."""
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
    attachment = UUID(str(document.attestation["document_attachment_id"]))
    runtime_id = uuid7()
    browser_stream_id = uuid7()
    exporter = BrowserExporter(
        ingest_websocket_url,
        token=document.token,
        scope={
            "client_id": str(preview.case.client_id),
            "client_user_id": str(preview.case.client_user_id),
            "customer_account_id": str(preview.case.customer_account_id),
            "session_id": str(preview.interview_session_id),
            "preview_environment_id": str(preview.preview_environment_id),
            "preview_runtime_allocation_id": str(
                preview.preview_runtime_allocation_id
            ),
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
        records = []
        for index, (trace_id, span_id) in enumerate(traces, start=1):
            sequence = index
            records.append(
                observation(
                    observation_type="request.completed",
                    runtime_id=runtime_id,
                    document_attachment_id=attachment,
                    source_sequence=sequence,
                    export_sequence=sequence,
                    payload={
                        "browser_request_id": f"req_{sequence}",
                        "status": 200,
                        "outcome": "success",
                        "duration_ms": 18.0,
                        "response_size_bytes": 32,
                        "from_cache": False,
                    },
                    trace_id=trace_id,
                    span_id=span_id,
                    target_id=None,
                    occurred_at=None,
                )
            )
        await exporter.send_batch(records, batch_id=None)
    return UUID(str(document.attestation["attestation_id"]))


async def test_a_browser_receipt_schedules_one_correlation_run_per_trace(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Completed requests schedule correlation, deduplicated by trace."""
    preview = await create_preview(
        telemetry_database,
        name=f"sched-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    first_trace = secrets.token_hex(16)
    second_trace = secrets.token_hex(16)
    activation_id = await _browser_stream(
        ingest_server=ingest_server,
        ingest_websocket_url=ingest_websocket_url,
        settings=settings,
        keys=keys,
        preview=preview,
        traces=(
            (first_trace, secrets.token_hex(8)),
            # The same trace twice in one batch is one correlation run.
            (first_trace, secrets.token_hex(8)),
            (second_trace, secrets.token_hex(8)),
        ),
    )

    correlations = await _jobs(telemetry_database, job_type=CORRELATE_TRACE)
    scheduled_traces = sorted(
        str(dict(job["input"])["trace_id"])
        for job in correlations
        if isinstance(job["input"], dict)
    )
    assert scheduled_traces == sorted([first_trace, second_trace])
    for job in correlations:
        assert job["state"] == "ready"
        # The Job and its outbox row commit together: a wake-up that was never
        # written could leave the derivation stranded.
        assert job["outbox_state"] in {"pending", "enqueued"}
        assert str(dict(job["input"])["activation_id"]) == str(activation_id)

    # Opening the stream schedules readiness once for the session, and a second
    # trigger while that run is still pending does not queue a duplicate.
    readiness = await _jobs(telemetry_database, job_type=EVALUATE_READINESS)
    assert len(readiness) == 1
    assert (
        readiness[0]["concurrency_key"]
        == f"{EVALUATE_READINESS}:{preview.interview_session_id}"
    )


async def test_a_server_export_schedules_correlation_on_the_browser_activation(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Server spans schedule the run keyed by the browser half they join onto."""
    preview = await create_preview(
        telemetry_database,
        name=f"sched-otlp-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    trace_id = secrets.token_hex(16)
    client_span_id = secrets.token_hex(8)
    browser_activation_id = await _browser_stream(
        ingest_server=ingest_server,
        ingest_websocket_url=ingest_websocket_url,
        settings=settings,
        keys=keys,
        preview=preview,
        traces=((trace_id, client_span_id),),
    )

    activation_id, server_stream_id, token, _entry = await start_server_activation(
        telemetry_database, preview=preview, keys=keys
    )
    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select service_instance_id from public.telemetry_activations where id = %s",
            (activation_id,),
        )
        row = await cur.fetchone()
    assert row is not None

    server_trace = secrets.token_hex(16)
    start = 1_780_000_000_000_000_000
    body = serialize(
        [
            SpanSpec(
                name="GET /api/saved-views/:view_id",
                kind=Span.SpanKind.SPAN_KIND_SERVER,
                trace_id=server_trace,
                span_id=secrets.token_hex(8),
                parent_span_id=None,
                start_unix_nano=start,
                end_unix_nano=start + 12_000_000,
                scope_name="@opentelemetry/instrumentation-http",
                attributes={
                    "http.route": "/api/saved-views/:view_id",
                    "http.request.method": "GET",
                    "http.response.status_code": 200,
                },
            )
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
        service_instance_id=UUID(str(row["service_instance_id"])),
        batch_sequence=1,
        body=body,
        span_count=1,
        policy_version=POLICY_VERSION,
        utrace_schema_version=UTRACE_SCHEMA_VERSION,
        semantic_convention_version=SEMANTIC_CONVENTION_VERSION,
        manifest_version=MANIFEST_VERSION,
        batch_id=None,
    )
    assert response.status_code == 200

    correlations = await _jobs(telemetry_database, job_type=CORRELATE_TRACE)
    by_trace = {
        str(dict(job["input"])["trace_id"]): job
        for job in correlations
        if isinstance(job["input"], dict)
    }
    assert set(by_trace) == {trace_id, server_trace}
    # The projection is keyed by the browser activation, because that is the half
    # the server span has to join onto.
    assert (
        str(dict(by_trace[server_trace]["input"])["activation_id"])
        == str(browser_activation_id)
    )


async def test_the_stale_readiness_scan_reschedules_an_expired_projection(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    auth_users: dict[str, SignedInUser],
) -> None:
    """The pg_cron scan re-evaluates an activation whose projection expired."""
    preview = await create_preview(
        telemetry_database,
        name=f"scan-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    activation_id, _stream, _token, _entry = await start_server_activation(
        telemetry_database, preview=preview, keys=keys
    )
    # The activation already scheduled one readiness run; draining that Job is
    # what leaves the concurrency key free for the scan to use.
    async with telemetry_database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.jobs set state = 'succeeded' where job_type = %s",
            (EVALUATE_READINESS,),
        )
        await cur.execute(
            """
            insert into public.telemetry_readiness_projections (
                id, client_id, activation_id, interview_session_id, kind, revision,
                ready, contributing_resource_ids, accepted_watermarks, failures,
                checked_at, expires_at
            )
            values (
                %s, %s, %s, %s, 'server', 1, true, '[]'::jsonb, '{}'::jsonb,
                '[]'::jsonb, now() - interval '10 minutes',
                now() - interval '9 minutes'
            )
            """,
            (
                uuid7(),
                preview.case.client_id,
                activation_id,
                preview.interview_session_id,
            ),
        )

    async with telemetry_database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "select utrace.rescan_stale_telemetry_readiness(50) as rescheduled"
        )
        scanned = await cur.fetchone()
    assert scanned is not None
    assert int(str(scanned["rescheduled"])) >= 1

    rescheduled = [
        job
        for job in await _jobs(telemetry_database, job_type=EVALUATE_READINESS)
        if job["state"] == "ready"
    ]
    assert len(rescheduled) == 1
    assert rescheduled[0]["outbox_state"] in {"pending", "enqueued"}

    # A fresh projection is not stale, so a second scan schedules nothing more.
    async with telemetry_database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "select utrace.rescan_stale_telemetry_readiness(50) as rescheduled"
        )
        again = await cur.fetchone()
    assert again is not None
    assert int(str(again["rescheduled"])) == 0


async def test_a_browser_activation_establishes_the_not_ready_baseline(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Activating a document schedules readiness before any stream exists."""
    preview = await create_preview(
        telemetry_database,
        name=f"activate-{uuid7().hex[:8]}",
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
    readiness = await _jobs(telemetry_database, job_type=EVALUATE_READINESS)
    assert len(readiness) == 1
    assert str(dict(readiness[0]["input"])["activation_id"]) == str(
        document.attestation["attestation_id"]
    )
    assert dict(readiness[0]["input"])["trigger"] == "browser_activation_created"
