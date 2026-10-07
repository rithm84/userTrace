"""The versioned, typed, read-only telemetry query surface.

Every query names the authenticated client, its purpose, an exact session or
frozen evidence window, the source classes it may read, bounded time or
watermarks, a field projection, a result limit, and a cursor. There is no
arbitrary SQL, OTTL, object-store path, full-text product search, raw DOM
selector, or cross-session telemetry API, and a query can neither mutate state
nor broaden its own authorization.

Results carry canonical IDs, safe typed summaries, freshness, completeness, and
provenance. They never carry a storage credential or a permanent object URL.

Contract: docs/arch-components/telemetry-and-evidence.md#query-and-subscription-contracts
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Literal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict, Field

from app.db.pool import Database
from app.db.rows import as_int, as_str
from app.envelope import JsonValue
from app.evidence.windows import EvidenceWindow, current_watermarks, load_window
from app.ingestion.errors import QueryAuthorizationError
from app.logging import get_logger

__all__ = [
    "QueryScope",
    "TelemetryQueryResult",
    "before_and_after_comparison",
    "browser_request_and_server_chain",
    "delivery_gap_report",
    "product_state_projection",
    "registered_operation_errors",
    "session_activity",
    "session_timeline",
    "target_history",
    "trace_with_completeness",
]

_log = get_logger(__name__)

#: The purposes a caller may name. A purpose is part of the authorization: a
#: live-interview read cannot quietly become an evaluation replay.
QueryPurpose = Literal[
    "live_interview",
    "gate_decision",
    "implementation_input",
    "validation_outcome",
    "case_file_core",
    "evaluation_run",
]

SourceClass = Literal["browser_observation", "server_span"]

MAX_RESULT_LIMIT = 500


class QueryScope(BaseModel):
    """The authorization and bounds every telemetry query must carry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    client_id: UUID
    purpose: QueryPurpose
    interview_session_id: UUID | None = None
    evidence_window_id: UUID | None = None
    allowed_source_classes: tuple[SourceClass, ...] = Field(min_length=1)
    not_before: datetime | None = None
    not_after: datetime | None = None
    projection: tuple[str, ...] = Field(min_length=1)
    limit: int = Field(ge=1, le=MAX_RESULT_LIMIT)
    cursor: UUID | None = None


class TelemetryQueryResult(BaseModel):
    """One typed answer: rows plus the freshness and provenance they were read at."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    shape: str
    rows: tuple[Mapping[str, JsonValue], ...]
    next_cursor: UUID | None
    accepted_through: datetime | None
    browser_accepted_through: int
    server_accepted_through: int
    correlation_revision: int
    completeness: Mapping[str, JsonValue]
    source_classes: tuple[str, ...]


async def _authorize(database: Database, scope: QueryScope) -> EvidenceWindow | None:
    """Resolve the scope's authority, or refuse before any row is read.

    A frozen evidence window is the authority when one is named; otherwise the
    query must name the exact session. A query that names neither, or names a
    window from another client, is refused before lookup rather than filtered
    afterwards.
    """
    if scope.evidence_window_id is None and scope.interview_session_id is None:
        raise QueryAuthorizationError(
            subject="telemetry query",
            reason="must name an exact Interview Session or a frozen evidence window",
        )
    if scope.evidence_window_id is None:
        return None
    window = await load_window(database, evidence_window_id=scope.evidence_window_id)
    if window.client_id != scope.client_id:
        raise QueryAuthorizationError(
            subject=f"evidence window {scope.evidence_window_id}",
            reason="belongs to another client",
        )
    if (
        scope.interview_session_id is not None
        and window.interview_session_id != scope.interview_session_id
    ):
        raise QueryAuthorizationError(
            subject=f"evidence window {scope.evidence_window_id}",
            reason="is not bound to the Interview Session the query names",
        )
    missing = set(scope.allowed_source_classes) - set(window.allowed_source_classes)
    if len(missing) > 0:
        raise QueryAuthorizationError(
            subject=f"evidence window {scope.evidence_window_id}",
            reason=f"does not admit the source classes {sorted(missing)}",
        )
    return window


def _require_source_class(scope: QueryScope, source_class: SourceClass) -> None:
    """Refuse a query shape whose source class the caller did not allow."""
    if source_class not in scope.allowed_source_classes:
        raise QueryAuthorizationError(
            subject=f"source class {source_class}",
            reason="is not among the classes this query is authorized to read",
        )


def _projection(scope: QueryScope, row: Mapping[str, object]) -> Mapping[str, JsonValue]:
    """Apply the caller's field projection, refusing a field it may not read."""
    projected: dict[str, JsonValue] = {}
    for field in scope.projection:
        if field not in row:
            raise QueryAuthorizationError(
                subject=f"field {field}",
                reason="is not part of this query shape's result",
            )
        projected[field] = _render(row[field])
    return projected


def _render(value: object) -> JsonValue:
    """Render one column safely. Storage paths and credentials never appear."""
    match value:
        case None | bool() | int() | float() | str():
            return value
        case datetime():
            return value.isoformat()
        case UUID():
            return str(value)
        case dict():
            return {str(key): _render(item) for key, item in value.items()}
        case list():
            return [_render(item) for item in value]
        case _:
            return str(value)


def _session_of(scope: QueryScope, window: EvidenceWindow | None) -> UUID:
    """Return the exact session the query reads."""
    if window is not None:
        return window.interview_session_id
    if scope.interview_session_id is None:
        raise QueryAuthorizationError(
            subject="telemetry query", reason="names no Interview Session"
        )
    return scope.interview_session_id


def _bounds(
    scope: QueryScope, window: EvidenceWindow | None
) -> tuple[datetime | None, datetime | None]:
    """Return the effective time bounds, tightened by a frozen window's cutoff."""
    upper = scope.not_after
    if window is not None and window.is_frozen():
        cutoff = window.watermarks.accepted_through
        upper = cutoff if upper is None else min(upper, cutoff)
    return scope.not_before, upper


async def _result(
    conn: AsyncConnection[DictRow],
    *,
    shape: str,
    scope: QueryScope,
    window: EvidenceWindow | None,
    rows: Sequence[Mapping[str, object]],
    completeness: Mapping[str, JsonValue],
) -> TelemetryQueryResult:
    """Wrap rows with the freshness and provenance they were read at."""
    projected = tuple(_projection(scope, row) for row in rows)
    next_cursor = (
        UUID(str(rows[-1]["id"])) if len(rows) == scope.limit and "id" in rows[-1] else None
    )
    if window is not None:
        watermarks = window.watermarks
        return TelemetryQueryResult(
            shape=shape,
            rows=projected,
            next_cursor=next_cursor,
            accepted_through=watermarks.accepted_through,
            browser_accepted_through=watermarks.browser_accepted_through,
            server_accepted_through=watermarks.server_accepted_through,
            correlation_revision=watermarks.correlation_revision,
            completeness=completeness,
            source_classes=tuple(scope.allowed_source_classes),
        )
    live = await current_watermarks(
        conn, interview_session_id=_session_of(scope, window)
    )
    return TelemetryQueryResult(
        shape=shape,
        rows=projected,
        next_cursor=next_cursor,
        accepted_through=live.accepted_through,
        browser_accepted_through=live.browser_accepted_through,
        server_accepted_through=live.server_accepted_through,
        correlation_revision=live.correlation_revision,
        completeness=completeness,
        source_classes=tuple(scope.allowed_source_classes),
    )


async def product_state_projection(
    database: Database, scope: QueryScope
) -> TelemetryQueryResult:
    """Return the current product-state projection for one session.

    The projection is the latest route, the mounted target revisions it carries,
    and the most recent baseline, which is what an interview needs to speak about
    what the user is looking at.
    """
    _require_source_class(scope, "browser_observation")
    window = await _authorize(database, scope)
    session_id = _session_of(scope, window)
    async with database.connection() as conn:
        lower, upper = _bounds(scope, window)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select distinct on (observations.observation_type)
                       observations.id, observations.observation_type,
                       observations.route_revision, observations.visibility_state,
                       observations.target_id, observations.target_revision,
                       observations.occurred_at, observations.payload
                from public.browser_observations as observations
                join public.telemetry_activations as activations
                  on activations.id = observations.activation_id
                where observations.client_id = %(client)s
                  and activations.interview_session_id = %(session)s
                  and observations.observation_type in (
                      'route.change', 'baseline.snapshot', 'target.registry_change'
                  )
                  and (%(lower)s::timestamptz is null
                       or observations.occurred_at >= %(lower)s)
                  and (%(upper)s::timestamptz is null
                       or observations.occurred_at <= %(upper)s)
                order by observations.observation_type, observations.occurred_at desc
                limit %(limit)s
                """,
                {
                    "client": scope.client_id,
                    "session": session_id,
                    "lower": lower,
                    "upper": upper,
                    "limit": scope.limit,
                },
            )
            rows = await cur.fetchall()
        return await _result(
            conn,
            shape="product_state_projection",
            scope=scope,
            window=window,
            rows=rows,
            completeness={"kinds_present": [as_str(row, "observation_type") for row in rows]},
        )


async def session_timeline(database: Database, scope: QueryScope) -> TelemetryQueryResult:
    """Return one bounded session timeline in the contract's ordering authority."""
    _require_source_class(scope, "browser_observation")
    window = await _authorize(database, scope)
    session_id = _session_of(scope, window)
    async with database.connection() as conn:
        lower, upper = _bounds(scope, window)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select observations.id, observations.observation_type,
                       observations.source_sequence, observations.export_sequence,
                       observations.occurred_at, observations.route_revision,
                       observations.target_id, observations.trace_id,
                       observations.browser_request_id, observations.payload
                from public.browser_observations as observations
                join public.telemetry_activations as activations
                  on activations.id = observations.activation_id
                where observations.client_id = %(client)s
                  and activations.interview_session_id = %(session)s
                  and (%(lower)s::timestamptz is null
                       or observations.occurred_at >= %(lower)s)
                  and (%(upper)s::timestamptz is null
                       or observations.occurred_at <= %(upper)s)
                  and (%(cursor)s::uuid is null or observations.id > %(cursor)s)
                order by observations.export_sequence, observations.id
                limit %(limit)s
                """,
                {
                    "client": scope.client_id,
                    "session": session_id,
                    "lower": lower,
                    "upper": upper,
                    "cursor": scope.cursor,
                    "limit": scope.limit,
                },
            )
            rows = await cur.fetchall()
        return await _result(
            conn,
            shape="session_timeline",
            scope=scope,
            window=window,
            rows=rows,
            completeness={"record_count": len(rows)},
        )


#: What a person did, as opposed to what the runtime recorded about itself: the
#: product's declared milestones, activations of controls, and each page load.
_PERSON_ACTIVITY: tuple[str, ...] = (
    "product.milestone",
    "interaction.activation",
    "baseline.snapshot",
)


async def session_activity(database: Database, scope: QueryScope) -> TelemetryQueryResult:
    """Return what the person most recently did in the session, newest first.

    The timeline replays every record in ordering authority, where a bounded read
    keeps the oldest records and registry and request bookkeeping crowds out the
    person. This answers what they just did instead, so its bound keeps the latest
    activity. Each row also names its document, so a control can be described from
    that document's target registry.
    """
    _require_source_class(scope, "browser_observation")
    if scope.cursor is not None:
        raise QueryAuthorizationError(
            subject="cursor", reason="does not apply to a newest-first activity read"
        )
    window = await _authorize(database, scope)
    session_id = _session_of(scope, window)
    async with database.connection() as conn:
        lower, upper = _bounds(scope, window)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select observations.id, observations.observation_type,
                       observations.occurred_at, observations.target_id,
                       observations.document_attachment_id, observations.payload
                from public.browser_observations as observations
                join public.telemetry_activations as activations
                  on activations.id = observations.activation_id
                where observations.client_id = %(client)s
                  and activations.interview_session_id = %(session)s
                  and observations.observation_type = any(%(types)s)
                  and (%(lower)s::timestamptz is null
                       or observations.occurred_at >= %(lower)s)
                  and (%(upper)s::timestamptz is null
                       or observations.occurred_at <= %(upper)s)
                order by observations.occurred_at desc, observations.id desc
                limit %(limit)s
                """,
                {
                    "client": scope.client_id,
                    "session": session_id,
                    "types": list(_PERSON_ACTIVITY),
                    "lower": lower,
                    "upper": upper,
                    "limit": scope.limit,
                },
            )
            rows = await cur.fetchall()
        return await _result(
            conn,
            shape="session_activity",
            scope=scope,
            window=window,
            rows=rows,
            completeness={"record_count": len(rows)},
        )


async def trace_with_completeness(
    database: Database, scope: QueryScope, *, trace_id: str
) -> TelemetryQueryResult:
    """Return one trace with its correlation status, gaps, and conflicts."""
    window = await _authorize(database, scope)
    session_id = _session_of(scope, window)
    async with database.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select id, trace_id, status, required_components, observed_components,
                       gaps, conflicts, revision, first_watermark_at, last_watermark_at,
                       browser_accepted_through, server_accepted_through
                from public.correlation_projections
                where client_id = %s and interview_session_id = %s and trace_id = %s
                """,
                (scope.client_id, session_id, trace_id),
            )
            rows = await cur.fetchall()
        completeness: Mapping[str, JsonValue] = (
            {
                "status": as_str(rows[0], "status"),
                "revision": as_int(rows[0], "revision"),
            }
            if len(rows) > 0
            else {"status": "pending", "revision": 0}
        )
        return await _result(
            conn,
            shape="trace_with_completeness",
            scope=scope,
            window=window,
            rows=rows,
            completeness=completeness,
        )


async def browser_request_and_server_chain(
    database: Database, scope: QueryScope, *, browser_request_id: str
) -> TelemetryQueryResult:
    """Return one browser request with the server chain beneath it.

    The chain is assembled from exact parentage and links, never from arrival
    order or temporal proximity.
    """
    window = await _authorize(database, scope)
    session_id = _session_of(scope, window)
    async with database.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                with request as (
                    select observations.trace_id
                    from public.browser_observations as observations
                    join public.telemetry_activations as activations
                      on activations.id = observations.activation_id
                    where observations.client_id = %(client)s
                      and activations.interview_session_id = %(session)s
                      and observations.browser_request_id = %(request)s
                      and observations.trace_id is not null
                    limit 1
                )
                select spans.id, spans.trace_id, spans.span_id, spans.parent_span_id,
                       spans.span_name, spans.span_kind, spans.status_code,
                       spans.http_route, spans.http_status_code,
                       spans.registered_operation, spans.start_time_unix_nano,
                       spans.end_time_unix_nano, spans.service_name
                from public.server_spans as spans
                join public.telemetry_activations as activations
                  on activations.id = spans.activation_id
                join request on request.trace_id = spans.trace_id
                where spans.client_id = %(client)s
                  and activations.interview_session_id = %(session)s
                order by spans.start_time_unix_nano, spans.span_id
                limit %(limit)s
                """,
                {
                    "client": scope.client_id,
                    "session": session_id,
                    "request": browser_request_id,
                    "limit": scope.limit,
                },
            )
            rows = await cur.fetchall()
        return await _result(
            conn,
            shape="browser_request_and_server_chain",
            scope=scope,
            window=window,
            rows=rows,
            completeness={"span_count": len(rows)},
        )


async def target_history(
    database: Database, scope: QueryScope, *, target_id: str
) -> TelemetryQueryResult:
    """Return the exact history of one registered target."""
    _require_source_class(scope, "browser_observation")
    window = await _authorize(database, scope)
    session_id = _session_of(scope, window)
    async with database.connection() as conn:
        lower, upper = _bounds(scope, window)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select observations.id, observations.observation_type,
                       observations.target_id, observations.target_revision,
                       observations.occurred_at, observations.route_revision,
                       observations.payload
                from public.browser_observations as observations
                join public.telemetry_activations as activations
                  on activations.id = observations.activation_id
                where observations.client_id = %(client)s
                  and activations.interview_session_id = %(session)s
                  and observations.target_id = %(target)s
                  and (%(lower)s::timestamptz is null
                       or observations.occurred_at >= %(lower)s)
                  and (%(upper)s::timestamptz is null
                       or observations.occurred_at <= %(upper)s)
                order by observations.export_sequence
                limit %(limit)s
                """,
                {
                    "client": scope.client_id,
                    "session": session_id,
                    "target": target_id,
                    "lower": lower,
                    "upper": upper,
                    "limit": scope.limit,
                },
            )
            rows = await cur.fetchall()
        return await _result(
            conn,
            shape="target_history",
            scope=scope,
            window=window,
            rows=rows,
            completeness={"revision_count": len(rows)},
        )


async def registered_operation_errors(
    database: Database, scope: QueryScope
) -> TelemetryQueryResult:
    """Return errors on registered operations, from both halves of the path."""
    _require_source_class(scope, "server_span")
    window = await _authorize(database, scope)
    session_id = _session_of(scope, window)
    async with database.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select spans.id, spans.trace_id, spans.span_id, spans.span_name,
                       spans.registered_operation, spans.http_route,
                       spans.http_status_code, spans.status_code, spans.service_name,
                       spans.span_events
                from public.server_spans as spans
                join public.telemetry_activations as activations
                  on activations.id = spans.activation_id
                where spans.client_id = %(client)s
                  and activations.interview_session_id = %(session)s
                  and spans.status_code = 'error'
                  and (%(cursor)s::uuid is null or spans.id > %(cursor)s)
                order by spans.id
                limit %(limit)s
                """,
                {
                    "client": scope.client_id,
                    "session": session_id,
                    "cursor": scope.cursor,
                    "limit": scope.limit,
                },
            )
            rows = await cur.fetchall()
        return await _result(
            conn,
            shape="registered_operation_errors",
            scope=scope,
            window=window,
            rows=rows,
            completeness={"error_count": len(rows)},
        )


async def before_and_after_comparison(
    database: Database,
    scope: QueryScope,
    *,
    before_window_id: UUID,
    after_window_id: UUID,
) -> TelemetryQueryResult:
    """Compare the cited evidence of two frozen windows.

    Both windows must be frozen and belong to the same Product Signal, so the
    comparison describes one candidate against its base rather than two unrelated
    selections.
    """
    before = await load_window(database, evidence_window_id=before_window_id)
    after = await load_window(database, evidence_window_id=after_window_id)
    for window in (before, after):
        if window.client_id != scope.client_id:
            raise QueryAuthorizationError(
                subject=f"evidence window {window.evidence_window_id}",
                reason="belongs to another client",
            )
        if not window.is_frozen():
            raise QueryAuthorizationError(
                subject=f"evidence window {window.evidence_window_id}",
                reason="is rolling; a validation comparison cites frozen windows only",
            )
    if before.product_signal_id != after.product_signal_id:
        raise QueryAuthorizationError(
            subject="validation comparison",
            reason="the two windows belong to different Product Signals",
        )
    async with database.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select items.id, items.evidence_window_id, items.source_class,
                       items.source_record_id, items.claim_ref, items.supports,
                       items.trace_id, items.artifact_id, windows.mode,
                       windows.purpose
                from public.telemetry_evidence_items as items
                join public.telemetry_evidence_windows as windows
                  on windows.id = items.evidence_window_id
                where items.client_id = %s and items.evidence_window_id = any(%s)
                order by items.evidence_window_id, items.claim_ref, items.id
                limit %s
                """,
                (scope.client_id, [before_window_id, after_window_id], scope.limit),
            )
            rows = await cur.fetchall()
        return await _result(
            conn,
            shape="before_and_after_validation_comparison",
            scope=scope,
            window=after,
            rows=rows,
            completeness={
                "before_evidence_window_id": str(before_window_id),
                "after_evidence_window_id": str(after_window_id),
                "before_accepted_through": before.watermarks.accepted_through.isoformat(),
                "after_accepted_through": after.watermarks.accepted_through.isoformat(),
            },
        )


async def delivery_gap_report(
    database: Database, scope: QueryScope
) -> TelemetryQueryResult:
    """Return every explicit delivery gap and unacknowledged tail for one session."""
    window = await _authorize(database, scope)
    session_id = _session_of(scope, window)
    async with database.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select gaps.id, gaps.browser_stream_id, gaps.first_source_sequence,
                       gaps.last_source_sequence, gaps.record_count,
                       gaps.observation_class, gaps.reason, gaps.baseline_follows,
                       gaps.resolved, gaps.recorded_at,
                       streams.accepted_through_export_sequence,
                       streams.highest_received_export_sequence, streams.state
                from public.browser_stream_gaps as gaps
                join public.browser_streams as streams on streams.id = gaps.browser_stream_id
                join public.telemetry_activations as activations
                  on activations.id = streams.activation_id
                where gaps.client_id = %s and activations.interview_session_id = %s
                order by gaps.recorded_at
                limit %s
                """,
                (scope.client_id, session_id, scope.limit),
            )
            gap_rows = await cur.fetchall()
            await cur.execute(
                """
                select coalesce(sum(
                         streams.highest_received_export_sequence
                         - streams.accepted_through_export_sequence
                       ), 0)::bigint as unacknowledged
                from public.browser_streams as streams
                join public.telemetry_activations as activations
                  on activations.id = streams.activation_id
                where activations.interview_session_id = %s
                """,
                (session_id,),
            )
            tail = await cur.fetchone()
        return await _result(
            conn,
            shape="delivery_gap_report",
            scope=scope,
            window=window,
            rows=gap_rows,
            completeness={
                "gap_count": len(gap_rows),
                "unacknowledged_records": as_int(tail, "unacknowledged")
                if tail is not None
                else 0,
            },
        )


