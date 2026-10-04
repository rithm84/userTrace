"""The OTLP receiver: one authenticated export request becomes durable evidence.

The request is decompressed within bounds, authenticated, validated, checked
against the non-overridable platform exclusions in memory, and only then written.
The receipt, the raw batch artifact, the canonical spans, and the stream
watermark all commit in one transaction, so a full-success response can never
outrun durability.

uTrace never emits partial success. If any span makes the batch invalid, the
whole request fails so the server SDK can fail the stream explicitly.

Contract: docs/arch-components/telemetry-and-evidence.md#durable-receipt-and-acknowledgement
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.db.pool import Database
from app.ids import uuid7
from app.ingestion.artifacts import ArtifactStore
from app.ingestion.credentials import resolve_credential
from app.ingestion.errors import (
    IdentityReuseError,
    IngestionError,
    IntegrityViolationError,
    SourceValidationError,
)
from app.ingestion.journal import (
    ReceiptDraft,
    advance_stage,
    find_receipt,
    record_failure,
    write_receipt,
)
from app.ingestion.normalize import CANONICAL_SCHEMA_VERSION
from app.ingestion.otlp import (
    ExportBatchEnvelope,
    NormalizedSpan,
    decode_export_request,
    decompress,
    envelope_from_headers,
)
from app.ingestion.rate_budget import RateBudget
from app.ingestion.scheduling import (
    browser_activation_for_session,
    schedule_correlation,
)
from app.ingestion.server_activation import ServerActivation, load_server_activation
from app.ingestion.server_records import (
    advance_batch_sequence,
    assert_trace_not_claimed_elsewhere,
    record_server_identity_conflict,
    store_span,
)
from app.logging import get_logger

__all__ = ["AcceptedExport", "accept_export"]

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AcceptedExport:
    """What one accepted OTLP request committed."""

    ingestion_receipt_id: UUID
    activation_id: UUID
    interview_session_id: UUID
    accepted_through_batch_sequence: int
    stored_spans: int
    duplicate_spans: int
    trace_ids: tuple[str, ...]
    replayed: bool


async def accept_export(
    database: Database,
    *,
    artifacts: ArtifactStore,
    token: str,
    headers: Mapping[str, str],
    body: bytes,
    content_encoding: str | None,
    rate_budget: RateBudget,
) -> AcceptedExport:
    """Accept one OTLP export request, or refuse the whole batch.

    ``rate_budget`` is supplied by the caller so one process's budget covers the
    whole server edge rather than resetting per request.
    """
    envelope = envelope_from_headers(headers)
    resolved = await resolve_credential(database, token=token, expected_kind="server")
    activation = await load_server_activation(
        database, activation_id=resolved.activation_id
    )
    _assert_envelope_matches(envelope, activation)
    try:
        return await _accept_authenticated(
            database,
            artifacts=artifacts,
            activation=activation,
            resolved_credential_id=resolved.telemetry_credential_id,
            envelope=envelope,
            body=body,
            content_encoding=content_encoding,
            rate_budget=rate_budget,
        )
    except IdentityReuseError as error:
        # The rejected batch has rolled back by now, so the diagnostic is written
        # in its own transaction and both versions survive as non-authoritative.
        async with database.transaction() as conn:
            await record_server_identity_conflict(
                conn, activation=activation, error=error
            )
        await _report(database, activation, error, readiness="fail_stream")
        raise
    except IngestionError as error:
        await _report(database, activation, error, readiness="withdraw_readiness")
        raise


async def _report(
    database: Database,
    activation: ServerActivation,
    error: IngestionError,
    *,
    readiness: str,
) -> None:
    """Record one typed control fact for a refused export."""
    await record_failure(
        database,
        client_id=activation.client_id,
        activation_id=activation.activation_id,
        ingestion_receipt_id=None,
        producer_kind="server",
        source_stream_id=activation.server_stream_id,
        error=error,
        readiness_consequence=readiness,
        affected_watermark=None,
        affected_trace_id=None,
        detail={"code": error.code, "stage": error.stage},
    )


async def _schedule_correlation(
    conn: AsyncConnection[DictRow],
    *,
    activation: ServerActivation,
    spans: Sequence[NormalizedSpan],
) -> None:
    """Schedule one correlation run per trace this batch carried.

    The Job is written in the same transaction as the receipt and the spans, so
    a crash after the commit still leaves the derivation scheduled.
    """
    browser_activation_id = await browser_activation_for_session(
        conn, interview_session_id=activation.interview_session_id
    )
    if browser_activation_id is None:
        return
    await schedule_correlation(
        conn,
        client_id=activation.client_id,
        activation_id=browser_activation_id,
        trace_ids=(span.trace_id for span in spans),
    )


async def _accept_authenticated(
    database: Database,
    *,
    artifacts: ArtifactStore,
    activation: ServerActivation,
    resolved_credential_id: UUID,
    envelope: ExportBatchEnvelope,
    body: bytes,
    content_encoding: str | None,
    rate_budget: RateBudget,
) -> AcceptedExport:
    """Validate, commit, and acknowledge one authenticated export request."""
    decompressed = decompress(body, content_encoding)
    content_sha256 = sha256(decompressed).hexdigest()
    if content_sha256 != envelope.content_sha256:
        raise SourceValidationError(
            field="x-utrace-content-sha256",
            reason="does not match the decompressed OTLP body",
        )
    received_at = datetime.now(tz=UTC)
    rate_budget.consume(
        byte_count=len(decompressed),
        record_count=envelope.span_count,
        now=received_at,
        retry_after_ms=1_000,
    )
    spans = decode_export_request(
        decompressed, declared_span_count=envelope.span_count
    )

    batch_id = UUID(envelope.batch_id)
    async with database.transaction() as conn:
        existing = await find_receipt(
            conn,
            client_id=activation.client_id,
            source_stream_id=activation.server_stream_id,
            batch_id=batch_id,
        )
        if existing is not None:
            if existing.content_sha256 != content_sha256:
                raise IdentityReuseError(
                    identity_kind="batch_id",
                    identity_value=envelope.batch_id,
                    first_content_sha256=existing.content_sha256,
                    conflicting_content_sha256=content_sha256,
                    first_ingestion_receipt_id=existing.ingestion_receipt_id,
                )
            accepted_through = await advance_batch_sequence(
                conn,
                server_stream_id=activation.server_stream_id,
                batch_sequence=envelope.batch_sequence,
            )
            _log.info(
                "server_batch_replayed",
                activation_id=str(activation.activation_id),
                batch_id=envelope.batch_id,
                ingestion_receipt_id=str(existing.ingestion_receipt_id),
            )
            return AcceptedExport(
                ingestion_receipt_id=existing.ingestion_receipt_id,
                activation_id=activation.activation_id,
                interview_session_id=activation.interview_session_id,
                accepted_through_batch_sequence=accepted_through,
                stored_spans=0,
                duplicate_spans=len(spans),
                trace_ids=tuple(sorted({span.trace_id for span in spans})),
                replayed=True,
            )

        for trace_id in sorted({span.trace_id for span in spans}):
            await assert_trace_not_claimed_elsewhere(
                conn, activation=activation, trace_id=trace_id
            )

        artifact = await artifacts.put(
            conn,
            client_id=activation.client_id,
            artifact_class="server_raw_batch",
            media_type="application/x-protobuf",
            schema_type="opentelemetry/trace-export-request.v1",
            retention_class="telemetry_source",
            body=decompressed,
            manifest=None,
        )
        receipt = await write_receipt(
            conn,
            ReceiptDraft(
                client_id=activation.client_id,
                activation_id=activation.activation_id,
                producer_kind="server",
                telemetry_credential_id=resolved_credential_id,
                source_stream_id=activation.server_stream_id,
                batch_id=batch_id,
                batch_sequence=envelope.batch_sequence,
                first_export_sequence=None,
                last_export_sequence=None,
                byte_count=len(decompressed),
                record_count=envelope.span_count,
                content_sha256=content_sha256,
                source_schema_version=envelope.utrace_schema_version,
                canonical_schema_version=CANONICAL_SCHEMA_VERSION,
                policy_version=envelope.policy_version,
                raw_artifact_id=artifact.artifact_id,
            ),
            stage="received",
            outcome="accepted",
        )
        for stage in ("authenticated", "source_validated"):
            await advance_stage(
                conn,
                client_id=activation.client_id,
                ingestion_receipt_id=receipt.ingestion_receipt_id,
                stage=stage,
                normalization_run_id=None,
            )
        normalization_run_id = uuid7()
        stored = 0
        for span in spans:
            if await store_span(
                conn,
                activation=activation,
                ingestion_receipt_id=receipt.ingestion_receipt_id,
                normalization_run_id=normalization_run_id,
                received_at=received_at,
                span=span,
            ):
                stored += 1
        for stage in ("normalized", "indexed"):
            await advance_stage(
                conn,
                client_id=activation.client_id,
                ingestion_receipt_id=receipt.ingestion_receipt_id,
                stage=stage,
                normalization_run_id=normalization_run_id,
            )
        accepted_through = await advance_batch_sequence(
            conn,
            server_stream_id=activation.server_stream_id,
            batch_sequence=envelope.batch_sequence,
        )
        await _schedule_correlation(conn, activation=activation, spans=spans)

    _log.info(
        "server_batch_committed",
        client_id=str(activation.client_id),
        activation_id=str(activation.activation_id),
        server_stream_id=str(activation.server_stream_id),
        batch_id=envelope.batch_id,
        batch_sequence=envelope.batch_sequence,
        stored=stored,
        duplicates=len(spans) - stored,
    )
    return AcceptedExport(
        ingestion_receipt_id=receipt.ingestion_receipt_id,
        activation_id=activation.activation_id,
        interview_session_id=activation.interview_session_id,
        accepted_through_batch_sequence=accepted_through,
        stored_spans=stored,
        duplicate_spans=len(spans) - stored,
        trace_ids=tuple(sorted({span.trace_id for span in spans})),
        replayed=False,
    )


def _assert_envelope_matches(
    envelope: ExportBatchEnvelope, activation: ServerActivation
) -> None:
    """Refuse a batch whose envelope claims identities the activation does not hold."""
    claimed: tuple[tuple[str, str, str], ...] = (
        ("activation_id", envelope.activation_id, str(activation.activation_id)),
        (
            "server_stream_id",
            envelope.server_stream_id,
            str(activation.server_stream_id),
        ),
        (
            "service_instance_id",
            envelope.service_instance_id,
            str(activation.service_instance_id),
        ),
        (
            "policy_version",
            envelope.policy_version,
            activation.policy_version,
        ),
        (
            "utrace_schema_version",
            envelope.utrace_schema_version,
            activation.source_schema_version,
        ),
        (
            "semantic_convention_version",
            envelope.semantic_convention_version,
            activation.semantic_convention_version,
        ),
    )
    for field, presented, authenticated in claimed:
        if presented != authenticated:
            raise IntegrityViolationError(
                identity_kind=field,
                identity_value=presented,
                reason="does not match the authenticated observation activation",
            )
    if envelope.manifest_version != activation.manifest_version:
        raise IntegrityViolationError(
            identity_kind="manifest_version",
            identity_value=str(envelope.manifest_version),
            reason="is not the manifest version this activation pinned",
        )
