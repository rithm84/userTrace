"""The browser edge: durable receipt before acknowledgement, and its integrity rules."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, replace
from uuid import UUID

import pytest
import pytest_asyncio
from psycopg.types.json import Jsonb
from websockets.exceptions import ConnectionClosedError

from app.db.pool import Database
from app.ids import uuid7
from app.ingestion import browser_gateway
from app.settings import Settings
from tests.ingestion.activation_client import ActivatedDocument, activate_document
from tests.ingestion.browser_client import BrowserExporter, observation, request_started
from tests.ingestion.factories import (
    POLICY_VERSION,
    REDACTION_VERSION,
    PreviewFixture,
    create_preview,
    front_door_credential,
)
from tests.ingestion.keys import SigningKeys
from tests.supabase_auth import SignedInUser


@dataclass(frozen=True, slots=True)
class ActiveBrowserStream:
    """One activated document with the scope its exporter presents."""

    preview: PreviewFixture
    document: ActivatedDocument
    scope: Mapping[str, object]
    browser_stream_id: UUID
    runtime_id: UUID
    document_attachment_id: UUID


async def _activate(
    database: Database,
    settings: Settings,
    keys: SigningKeys,
    base_url: str,
    *,
    name: str,
    authorizing_auth_user_id: UUID,
) -> ActiveBrowserStream:
    """Run the activation gate and build the scope the exporter will present."""
    preview = await create_preview(
        database, name=name, authorizing_auth_user_id=authorizing_auth_user_id
    )
    document = await activate_document(
        base_url,
        service_token=settings.front_door_service_token,
        session_credential=front_door_credential(settings, preview),
        public_preview_host=preview.origin_host,
        document_origin=preview.document_origin,
        document_nonce=secrets.token_hex(24),
        sdk_installation_id="shopalytics-browser-sdk",
        public_key=keys.attestation_public,
    )
    browser_stream_id = uuid7()
    runtime_id = uuid7()
    attachment = UUID(str(document.attestation["document_attachment_id"]))
    scope: Mapping[str, object] = {
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
    }
    return ActiveBrowserStream(
        preview=preview,
        document=document,
        scope=scope,
        browser_stream_id=browser_stream_id,
        runtime_id=runtime_id,
        document_attachment_id=attachment,
    )


@pytest_asyncio.fixture
async def active_stream(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> AsyncIterator[ActiveBrowserStream]:
    """One activated document ready to export."""
    yield await _activate(
        telemetry_database,
        settings,
        keys,
        ingest_server,
        name=f"browser-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )


def _exporter(stream: ActiveBrowserStream, url: str) -> BrowserExporter:
    """Build an exporter for one activated document."""
    return BrowserExporter(
        url,
        token=stream.document.token,
        scope=stream.scope,
        browser_stream_id=stream.browser_stream_id,
        runtime_id=stream.runtime_id,
        document_attachment_id=stream.document_attachment_id,
    )


def _record(stream: ActiveBrowserStream, sequence: int) -> dict[str, object]:
    """Build one interaction record at a given export sequence."""
    return observation(
        observation_type="interaction.activation",
        runtime_id=stream.runtime_id,
        document_attachment_id=stream.document_attachment_id,
        source_sequence=sequence,
        export_sequence=sequence,
        payload={
            "actor": "user",
            "activation_kind": "click",
            "role": "button",
            "label": None,
            "entity_ref": None,
            "surface": "conversion_dashboard",
        },
        trace_id=None,
        span_id=None,
        target_id="tgt_1_abcdef01",
        occurred_at=None,
    )


async def test_receipt_is_durable_before_the_acknowledgement(
    active_stream: ActiveBrowserStream,
    telemetry_database: Database,
    ingest_websocket_url: str,
) -> None:
    """The acknowledged batch is already a committed receipt and committed records."""
    async with _exporter(active_stream, ingest_websocket_url) as exporter:
        accepted = await exporter.open_stream(resume_from=0)
        assert accepted["kind"] == "stream.accepted"
        assert accepted["accepted_through_export_sequence"] == 0

        records = [_record(active_stream, 1), _record(active_stream, 2)]
        ack = await exporter.send_batch(records, batch_id=None)

    assert ack["kind"] == "ack"
    assert ack["accepted_through_export_sequence"] == 2

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, outcome, stage, record_count, byte_count, raw_artifact_id
            from public.ingestion_receipts
            where source_stream_id = %s
            """,
            (active_stream.browser_stream_id,),
        )
        receipts = await cur.fetchall()
        await cur.execute(
            """
            select count(*) as total from public.browser_observations
            where browser_stream_id = %s
            """,
            (active_stream.browser_stream_id,),
        )
        stored = await cur.fetchone()
        await cur.execute(
            "select stage from public.ingestion_receipt_stages "
            "where ingestion_receipt_id = %s order by entered_at",
            (receipts[0]["id"],),
        )
        stages = [str(row["stage"]) for row in await cur.fetchall()]
    assert len(receipts) == 1
    assert stages == [
        "received",
        "authenticated",
        "source_validated",
        "normalized",
        "indexed",
    ]
    assert receipts[0]["outcome"] == "accepted"
    assert receipts[0]["stage"] == "indexed"
    assert receipts[0]["record_count"] == 2
    assert receipts[0]["raw_artifact_id"] is not None
    assert stored is not None
    assert stored["total"] == 2


async def test_a_stalled_commit_does_not_block_the_resend_of_its_batch(
    active_stream: ActiveBrowserStream,
    telemetry_database: Database,
    ingest_websocket_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres ends a batch transaction that stops committing, releasing its locks.

    The page's exporter reconnects on a new stream and resends the same records
    when an acknowledgement never comes; that resend must commit instead of
    waiting behind the stalled one.
    """
    monkeypatch.setattr(browser_gateway, "_IDLE_IN_TRANSACTION_TIMEOUT", "1s")
    insert = browser_gateway.store_observation
    stalled = asyncio.Event()

    async def stall_after_first_insert(*args: object, **kwargs: object) -> bool:
        stored = await insert(*args, **kwargs)  # type: ignore[arg-type]
        if not stalled.is_set():
            stalled.set()
            await asyncio.sleep(4)
        return stored

    monkeypatch.setattr(browser_gateway, "store_observation", stall_after_first_insert)
    records = [_record(active_stream, 1), _record(active_stream, 2)]
    resent_stream_id = uuid7()
    resend = replace(
        active_stream,
        browser_stream_id=resent_stream_id,
        scope={**active_stream.scope, "browser_stream_id": str(resent_stream_id)},
    )

    async with _exporter(active_stream, ingest_websocket_url) as first:
        await first.open_stream(resume_from=0)
        stalled_batch = asyncio.create_task(first.send_batch(records, batch_id=None))
        while not stalled.is_set():
            await asyncio.sleep(0.05)
        started = time.monotonic()
        async with _exporter(resend, ingest_websocket_url) as second:
            await second.open_stream(resume_from=0)
            ack = await second.send_batch(records, batch_id=None)
        resent_in = time.monotonic() - started
        with pytest.raises(ConnectionClosedError):
            await stalled_batch

    assert ack["kind"] == "ack"
    assert ack["accepted_through_export_sequence"] == 2
    assert resent_in < 3
    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select browser_stream_id from public.browser_observations "
            "where activation_id = %s",
            (UUID(str(active_stream.document.attestation["attestation_id"])),),
        )
        stored = [row["browser_stream_id"] for row in await cur.fetchall()]
    assert stored == [resent_stream_id, resent_stream_id]


async def test_acknowledgement_holds_at_a_gap_and_advances_when_it_is_filled(
    active_stream: ActiveBrowserStream,
    telemetry_database: Database,
    ingest_websocket_url: str,
) -> None:
    """A missing sequence leaves later records durable without a continuous claim."""
    async with _exporter(active_stream, ingest_websocket_url) as exporter:
        await exporter.open_stream(resume_from=0)
        first = await exporter.send_batch(
            [_record(active_stream, 1), _record(active_stream, 2)], batch_id=None
        )
        assert first["accepted_through_export_sequence"] == 2

        held = await exporter.send_batch(
            [_record(active_stream, 5), _record(active_stream, 6)], batch_id=None
        )
        assert held["accepted_through_export_sequence"] == 2

        filled = await exporter.send_batch(
            [_record(active_stream, 3), _record(active_stream, 4)], batch_id=None
        )
        assert filled["accepted_through_export_sequence"] == 6

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select accepted_through_export_sequence, highest_received_export_sequence
            from public.browser_streams where id = %s
            """,
            (active_stream.browser_stream_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["accepted_through_export_sequence"] == 6
    assert row["highest_received_export_sequence"] == 6


async def test_a_paused_stream_reopens_at_its_watermark(
    active_stream: ActiveBrowserStream,
    telemetry_database: Database,
    ingest_websocket_url: str,
) -> None:
    """Resume keeps the same stream and continues from the durable acknowledgement."""
    async with _exporter(active_stream, ingest_websocket_url) as exporter:
        await exporter.open_stream(resume_from=0)
        ack = await exporter.send_batch([_record(active_stream, 1)], batch_id=None)
        assert ack["accepted_through_export_sequence"] == 1
        await exporter.end_stream(reason="paused", terminal=1)

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state, terminal_export_sequence from public.browser_streams "
            "where id = %s",
            (active_stream.browser_stream_id,),
        )
        paused = await cur.fetchone()
    assert paused is not None
    assert paused["state"] == "paused"

    async with _exporter(active_stream, ingest_websocket_url) as resumed:
        accepted = await resumed.open_stream(resume_from=1)
        assert accepted["kind"] == "stream.accepted"
        assert accepted["accepted_through_export_sequence"] == 1
        ack = await resumed.send_batch([_record(active_stream, 2)], batch_id=None)
        assert ack["accepted_through_export_sequence"] == 2

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as total from public.browser_observations "
            "where browser_stream_id = %s",
            (active_stream.browser_stream_id,),
        )
        total = await cur.fetchone()
    assert total is not None
    assert total["total"] == 2


async def test_an_exact_retry_is_acknowledged_without_new_evidence(
    active_stream: ActiveBrowserStream,
    telemetry_database: Database,
    ingest_websocket_url: str,
) -> None:
    """Retries preserve their IDs and sequences; ingestion deduplicates them."""
    batch_id = uuid7()
    records = [_record(active_stream, 1), _record(active_stream, 2)]
    async with _exporter(active_stream, ingest_websocket_url) as exporter:
        await exporter.open_stream(resume_from=0)
        first = await exporter.send_batch(records, batch_id=batch_id)
        assert first["accepted_through_export_sequence"] == 2
        retry = await exporter.send_batch(records, batch_id=batch_id)
        assert retry["kind"] == "ack"
        assert retry["accepted_through_export_sequence"] == 2

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as total from public.ingestion_receipts "
            "where source_stream_id = %s",
            (active_stream.browser_stream_id,),
        )
        receipts = await cur.fetchone()
        await cur.execute(
            "select count(*) as total from public.browser_observations "
            "where browser_stream_id = %s",
            (active_stream.browser_stream_id,),
        )
        stored = await cur.fetchone()
    assert receipts is not None
    assert stored is not None
    assert receipts["total"] == 1
    assert stored["total"] == 2


async def test_reusing_an_observation_id_with_new_content_fails_the_stream(
    active_stream: ActiveBrowserStream,
    telemetry_database: Database,
    ingest_websocket_url: str,
) -> None:
    """Both versions stay diagnostics; the later arrival is not chosen."""
    original = _record(active_stream, 1)
    conflicting = dict(original)
    conflicting["export_sequence"] = 2
    conflicting["source_sequence"] = 2
    conflicting["payload"] = {
        "actor": "agent",
        "activation_kind": "keyboard",
        "role": "button",
        "label": None,
        "entity_ref": None,
        "surface": "conversion_dashboard",
    }

    async with _exporter(active_stream, ingest_websocket_url) as exporter:
        await exporter.open_stream(resume_from=0)
        await exporter.send_batch([original], batch_id=None)
        refusal = await exporter.send_batch([conflicting], batch_id=None)

    assert refusal["kind"] == "error"
    assert refusal["code"] == "duplicate_conflict"
    assert refusal["retryable"] is False

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select identity_kind, first_content_sha256, conflicting_content_sha256
            from public.telemetry_identity_conflicts
            where source_stream_id = %s
            """,
            (active_stream.browser_stream_id,),
        )
        conflicts = await cur.fetchall()
        await cur.execute(
            "select state from public.browser_streams where id = %s",
            (active_stream.browser_stream_id,),
        )
        stream = await cur.fetchone()
        await cur.execute(
            """
            select payload from public.browser_observations
            where observation_id = %s
            """,
            (UUID(str(original["observation_id"])),),
        )
        kept = await cur.fetchone()
    assert len(conflicts) == 1
    assert conflicts[0]["identity_kind"] == "observation_id"
    assert (
        conflicts[0]["first_content_sha256"]
        != conflicts[0]["conflicting_content_sha256"]
    )
    assert stream is not None
    assert stream["state"] == "failed"
    assert kept is not None
    assert kept["payload"]["actor"] == "user"


async def test_a_scope_claim_that_disagrees_with_the_activation_is_refused(
    active_stream: ActiveBrowserStream,
    ingest_websocket_url: str,
) -> None:
    """A payload identity never overrides the authenticated activation."""
    forged = dict(active_stream.scope)
    forged["customer_account_id"] = str(uuid7())
    exporter = BrowserExporter(
        ingest_websocket_url,
        token=active_stream.document.token,
        scope=forged,
        browser_stream_id=active_stream.browser_stream_id,
        runtime_id=active_stream.runtime_id,
        document_attachment_id=active_stream.document_attachment_id,
    )
    async with exporter:
        refusal = await exporter.open_stream(resume_from=0)
    assert refusal["kind"] == "error"
    assert refusal["code"] == "duplicate_conflict"


async def test_an_unknown_credential_is_refused_before_any_stream_opens(
    ingest_websocket_url: str,
) -> None:
    """The edge refuses an unknown credential with a non-retryable error frame."""
    exporter = BrowserExporter(
        ingest_websocket_url,
        token=secrets.token_urlsafe(32),
        scope={},
        browser_stream_id=uuid7(),
        runtime_id=uuid7(),
        document_attachment_id=uuid7(),
    )
    async with exporter:
        refusal = await exporter.receive()
    assert refusal["kind"] == "error"
    assert refusal["code"] == "credential_rejected"
    assert refusal["retryable"] is False


async def test_a_forbidden_value_is_refused_and_nothing_is_written(
    active_stream: ActiveBrowserStream,
    telemetry_database: Database,
    ingest_websocket_url: str,
) -> None:
    """A payload with platform-excluded material reaches no store at all."""
    unsafe = observation(
        observation_type="product.milestone",
        runtime_id=active_stream.runtime_id,
        document_attachment_id=active_stream.document_attachment_id,
        source_sequence=1,
        export_sequence=1,
        payload={
            "milestone_id": "view_saved",
            "severity": "low",
            "priority": "p2",
            "entity_ref": None,
            "attributes": {"authorization": "Bearer sk-live-abcdefghijklmnop"},
            "actor": "user",
        },
        trace_id=None,
        span_id=None,
        target_id=None,
        occurred_at=None,
    )
    async with _exporter(active_stream, ingest_websocket_url) as exporter:
        await exporter.open_stream(resume_from=0)
        refusal = await exporter.send_batch([unsafe], batch_id=None)

    assert refusal["kind"] == "error"
    assert refusal["code"] == "unsafe_data_detected"

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as total from public.ingestion_receipts "
            "where source_stream_id = %s",
            (active_stream.browser_stream_id,),
        )
        receipts = await cur.fetchone()
        await cur.execute(
            "select count(*) as total from public.telemetry_artifacts where client_id = %s",
            (active_stream.preview.case.client_id,),
        )
        artifacts = await cur.fetchone()
        await cur.execute(
            "select code, stage from public.ingestion_failures where activation_id = %s",
            (UUID(str(active_stream.document.attestation["attestation_id"])),),
        )
        failures = await cur.fetchall()
    assert receipts is not None
    assert artifacts is not None
    assert receipts["total"] == 0
    assert artifacts["total"] == 0
    assert len(failures) == 1
    assert failures[0]["code"] == "unsafe_data_detected"


async def test_backpressure_pauses_without_advancing_the_acknowledgement(
    active_stream: ActiveBrowserStream,
    telemetry_database: Database,
    ingest_websocket_url: str,
) -> None:
    """A quota limit produces flow control, never a silent drop or a false ack."""
    async with telemetry_database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.telemetry_activations set limits = limits || %s where id = %s",
            (
                Jsonb({"max_records_per_second": 1, "max_bytes_per_second": 1}),
                UUID(str(active_stream.document.attestation["attestation_id"])),
            ),
        )

    async with _exporter(active_stream, ingest_websocket_url) as exporter:
        await exporter.open_stream(resume_from=0)
        answer = await exporter.send_batch(
            [_record(active_stream, 1), _record(active_stream, 2)], batch_id=None
        )
    assert answer["kind"] == "flow_control"
    assert answer["state"] == "pause"
    assert answer["retry_after_ms"] > 0

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select accepted_through_export_sequence from public.browser_streams "
            "where id = %s",
            (active_stream.browser_stream_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["accepted_through_export_sequence"] == 0


async def test_paired_request_records_keep_their_browser_request_identity(
    active_stream: ActiveBrowserStream,
    telemetry_database: Database,
    ingest_websocket_url: str,
) -> None:
    """A request record's paired identity survives normalization for later joins."""
    trace_id = secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    record = observation(
        observation_type="request.started",
        runtime_id=active_stream.runtime_id,
        document_attachment_id=active_stream.document_attachment_id,
        source_sequence=1,
        export_sequence=1,
        payload=request_started("req_1"),
        trace_id=trace_id,
        span_id=span_id,
        target_id=None,
        occurred_at=None,
    )
    async with _exporter(active_stream, ingest_websocket_url) as exporter:
        await exporter.open_stream(resume_from=0)
        ack = await exporter.send_batch([record], batch_id=None)
    assert ack["accepted_through_export_sequence"] == 1

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select browser_request_id, trace_id, span_id from public.browser_observations "
            "where browser_stream_id = %s",
            (active_stream.browser_stream_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["browser_request_id"] == "req_1"
    assert row["trace_id"] == trace_id
    assert row["span_id"] == span_id


async def test_an_explicit_gap_record_is_stored_and_reported(
    active_stream: ActiveBrowserStream,
    telemetry_database: Database,
    ingest_websocket_url: str,
) -> None:
    """A dropped low-priority range stays explicit, with its baseline follow-up."""
    gap = observation(
        observation_type="observation.gap",
        runtime_id=active_stream.runtime_id,
        document_attachment_id=active_stream.document_attachment_id,
        source_sequence=2,
        export_sequence=2,
        payload={
            "gap_runtime_id": str(active_stream.runtime_id),
            "gap_first_source_sequence": 5,
            "gap_last_source_sequence": 9,
            "count": 5,
            "observation_class": "low",
            "reason": "buffer_pressure",
            "baseline_follows": True,
        },
        trace_id=None,
        span_id=None,
        target_id=None,
        occurred_at=None,
    )
    async with _exporter(active_stream, ingest_websocket_url) as exporter:
        await exporter.open_stream(resume_from=0)
        ack = await exporter.send_batch(
            [_record(active_stream, 1), gap], batch_id=None
        )
    assert ack["accepted_through_export_sequence"] == 2

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select first_source_sequence, last_source_sequence, record_count,
                   observation_class, reason, baseline_follows, resolved
            from public.browser_stream_gaps where browser_stream_id = %s
            """,
            (active_stream.browser_stream_id,),
        )
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["first_source_sequence"] == 5
    assert rows[0]["last_source_sequence"] == 9
    assert rows[0]["record_count"] == 5
    assert rows[0]["observation_class"] == "low"
    assert rows[0]["reason"] == "buffer_pressure"
    assert rows[0]["baseline_follows"] is True
    assert rows[0]["resolved"] is False


async def test_an_acknowledgement_beyond_the_durable_watermark_is_refused(
    active_stream: ActiveBrowserStream,
    ingest_websocket_url: str,
) -> None:
    """A resume that claims more than ingestion committed is an integrity failure."""
    async with _exporter(active_stream, ingest_websocket_url) as exporter:
        refusal = await exporter.open_stream(resume_from=12)
    assert refusal["kind"] == "error"
    assert refusal["code"] == "duplicate_conflict"


async def test_a_revoked_credential_stops_new_writes_immediately(
    active_stream: ActiveBrowserStream,
    telemetry_database: Database,
    ingest_websocket_url: str,
) -> None:
    """Revocation is durable, so it takes effect before the credential expires."""
    async with telemetry_database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.telemetry_credentials set state = 'revoked', "
            "revoked_at = now(), revocation_reason = 'session_closed' "
            "where activation_id = %s",
            (UUID(str(active_stream.document.attestation["attestation_id"])),),
        )
    async with _exporter(active_stream, ingest_websocket_url) as exporter:
        refusal = await exporter.receive()
    assert refusal["kind"] == "error"
    assert refusal["code"] == "credential_rejected"
