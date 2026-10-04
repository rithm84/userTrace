"""Correlation of the browser and server halves of one product operation.

Correlation begins with authenticated tenant and activation equality. Within one
activation, browser-to-server causality uses the W3C trace ID and the exact span
relationship: the server ``SERVER`` span must name the browser ``CLIENT`` span as
its remote parent. Downstream services, databases, caches, and queues extend that
trace through parentage or explicit span links.

Arrival time may rank a diagnostic, but it never establishes causality. Missing
parentage stays missing: ingestion never creates a synthetic span or parent to
make a trace look complete.

Ordering authority is, in order: source sequence where defined, explicit span
parentage or link, paired request identity, and then clock-adjusted time.

Contract: docs/arch-components/telemetry-and-evidence.md#correlation-model
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from app.db.pool import Database
from app.db.rows import as_int, as_optional_str, as_str, as_uuid
from app.envelope import JsonValue
from app.ids import uuid7
from app.ingestion.scheduling import schedule_readiness
from app.logging import get_logger

__all__ = [
    "CorrelationInput",
    "CorrelationProjection",
    "ServerSpanRef",
    "correlate_trace",
    "evaluate_correlation",
    "ordering_key",
]

_log = get_logger(__name__)

#: The components a browser-originated trace needs before it is complete.
_REQUIRED_COMPONENTS: tuple[str, ...] = ("browser_client_span", "server_span")


@dataclass(frozen=True, slots=True)
class ServerSpanRef:
    """The parentage facts correlation reads from one server span."""

    span_id: str
    parent_span_id: str | None
    span_kind: str
    link_span_ids: tuple[str, ...]
    start_time_unix_nano: int


@dataclass(frozen=True, slots=True)
class CorrelationInput:
    """Everything one trace's correlation decision reads. No I/O happens here."""

    trace_id: str
    browser_client_span_ids: frozenset[str]
    browser_request_ids: frozenset[str]
    server_spans: tuple[ServerSpanRef, ...]
    required_downstream_hops: frozenset[str]


@dataclass(frozen=True, slots=True)
class CorrelationProjection:
    """The decided projection: status, components, gaps, and conflicts."""

    status: str
    observed_components: tuple[str, ...]
    gaps: tuple[str, ...]
    conflicts: tuple[str, ...]


_HOP_KINDS: Mapping[str, str] = {
    "server": "server",
    "downstream_http": "client",
    "database": "client",
    "cache": "client",
    "queue": "producer",
    "custom_protocol": "internal",
}


def evaluate_correlation(source: CorrelationInput) -> CorrelationProjection:
    """Decide one trace's correlation status from parentage and links alone.

    Seeing browser and server telemetry separately is not correlation: the server
    ``SERVER`` span has to name a browser ``CLIENT`` span of this activation as
    its remote parent.
    """
    observed: list[str] = []
    gaps: list[str] = []
    conflicts: list[str] = []

    if len(source.browser_client_span_ids) > 0:
        observed.append("browser_client_span")
    else:
        gaps.append("browser_client_span")

    server_spans = [span for span in source.server_spans if span.span_kind == "server"]
    if len(server_spans) == 0:
        gaps.append("server_span")
    else:
        observed.append("server_span")

    rooted = [
        span
        for span in server_spans
        if span.parent_span_id is not None
        and span.parent_span_id in source.browser_client_span_ids
    ]
    if len(server_spans) > 0 and len(rooted) == 0:
        gaps.append("browser_to_server_parentage")
    # One interaction may issue several requests under one trace, each from its own
    # browser span; only two server spans naming the same parent are ambiguous.
    parents = [span.parent_span_id for span in rooted]
    if len(parents) != len(set(parents)):
        conflicts.append("multiple_server_spans_claim_the_same_browser_parent")

    known_span_ids = {span.span_id for span in source.server_spans}
    for hop in sorted(source.required_downstream_hops):
        kind = _HOP_KINDS.get(hop)
        if kind is None:
            conflicts.append(f"unknown_required_hop:{hop}")
            continue
        if hop == "server":
            continue
        attached = [
            span
            for span in source.server_spans
            if span.span_kind == kind
            and (
                (span.parent_span_id is not None and span.parent_span_id in known_span_ids)
                or any(link in known_span_ids for link in span.link_span_ids)
            )
        ]
        if len(attached) == 0:
            gaps.append(f"required_hop:{hop}")
        else:
            observed.append(f"required_hop:{hop}")

    orphans = [
        span
        for span in source.server_spans
        if span.parent_span_id is not None
        and span.parent_span_id not in known_span_ids
        and span.parent_span_id not in source.browser_client_span_ids
    ]
    if len(orphans) > 0:
        gaps.append("unresolved_parent_span")

    if len(conflicts) > 0:
        status = "ambiguous"
    elif len(gaps) == 0:
        status = "complete"
    elif "browser_client_span" in gaps and "server_span" in gaps:
        status = "pending"
    else:
        status = "partial"
    return CorrelationProjection(
        status=status,
        observed_components=tuple(observed),
        gaps=tuple(gaps),
        conflicts=tuple(conflicts),
    )


def ordering_key(
    *,
    source_sequence: int | None,
    parent_depth: int | None,
    paired_request_id: str | None,
    clock_adjusted_nanos: int,
) -> tuple[int, int, int, str, int]:
    """Return the ordering key ingestion sorts a mixed timeline by.

    The authority order is source sequence where defined, then explicit span
    parentage, then paired request identity, then clock-adjusted time. Records
    without an earlier authority sort after the ones that have it, so uTrace never
    claims a physical total order across frames, services, and workers.
    """
    return (
        0 if source_sequence is not None else 1,
        source_sequence if source_sequence is not None else 0,
        parent_depth if parent_depth is not None else 0,
        paired_request_id if paired_request_id is not None else "",
        clock_adjusted_nanos,
    )


async def correlate_trace(
    database: Database, *, client_id: UUID, activation_id: UUID, trace_id: str
) -> CorrelationProjection:
    """Recompute one trace's projection incrementally and idempotently.

    The projection is keyed by its exact client, activation, and trace. Rerunning
    it against unchanged inputs produces the same status and does not advance the
    revision beyond what the facts justify.
    """
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select span_id, browser_request_id, browser_stream_id, occurred_at
                from public.browser_observations
                where client_id = %s and activation_id = %s and trace_id = %s
                  and span_id is not null
                """,
                (client_id, activation_id, trace_id),
            )
            browser_rows = await cur.fetchall()
            await cur.execute(
                """
                select activations.id as server_activation_id,
                       spans.span_id, spans.parent_span_id, spans.span_kind,
                       spans.links, spans.start_time_unix_nano
                from public.server_spans as spans
                join public.telemetry_activations as activations
                  on activations.id = spans.activation_id
                where spans.client_id = %s and spans.trace_id = %s
                  and activations.interview_session_id = (
                      select interview_session_id from public.telemetry_activations
                      where id = %s
                  )
                """,
                (client_id, trace_id, activation_id),
            )
            server_rows = await cur.fetchall()
            await cur.execute(
                """
                select interview_session_id, required_hops
                from public.telemetry_activations
                where id = %s
                """,
                (activation_id,),
            )
            browser_activation = await cur.fetchone()
            await cur.execute(
                """
                select id, required_hops
                from public.telemetry_activations
                where kind = 'server' and interview_session_id = (
                    select interview_session_id from public.telemetry_activations
                    where id = %s
                )
                order by created_at
                limit 1
                """,
                (activation_id,),
            )
            server_activation = await cur.fetchone()
        if browser_activation is None:
            raise ValueError(f"activation {activation_id} does not exist")

        hops = server_activation["required_hops"] if server_activation is not None else None
        source = CorrelationInput(
            trace_id=trace_id,
            browser_client_span_ids=frozenset(
                as_str(row, "span_id") for row in browser_rows
            ),
            browser_request_ids=frozenset(
                value
                for row in browser_rows
                if (value := as_optional_str(row, "browser_request_id")) is not None
            ),
            server_spans=tuple(
                ServerSpanRef(
                    span_id=as_str(row, "span_id"),
                    parent_span_id=as_optional_str(row, "parent_span_id"),
                    span_kind=as_str(row, "span_kind"),
                    link_span_ids=tuple(
                        str(link["span_id"])
                        for link in (row["links"] if isinstance(row["links"], list) else [])
                        if isinstance(link, dict) and "span_id" in link
                    ),
                    start_time_unix_nano=as_int(row, "start_time_unix_nano"),
                )
                for row in server_rows
            ),
            required_downstream_hops=frozenset(
                str(hop) for hop in (hops if isinstance(hops, list) else [])
            ),
        )
        projection = evaluate_correlation(source)
        session_id = as_uuid(browser_activation, "interview_session_id")
        await _upsert_projection(
            conn,
            client_id=client_id,
            activation_id=activation_id,
            server_activation_id=(
                as_uuid(server_activation, "id") if server_activation is not None else None
            ),
            interview_session_id=session_id,
            trace_id=trace_id,
            projection=projection,
            browser_rows=browser_rows,
            server_rows=server_rows,
        )
        if projection.status == "complete" and not await _session_correlation_ready(
            conn, client_id=client_id, interview_session_id=session_id
        ):
            # Nothing else re-evaluates readiness when a trace completes, so the
            # session would wait for an unrelated trigger to learn its path works.
            await schedule_readiness(
                conn,
                client_id=client_id,
                activation_id=activation_id,
                interview_session_id=session_id,
                trigger="trace_correlated",
            )
    _log.info(
        "correlation_projection_updated",
        client_id=str(client_id),
        activation_id=str(activation_id),
        trace_id=trace_id,
        status=projection.status,
        gaps=list(projection.gaps),
    )
    return projection


async def _session_correlation_ready(
    conn: AsyncConnection[DictRow], *, client_id: UUID, interview_session_id: UUID
) -> bool:
    """Whether the session's newest correlation readiness is ready and unexpired."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select ready and expires_at > now() as current
            from public.telemetry_readiness_projections
            where client_id = %s and interview_session_id = %s and kind = 'correlation'
            order by checked_at desc, revision desc
            limit 1
            """,
            (client_id, interview_session_id),
        )
        row = await cur.fetchone()
    return row is not None and bool(row["current"])


async def _upsert_projection(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    activation_id: UUID,
    server_activation_id: UUID | None,
    interview_session_id: UUID,
    trace_id: str,
    projection: CorrelationProjection,
    browser_rows: Sequence[Mapping[str, object]],
    server_rows: Sequence[Mapping[str, object]],
) -> None:
    """Write the projection, bumping the revision only when its content changes."""
    watermarks = _watermarks(browser_rows)
    observed: Mapping[str, JsonValue] = {
        "components": list(projection.observed_components),
        "browser_span_count": len(browser_rows),
        "server_span_count": len(server_rows),
    }
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.correlation_projections (
                id, client_id, activation_id, server_activation_id,
                interview_session_id, trace_id, status, required_components,
                observed_components, gaps, conflicts, first_watermark_at,
                last_watermark_at, browser_accepted_through, server_accepted_through
            )
            values (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                coalesce((
                    select max(accepted_through_export_sequence)
                    from public.browser_streams where activation_id = %s
                ), 0),
                coalesce((
                    select max(accepted_through_batch_sequence)
                    from public.server_streams where activation_id = %s
                ), 0)
            )
            on conflict (client_id, activation_id, trace_id) do update
              set status = excluded.status,
                  server_activation_id = excluded.server_activation_id,
                  observed_components = excluded.observed_components,
                  gaps = excluded.gaps,
                  conflicts = excluded.conflicts,
                  first_watermark_at = excluded.first_watermark_at,
                  last_watermark_at = excluded.last_watermark_at,
                  browser_accepted_through = excluded.browser_accepted_through,
                  server_accepted_through = excluded.server_accepted_through,
                  revision = public.correlation_projections.revision + 1,
                  updated_at = now()
              where public.correlation_projections.status <> excluded.status
                 or public.correlation_projections.gaps <> excluded.gaps
                 or public.correlation_projections.conflicts <> excluded.conflicts
                 or public.correlation_projections.observed_components
                    <> excluded.observed_components
            """,
            (
                uuid7(),
                client_id,
                activation_id,
                server_activation_id,
                interview_session_id,
                trace_id,
                projection.status,
                Jsonb({"components": list(_REQUIRED_COMPONENTS)}),
                Jsonb(dict(observed)),
                Jsonb(list(projection.gaps)),
                Jsonb(list(projection.conflicts)),
                watermarks[0],
                watermarks[1],
                activation_id,
                server_activation_id,
            ),
        )


def _watermarks(
    browser_rows: Sequence[Mapping[str, object]],
) -> tuple[datetime | None, datetime | None]:
    """Return the first and last relevant browser watermarks for one trace."""
    times = [
        row["occurred_at"] for row in browser_rows if isinstance(row["occurred_at"], datetime)
    ]
    if len(times) == 0:
        return None, None
    return min(times), max(times)
