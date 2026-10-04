"""The durable ingress journal.

An acknowledgement means the authenticated batch passed the ingress checks and
was committed here. Receipt by a load balancer, a WebSocket process, an OTLP
receiver, or an in-memory queue is not enough, so every edge writes its receipt
inside the same transaction that writes the normalized records and only then
answers the producer.

Contract: docs/arch-components/telemetry-and-evidence.md#durable-receipt-and-acknowledgement
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import AggregateKind

from app.db.pool import Database
from app.db.rows import as_int, as_str
from app.envelope import JsonValue
from app.ids import uuid7
from app.ingestion.errors import IngestionError
from app.logging import get_logger

__all__ = [
    "STAGE_ORDER",
    "ReceiptDraft",
    "ReceiptOutcome",
    "advance_stage",
    "find_receipt",
    "record_failure",
    "write_receipt",
]

_log = get_logger(__name__)

#: `received -> authenticated -> source_validated -> normalized -> indexed ->
#: correlated -> available`. A terminal rejection or quarantine records the exact
#: stage it reached; no later stage is implied by an earlier one.
STAGE_ORDER: tuple[str, ...] = (
    "received",
    "authenticated",
    "source_validated",
    "normalized",
    "indexed",
    "correlated",
    "available",
)


@dataclass(frozen=True, slots=True)
class ReceiptDraft:
    """Everything one received batch records before it is acknowledged."""

    client_id: UUID
    activation_id: UUID
    producer_kind: str
    telemetry_credential_id: UUID
    source_stream_id: UUID
    batch_id: UUID
    batch_sequence: int | None
    first_export_sequence: int | None
    last_export_sequence: int | None
    byte_count: int
    record_count: int
    content_sha256: str
    source_schema_version: str
    canonical_schema_version: str
    policy_version: str
    raw_artifact_id: UUID | None


@dataclass(frozen=True, slots=True)
class ReceiptOutcome:
    """A committed receipt and whether this call is the one that created it."""

    ingestion_receipt_id: UUID
    stage: str
    outcome: str
    content_sha256: str
    created: bool


async def find_receipt(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    source_stream_id: UUID,
    batch_id: UUID,
) -> ReceiptOutcome | None:
    """Return the existing receipt for one batch identity, if there is one."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, stage, outcome, content_sha256
            from public.ingestion_receipts
            where client_id = %s and source_stream_id = %s and batch_id = %s
            """,
            (client_id, source_stream_id, batch_id),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return ReceiptOutcome(
        ingestion_receipt_id=UUID(str(row["id"])),
        stage=as_str(row, "stage"),
        outcome=as_str(row, "outcome"),
        content_sha256=as_str(row, "content_sha256"),
        created=False,
    )


async def write_receipt(
    conn: AsyncConnection[DictRow], draft: ReceiptDraft, *, stage: str, outcome: str
) -> ReceiptOutcome:
    """Commit one immutable receipt and its first stage entry.

    ``ingestion_receipt_id`` is a UUIDv7 minted here. It identifies the receipt;
    it never replaces a source record ID.
    """
    receipt_id = uuid7()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.ingestion_receipts (
                id, client_id, activation_id, producer_kind, telemetry_credential_id,
                source_stream_id, batch_id, batch_sequence, first_export_sequence,
                last_export_sequence, byte_count, record_count, content_sha256,
                source_schema_version, canonical_schema_version, policy_version,
                outcome, stage, raw_artifact_id
            )
            values (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                receipt_id,
                draft.client_id,
                draft.activation_id,
                draft.producer_kind,
                draft.telemetry_credential_id,
                draft.source_stream_id,
                draft.batch_id,
                draft.batch_sequence,
                draft.first_export_sequence,
                draft.last_export_sequence,
                draft.byte_count,
                draft.record_count,
                draft.content_sha256,
                draft.source_schema_version,
                draft.canonical_schema_version,
                draft.policy_version,
                outcome,
                stage,
                draft.raw_artifact_id,
            ),
        )
        await cur.execute(
            """
            insert into public.ingestion_receipt_stages (
                id, client_id, ingestion_receipt_id, stage
            )
            values (%s, %s, %s, %s)
            """,
            (uuid7(), draft.client_id, receipt_id, stage),
        )
    _log.info(
        "ingestion_receipt_committed",
        client_id=str(draft.client_id),
        activation_id=str(draft.activation_id),
        ingestion_receipt_id=str(receipt_id),
        producer_kind=draft.producer_kind,
        source_stream_id=str(draft.source_stream_id),
        batch_id=str(draft.batch_id),
        record_count=draft.record_count,
        byte_count=draft.byte_count,
        stage=stage,
        outcome=outcome,
    )
    return ReceiptOutcome(
        ingestion_receipt_id=receipt_id,
        stage=stage,
        outcome=outcome,
        content_sha256=draft.content_sha256,
        created=True,
    )


async def advance_stage(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    ingestion_receipt_id: UUID,
    stage: str,
    normalization_run_id: UUID | None,
) -> None:
    """Record that one receipt reached a later stage.

    Reaching a stage is written twice: an append-only row that says when it
    happened and which normalization run produced it, and the receipt's own
    ``stage`` column, which only ever moves forward.
    """
    if stage not in STAGE_ORDER:
        raise ValueError(f"'{stage}' is not an ingestion stage")
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.ingestion_receipt_stages (
                id, client_id, ingestion_receipt_id, stage, normalization_run_id
            )
            values (%s, %s, %s, %s, %s)
            on conflict (ingestion_receipt_id, stage) do nothing
            """,
            (uuid7(), client_id, ingestion_receipt_id, stage, normalization_run_id),
        )
        await cur.execute(
            """
            update public.ingestion_receipts
            set stage = %s, revision = revision + 1, updated_at = now()
            where id = %s and client_id = %s
              and array_position(%s::text[], stage::text)
                  < array_position(%s::text[], %s)
            """,
            (
                stage,
                ingestion_receipt_id,
                client_id,
                list(STAGE_ORDER),
                list(STAGE_ORDER),
                stage,
            ),
        )


async def record_failure(
    database: Database,
    *,
    client_id: UUID,
    activation_id: UUID | None,
    ingestion_receipt_id: UUID | None,
    producer_kind: str,
    source_stream_id: UUID | None,
    error: IngestionError,
    readiness_consequence: str,
    affected_watermark: int | None,
    affected_trace_id: str | None,
    detail: Mapping[str, JsonValue],
) -> UUID:
    """Record one typed ingestion failure.

    The record carries safe identities, the stage, the stable code, the retry
    classification, the affected watermark or trace, and the readiness
    consequence. It never carries the rejected product value.
    """
    failure_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.ingestion_failures (
                id, client_id, activation_id, ingestion_receipt_id, producer_kind,
                source_stream_id, stage, code, retry_classification,
                readiness_consequence, affected_watermark, affected_trace_id, detail
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                failure_id,
                client_id,
                activation_id,
                ingestion_receipt_id,
                producer_kind,
                source_stream_id,
                error.stage,
                error.code,
                error.retry_classification,
                readiness_consequence,
                affected_watermark,
                affected_trace_id,
                Jsonb(dict(detail)),
            ),
        )
    _log.warning(
        "ingestion_failure_recorded",
        client_id=str(client_id),
        activation_id=str(activation_id) if activation_id is not None else None,
        producer_kind=producer_kind,
        code=error.code,
        stage=error.stage,
        retry_classification=error.retry_classification,
        readiness_consequence=readiness_consequence,
    )
    return failure_id


def stage_reached(stage: str, at_least: str) -> bool:
    """Return whether one stage is at or past another."""
    return STAGE_ORDER.index(stage) >= STAGE_ORDER.index(at_least)


async def receipt_counts(
    database: Database, *, activation_id: UUID
) -> Mapping[str, int]:
    """Return accepted, rejected, and quarantined receipt counts for one activation."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select outcome, count(*) as total
            from public.ingestion_receipts
            where activation_id = %s
            group by outcome
            """,
            (activation_id,),
        )
        rows = await cur.fetchall()
    return {as_str(row, "outcome"): as_int(row, "total") for row in rows}


def stream_kind_for(producer_kind: str) -> AggregateKind:
    """Return the control-plane stream a producer's derived facts belong to."""
    if producer_kind not in {"browser", "server"}:
        raise ValueError(f"'{producer_kind}' is not a telemetry producer kind")
    return AggregateKind.INTERVIEW_SESSION


def watermark_payload(
    *, browser_accepted_through: int, server_accepted_through: int, accepted_at: datetime
) -> Mapping[str, JsonValue]:
    """Build the watermark block every frozen evidence use records."""
    return {
        "browser_accepted_through": browser_accepted_through,
        "server_accepted_through": server_accepted_through,
        "accepted_through": accepted_at.isoformat(),
    }
