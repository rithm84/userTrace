"""Observing one running Server SDK from the control plane's durable state.

The control-socket journey asserts on rows, not on the service's own claims, so
these helpers read the activation, the health reports, the spans, and the
control commands the journey produced. ``drain_ingestion_jobs`` runs the
derivation the ingress path scheduled through the real worker contract, because
the suite runs no worker process of its own.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable, Mapping
from uuid import UUID

import httpx

from app.db.pool import Database
from app.execution.handlers import build_handlers
from app.execution.jobs import claim_job, complete_job_run
from app.ids import uuid7
from app.ingestion.jobs import CORRELATE_TRACE, EVALUATE_READINESS
from app.settings import Settings
from tests.ingestion.activation_client import activate_document
from tests.ingestion.browser_client import BrowserExporter, observation
from tests.ingestion.factories import (
    POLICY_VERSION,
    REDACTION_VERSION,
    PreviewFixture,
    front_door_credential,
)
from tests.ingestion.keys import SigningKeys
from tests.ingestion.server_sdk_harness import APP_PORT, SdkProcess

__all__ = [
    "APP_ORIGIN",
    "active_server_activation",
    "call_product_route",
    "command_acknowledgement",
    "drain_ingestion_jobs",
    "issue_command",
    "latest_health",
    "send_browser_half",
    "span_count",
    "wait_for",
]

APP_ORIGIN = f"http://127.0.0.1:{APP_PORT}"

#: The SDK reports health every five seconds, so a wait for anything a health
#: report carries has to outlast several of those ticks on a loaded machine.
_DEADLINE_SECONDS = 120.0


async def wait_for[T](
    description: str,
    probe: Callable[[], Awaitable[T | None]],
    *,
    process: SdkProcess,
) -> T:
    """Poll until the probe returns a value, or fail with the service's output."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _DEADLINE_SECONDS
    while loop.time() < deadline:
        outcome = await probe()
        if outcome is not None:
            return outcome
        await asyncio.sleep(0.25)
    raise AssertionError(f"timed out waiting for {description}\n{process.output()}")


async def drain_ingestion_jobs(database: Database) -> int:
    """Run every claimable ingestion Job through the real worker contract.

    The suite runs no worker process, so the derivation the ingress path
    scheduled is executed here with the same claim, handler, and completion the
    worker uses. Nothing is shortcut: an unscheduled Job cannot be drained.
    """
    handlers = build_handlers()
    drained = 0
    while True:
        claimed = await claim_job(
            database,
            job_types=[CORRELATE_TRACE, EVALUATE_READINESS],
            worker_id="ingestion-control-test-worker",
            lease_seconds=60,
        )
        if claimed is None:
            return drained
        await handlers[claimed.job_type](database, claimed)
        await complete_job_run(
            database,
            job_id=claimed.job_id,
            job_run_id=claimed.job_run_id,
            fencing_token=claimed.fencing_token,
        )
        drained += 1


async def active_server_activation(
    database: Database, *, preview: PreviewFixture
) -> Mapping[str, object] | None:
    """Return the active server activation the runtime's own SDK committed."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select activations.id, activations.server_stream_id,
                   activations.service_key, activations.required_hops,
                   activations.interview_session_id,
                   activations.preview_environment_id,
                   allocations.preview_entry_token
            from public.telemetry_activations as activations
            join public.preview_runtime_allocations as allocations
              on allocations.id = activations.preview_runtime_allocation_id
            where activations.kind = 'server'
              and activations.state = 'active'
              and activations.preview_runtime_allocation_id = %s
            """,
            (preview.preview_runtime_allocation_id,),
        )
        row = await cur.fetchone()
    if row is None or row["preview_entry_token"] is None:
        return None
    return row


async def latest_health(
    database: Database, *, activation_id: UUID
) -> Mapping[str, object] | None:
    """Return the newest health report for one activation."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select lifecycle_state, probe_result, buffer, sdk_version, reported_at
            from public.server_health_reports
            where activation_id = %s order by reported_at desc limit 1
            """,
            (activation_id,),
        )
        return await cur.fetchone()


async def span_count(database: Database, *, trace_id: str) -> int:
    """Return how many canonical server spans one trace has committed."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as spans from public.server_spans where trace_id = %s",
            (trace_id,),
        )
        row = await cur.fetchone()
    return 0 if row is None else int(str(row["spans"]))


async def call_product_route(
    *, trace_id: str, client_span_id: str, entry_token: str
) -> int:
    """Drive the one registered product route with a browser trace context."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{APP_ORIGIN}/api/saved-views/1",
            headers={
                "traceparent": f"00-{trace_id}-{client_span_id}-01",
                "x-utrace-preview-entry": entry_token,
            },
        )
    return response.status_code


async def issue_command(
    base_url: str, *, service_token: str, activation_id: UUID, kind: str, reason: str
) -> UUID:
    """Send one pause, resume, or terminate through the internal command route."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{base_url}/v1/server-sdk/control-commands",
            headers={"authorization": f"Bearer {service_token}"},
            json={
                "schema_version": "utrace-server-control-command-request-v1",
                "activation_id": str(activation_id),
                "kind": kind,
                "reason": reason,
            },
        )
    assert response.status_code == 200, response.text
    return UUID(str(response.json()["command_id"]))


async def command_acknowledgement(
    database: Database, *, command_id: UUID
) -> str | None:
    """Return the lifecycle state the SDK acknowledged one command with."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select acknowledged_lifecycle_state from public.server_control_commands
            where id = %s
            """,
            (command_id,),
        )
        row = await cur.fetchone()
    if row is None or row["acknowledged_lifecycle_state"] is None:
        return None
    return str(row["acknowledged_lifecycle_state"])


async def send_browser_half(
    *,
    ingest_server: str,
    ingest_websocket_url: str,
    settings: Settings,
    keys: SigningKeys,
    preview: PreviewFixture,
    trace_id: str,
    client_span_id: str,
) -> None:
    """Open a real browser stream and export the CLIENT half of one operation."""
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
                    observation_type="request.completed",
                    runtime_id=runtime_id,
                    document_attachment_id=attachment,
                    source_sequence=2,
                    export_sequence=2,
                    payload={
                        "browser_request_id": "req_saved_view_read",
                        "status": 200,
                        "outcome": "success",
                        "duration_ms": 24.0,
                        "response_size_bytes": 64,
                        "from_cache": False,
                    },
                    trace_id=trace_id,
                    span_id=client_span_id,
                    target_id=None,
                    occurred_at=None,
                ),
            ],
            batch_id=None,
        )

