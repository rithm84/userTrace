"""Durable writes for the browser edge.

The stream row, the canonical observations, the explicit gaps, and the contiguous
acknowledgement watermark all move inside the same transaction that commits the
batch's receipt, so an acknowledgement can never outrun durability.

Deduplication is by ``observation_id`` inside the client scope. An exact
duplicate with the same content hash is accepted without creating new evidence;
the same identity with different content is an integrity violation that fails the
source stream, and ingestion does not choose the later arrival.

Contract: docs/arch-components/telemetry-and-evidence.md#duplicates-ordering-lateness-and-time
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from app.db.rows import as_int, as_str
from app.ids import uuid7
from app.ingestion.activation import BrowserActivation
from app.ingestion.errors import IdentityReuseError, IntegrityViolationError
from app.ingestion.normalize import CanonicalObservation, ObservationGap
from app.logging import get_logger

__all__ = [
    "BrowserStreamState",
    "advance_accepted_through",
    "end_stream",
    "fail_stream",
    "open_stream",
    "record_identity_conflict",
    "store_gap",
    "store_observation",
]

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BrowserStreamState:
    """The durable position of one browser stream."""

    browser_stream_id: UUID
    state: str
    accepted_through_export_sequence: int
    highest_received_export_sequence: int


async def open_stream(
    conn: AsyncConnection[DictRow],
    *,
    activation: BrowserActivation,
    browser_stream_id: UUID,
    runtime_id: UUID,
    document_attachment_id: UUID,
) -> BrowserStreamState:
    """Open a new stream or resume a paused one, and return its durable position.

    A resume keeps the same ``browser_stream_id`` and its acknowledgement
    watermark. A new document creates a new stream that starts at sequence one.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.browser_streams (
                id, client_id, activation_id, runtime_id, document_attachment_id, state
            )
            values (%s, %s, %s, %s, %s, 'open')
            on conflict (id) do update
              set state = case
                    when public.browser_streams.state = 'paused' then 'open'
                    else public.browser_streams.state
                  end,
                  revision = public.browser_streams.revision + 1,
                  updated_at = now()
            returning state, accepted_through_export_sequence,
                      highest_received_export_sequence, activation_id, runtime_id
            """,
            (
                browser_stream_id,
                activation.client_id,
                activation.activation_id,
                runtime_id,
                document_attachment_id,
            ),
        )
        row = await cur.fetchone()
    if row is None:
        raise IntegrityViolationError(
            identity_kind="browser_stream_id",
            identity_value=str(browser_stream_id),
            reason="could not be opened",
        )
    if UUID(str(row["activation_id"])) != activation.activation_id:
        raise IntegrityViolationError(
            identity_kind="browser_stream_id",
            identity_value=str(browser_stream_id),
            reason="belongs to a different observation activation",
        )
    if UUID(str(row["runtime_id"])) != runtime_id:
        raise IntegrityViolationError(
            identity_kind="runtime_id",
            identity_value=str(runtime_id),
            reason="is not the runtime that created this browser stream",
        )
    state = as_str(row, "state")
    if state in {"ended", "failed"}:
        raise IntegrityViolationError(
            identity_kind="browser_stream_id",
            identity_value=str(browser_stream_id),
            reason=f"is {state} and cannot be resumed",
        )
    return BrowserStreamState(
        browser_stream_id=browser_stream_id,
        state="open",
        accepted_through_export_sequence=as_int(row, "accepted_through_export_sequence"),
        highest_received_export_sequence=as_int(row, "highest_received_export_sequence"),
    )


async def store_observation(
    conn: AsyncConnection[DictRow],
    *,
    activation: BrowserActivation,
    browser_stream_id: UUID,
    ingestion_receipt_id: UUID,
    normalization_run_id: UUID,
    received_at: datetime,
    record: CanonicalObservation,
) -> bool:
    """Persist one canonical observation. Returns whether it is new evidence."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.browser_observations (
                id, client_id, activation_id, browser_stream_id, ingestion_receipt_id,
                observation_id, observation_type, runtime_id, document_attachment_id,
                frame_id, source_sequence, export_sequence, occurred_at,
                monotonic_offset, route_revision, visibility_state, target_id,
                target_revision, trace_id, span_id, browser_request_id, payload,
                policy_version, redaction_version, source_schema_version,
                canonical_schema_version, normalization_run_id, content_sha256,
                received_at
            )
            values (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            on conflict (client_id, observation_id) do nothing
            returning id
            """,
            (
                uuid7(),
                activation.client_id,
                activation.activation_id,
                browser_stream_id,
                ingestion_receipt_id,
                record.observation_id,
                record.observation_type,
                record.runtime_id,
                record.document_attachment_id,
                record.frame_id,
                record.source_sequence,
                record.export_sequence,
                record.occurred_at,
                record.monotonic_offset,
                record.route_revision,
                record.visibility_state,
                record.target_id,
                record.target_revision,
                record.trace_id,
                record.span_id,
                record.browser_request_id,
                Jsonb(dict(record.payload)),
                activation.policy_version,
                activation.redaction_version,
                activation.source_schema_version,
                "1",
                normalization_run_id,
                record.content_sha256,
                received_at,
            ),
        )
        inserted = await cur.fetchone()
    if inserted is not None:
        return True
    return await _confirm_exact_duplicate(
        conn, client_id=activation.client_id, record=record
    )


async def _confirm_exact_duplicate(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    record: CanonicalObservation,
) -> bool:
    """Accept an exact duplicate, or raise when the identity carries new content."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select content_sha256, ingestion_receipt_id
            from public.browser_observations
            where client_id = %s and observation_id = %s
            """,
            (client_id, record.observation_id),
        )
        existing = await cur.fetchone()
    if existing is None:
        raise IntegrityViolationError(
            identity_kind="observation_id",
            identity_value=str(record.observation_id),
            reason="conflicted on insert but cannot be read back",
        )
    if as_str(existing, "content_sha256") == record.content_sha256:
        return False
    raise IdentityReuseError(
        identity_kind="observation_id",
        identity_value=str(record.observation_id),
        first_content_sha256=as_str(existing, "content_sha256"),
        conflicting_content_sha256=record.content_sha256,
        first_ingestion_receipt_id=UUID(str(existing["ingestion_receipt_id"])),
    )


async def record_identity_conflict(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    activation_id: UUID,
    producer_kind: str,
    source_stream_id: UUID,
    error: IdentityReuseError,
) -> None:
    """Keep both versions of a reused identity as non-authoritative diagnostics.

    This runs in its own transaction, after the rejected batch has rolled back,
    so the diagnostic survives while the conflicting record never becomes
    evidence.
    """
    if error.first_ingestion_receipt_id is None:
        raise IntegrityViolationError(
            identity_kind=error.identity_kind,
            identity_value=error.identity_value,
            reason="has no first receipt to attribute the conflict to",
        )
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.telemetry_identity_conflicts (
                id, client_id, activation_id, producer_kind, source_stream_id,
                identity_kind, identity_value, first_content_sha256,
                conflicting_content_sha256, first_ingestion_receipt_id
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                uuid7(),
                client_id,
                activation_id,
                producer_kind,
                source_stream_id,
                error.identity_kind,
                error.identity_value,
                error.first_content_sha256,
                error.conflicting_content_sha256,
                error.first_ingestion_receipt_id,
            ),
        )


async def fail_stream(
    conn: AsyncConnection[DictRow], *, browser_stream_id: UUID, reason: str
) -> None:
    """Fail one browser stream. A failed stream cannot be resumed."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.browser_streams
            set state = 'failed', end_reason = %s, revision = revision + 1,
                updated_at = now()
            where id = %s
            """,
            (reason, browser_stream_id),
        )


async def store_gap(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    browser_stream_id: UUID,
    ingestion_receipt_id: UUID,
    gap: ObservationGap,
) -> None:
    """Record one explicit delivery gap against its stream."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.browser_stream_gaps (
                id, client_id, browser_stream_id, ingestion_receipt_id, gap_runtime_id,
                first_source_sequence, last_source_sequence, record_count,
                observation_class, reason, baseline_follows
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                uuid7(),
                client_id,
                browser_stream_id,
                ingestion_receipt_id,
                gap.gap_runtime_id,
                gap.first_source_sequence,
                gap.last_source_sequence,
                gap.record_count,
                gap.observation_class,
                gap.reason,
                gap.baseline_follows,
            ),
        )


async def advance_accepted_through(
    conn: AsyncConnection[DictRow],
    *,
    browser_stream_id: UUID,
    highest_received: int,
) -> int:
    """Advance the watermark to the highest contiguous accepted ``export_sequence``.

    The walk runs against the durable rows rather than against what this batch
    happened to carry, so a sequence held back by an earlier gap keeps the
    watermark where it is until the hole is filled.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            with current as (
                select accepted_through_export_sequence as base
                from public.browser_streams where id = %(stream)s
            ),
            ordered as (
                select observations.export_sequence,
                       row_number() over (order by observations.export_sequence) as position
                from public.browser_observations as observations, current
                where observations.browser_stream_id = %(stream)s
                  and observations.export_sequence > current.base
            )
            update public.browser_streams
            set accepted_through_export_sequence = coalesce(
                    (
                      select max(ordered.export_sequence)
                      from ordered, current
                      where ordered.export_sequence = current.base + ordered.position
                    ),
                    public.browser_streams.accepted_through_export_sequence
                ),
                highest_received_export_sequence = greatest(
                    public.browser_streams.highest_received_export_sequence, %(highest)s
                ),
                revision = public.browser_streams.revision + 1,
                updated_at = now()
            where id = %(stream)s
            returning accepted_through_export_sequence
            """,
            {"stream": browser_stream_id, "highest": highest_received},
        )
        row = await cur.fetchone()
    if row is None:
        raise IntegrityViolationError(
            identity_kind="browser_stream_id",
            identity_value=str(browser_stream_id),
            reason="no longer exists",
        )
    return as_int(row, "accepted_through_export_sequence")


async def end_stream(
    conn: AsyncConnection[DictRow],
    *,
    browser_stream_id: UUID,
    terminal_export_sequence: int,
    reason: str,
) -> str:
    """Record a terminal sequence. ``paused`` leaves the stream resumable."""
    state = "paused" if reason == "paused" else "ended"
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.browser_streams
            set state = %s, terminal_export_sequence = %s, end_reason = %s,
                revision = revision + 1, updated_at = now()
            where id = %s
            """,
            (state, terminal_export_sequence, reason, browser_stream_id),
        )
    _log.info(
        "browser_stream_ended",
        browser_stream_id=str(browser_stream_id),
        state=state,
        reason=reason,
        terminal_export_sequence=terminal_export_sequence,
    )
    return state
