"""Durable writes for the server edge.

A server span deduplicates by authenticated activation plus trace ID and span ID;
a batch deduplicates by source stream and batch ID. An exact duplicate with the
same content hash is acknowledged without creating new evidence.

A trace ID is scoped by the authenticated activation. The same trace ID appearing
under another client or activation is an integrity violation, not a cross-session
correlation opportunity.

Contract: docs/arch-components/telemetry-and-evidence.md#correlation-model
"""

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from app.db.rows import as_int, as_str, as_uuid
from app.envelope import JsonValue
from app.ids import uuid7
from app.ingestion.errors import IdentityReuseError, IntegrityViolationError
from app.ingestion.normalize import content_digest
from app.ingestion.otlp import NormalizedSpan
from app.ingestion.server_activation import ServerActivation
from app.logging import get_logger

__all__ = [
    "advance_batch_sequence",
    "assert_trace_not_claimed_elsewhere",
    "record_server_identity_conflict",
    "span_digest",
    "store_span",
]

_log = get_logger(__name__)


def span_digest(span: NormalizedSpan) -> str:
    """Return the canonical content digest of one normalized span.

    OpenTelemetry nanosecond times exceed the integer domain RFC 8785 allows, so
    they are hashed as their exact decimal strings. That keeps the canonical form
    lossless: a one-nanosecond difference still produces a different digest.
    """
    return content_digest(
        {
            "trace_id": span.trace_id,
            "span_id": span.span_id,
            "parent_span_id": span.parent_span_id,
            "span_name": span.span_name,
            "span_kind": span.span_kind,
            "start_time_unix_nano": str(span.start_time_unix_nano),
            "end_time_unix_nano": str(span.end_time_unix_nano),
            "status_code": span.status_code,
            "scope_name": span.scope_name,
            "attributes": dict(span.attributes),
            "links": [dict(link) for link in span.links],
            "events": [_event_digest_form(event) for event in span.span_events],
        }
    )


def _event_digest_form(event: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    """Render one span event for hashing, with its nanosecond time as a string."""
    rendered = dict(event)
    time_value = rendered.get("time_unix_nano")
    if isinstance(time_value, int):
        rendered["time_unix_nano"] = str(time_value)
    return rendered


async def assert_trace_not_claimed_elsewhere(
    conn: AsyncConnection[DictRow], *, activation: ServerActivation, trace_id: str
) -> None:
    """Refuse a trace ID that already belongs to another client or activation.

    The check is the correlation model's first rule made enforceable: records from
    different clients, customer accounts, users, sessions, preview environments,
    runtime allocations, or Preview Versions cannot join merely because another
    field matches.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select client_id, activation_id
            from public.server_spans
            where trace_id = %s
              and (client_id <> %s or activation_id <> %s)
            limit 1
            """,
            (trace_id, activation.client_id, activation.activation_id),
        )
        foreign = await cur.fetchone()
        if foreign is None:
            await cur.execute(
                """
                select client_id, activation_id
                from public.browser_observations
                where trace_id = %s
                  and client_id <> %s
                limit 1
                """,
                (trace_id, activation.client_id),
            )
            foreign = await cur.fetchone()
    if foreign is None:
        return
    raise IntegrityViolationError(
        identity_kind="trace_id",
        identity_value=trace_id,
        reason=(
            "already belongs to another authenticated observation activation, which "
            "is an integrity violation rather than a cross-session correlation"
        ),
    )


async def store_span(
    conn: AsyncConnection[DictRow],
    *,
    activation: ServerActivation,
    ingestion_receipt_id: UUID,
    normalization_run_id: UUID,
    received_at: datetime,
    span: NormalizedSpan,
) -> bool:
    """Persist one canonical span. Returns whether it is new evidence."""
    digest = span_digest(span)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.server_spans (
                id, client_id, activation_id, server_stream_id, ingestion_receipt_id,
                trace_id, span_id, parent_span_id, span_name, span_kind,
                start_time_unix_nano, end_time_unix_nano, status_code, scope_name,
                scope_version, service_name, service_namespace, service_version,
                deployment_environment_name, attributes, links, span_events,
                registered_operation, http_route, http_status_code,
                source_schema_version, canonical_schema_version,
                semantic_convention_version, policy_version, normalization_run_id,
                content_sha256, received_at
            )
            values (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            on conflict (client_id, activation_id, trace_id, span_id) do nothing
            returning id
            """,
            (
                uuid7(),
                activation.client_id,
                activation.activation_id,
                activation.server_stream_id,
                ingestion_receipt_id,
                span.trace_id,
                span.span_id,
                span.parent_span_id,
                span.span_name,
                span.span_kind,
                span.start_time_unix_nano,
                span.end_time_unix_nano,
                span.status_code,
                span.scope_name,
                span.scope_version,
                span.service_name,
                span.service_namespace,
                span.service_version,
                span.deployment_environment_name,
                Jsonb(dict(span.attributes)),
                Jsonb([dict(link) for link in span.links]),
                Jsonb([dict(event) for event in span.span_events]),
                span.registered_operation,
                span.http_route,
                span.http_status_code,
                activation.source_schema_version,
                "1",
                activation.semantic_convention_version,
                activation.policy_version,
                normalization_run_id,
                digest,
                received_at,
            ),
        )
        inserted = await cur.fetchone()
    if inserted is not None:
        return True
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select content_sha256, ingestion_receipt_id
            from public.server_spans
            where client_id = %s and activation_id = %s and trace_id = %s and span_id = %s
            """,
            (
                activation.client_id,
                activation.activation_id,
                span.trace_id,
                span.span_id,
            ),
        )
        existing = await cur.fetchone()
    if existing is None:
        raise IntegrityViolationError(
            identity_kind="span_id",
            identity_value=span.span_id,
            reason="conflicted on insert but cannot be read back",
        )
    if as_str(existing, "content_sha256") == digest:
        return False
    raise IdentityReuseError(
        identity_kind="span_id",
        identity_value=f"{span.trace_id}:{span.span_id}",
        first_content_sha256=as_str(existing, "content_sha256"),
        conflicting_content_sha256=digest,
        first_ingestion_receipt_id=as_uuid(existing, "ingestion_receipt_id"),
    )


async def record_server_identity_conflict(
    conn: AsyncConnection[DictRow],
    *,
    activation: ServerActivation,
    error: IdentityReuseError,
) -> None:
    """Keep both versions of a reused span identity as diagnostics and fail the stream."""
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
            values (%s, %s, %s, 'server', %s, %s, %s, %s, %s, %s)
            """,
            (
                uuid7(),
                activation.client_id,
                activation.activation_id,
                activation.server_stream_id,
                error.identity_kind,
                error.identity_value,
                error.first_content_sha256,
                error.conflicting_content_sha256,
                error.first_ingestion_receipt_id,
            ),
        )
        await cur.execute(
            """
            update public.server_streams
            set state = 'failed', clean = false, revision = revision + 1,
                updated_at = now()
            where id = %s
            """,
            (activation.server_stream_id,),
        )


async def advance_batch_sequence(
    conn: AsyncConnection[DictRow], *, server_stream_id: UUID, batch_sequence: int
) -> int:
    """Advance the watermark to the highest contiguous accepted batch sequence.

    The server SDK's batch sequence is gapless, but its exports run concurrently,
    so a later batch can commit before an earlier one. The walk runs against the
    stream's durable accepted receipts rather than against this batch alone: a
    later batch does not carry the watermark across a hole, and the batch that
    fills the hole carries it past every batch already received behind it. The
    stream row is locked before the walk, so the walk's statement sees every
    batch committed ahead of this one.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "select id from public.server_streams where id = %s for update",
            (server_stream_id,),
        )
        if await cur.fetchone() is None:
            raise IntegrityViolationError(
                identity_kind="server_stream_id",
                identity_value=str(server_stream_id),
                reason="no longer exists",
            )
        await cur.execute(
            """
            with current as (
                select accepted_through_batch_sequence as base
                from public.server_streams where id = %(stream)s
            ),
            received as (
                select distinct receipts.batch_sequence
                from public.ingestion_receipts as receipts, current
                where receipts.source_stream_id = %(stream)s
                  and receipts.producer_kind = 'server'
                  and receipts.outcome = 'accepted'
                  and receipts.batch_sequence > current.base
            ),
            ordered as (
                select batch_sequence,
                       row_number() over (order by batch_sequence) as position
                from received
            )
            update public.server_streams
            set accepted_through_batch_sequence = coalesce(
                    (
                      select max(ordered.batch_sequence)
                      from ordered, current
                      where ordered.batch_sequence = current.base + ordered.position
                    ),
                    public.server_streams.accepted_through_batch_sequence
                ),
                highest_received_batch_sequence = greatest(
                    public.server_streams.highest_received_batch_sequence, %(sequence)s
                ),
                revision = public.server_streams.revision + 1,
                updated_at = now()
            where id = %(stream)s
            returning accepted_through_batch_sequence
            """,
            {"sequence": batch_sequence, "stream": server_stream_id},
        )
        row = await cur.fetchone()
    if row is None:
        raise IntegrityViolationError(
            identity_kind="server_stream_id",
            identity_value=str(server_stream_id),
            reason="no longer exists",
        )
    return as_int(row, "accepted_through_batch_sequence")
