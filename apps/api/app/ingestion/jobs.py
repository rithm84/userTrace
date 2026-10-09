"""Ingestion Jobs.

Each edge commits a receipt with its normalized records; correlation and readiness
evaluation then run as durable Jobs scheduled in that transaction. Every handler is
incremental and idempotent: rerunning it against unchanged inputs produces the same
projection and the same readiness revision content, and it never resends browser
commands, changes interview state, or promotes evidence by itself.

Readiness evaluation also recombines the open session's combined readiness
record from the projections it just published, because that record is the one
every reader consults. When the record changes, the session projection is
republished for the session tab, and the record's own commit wakes the
extension's control socket.

Contract: docs/arch-components/telemetry-and-evidence.md#failure-reporting-and-recovery
"""

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID

from utrace_contracts.domain.lifecycle import InterviewSessionState

from app.adapters.observation_readiness import IngestionObservationReadiness
from app.db.pool import Database
from app.db.rows import as_int, as_optional_uuid, as_str, as_uuid
from app.envelope import JsonValue
from app.execution.jobs import ClaimedJob
from app.ingestion.correlation import correlate_trace
from app.ingestion.readiness import ReadinessFact, publish_readiness
from app.ingestion.scheduling import CORRELATE_TRACE, EVALUATE_READINESS
from app.interview.projection import publish_projection
from app.interview.readiness import load_readiness, refresh_telemetry_readiness
from app.logging import get_logger

__all__ = [
    "CORRELATE_TRACE",
    "EVALUATE_READINESS",
    "build_ingestion_handlers",
    "correlate_trace_job",
    "evaluate_readiness_job",
    "refresh_session_readiness",
]

_log = get_logger(__name__)



def _required_uuid(claimed: ClaimedJob, field: str) -> UUID:
    """Read one identifier from a frozen Job input, or fail with the field named."""
    value = claimed.input.get(field)
    if value is None:
        raise KeyError(f"job {claimed.job_type} input is missing '{field}'")
    return UUID(str(value))


def _required_str(claimed: ClaimedJob, field: str) -> str:
    """Read one string from a frozen Job input, or fail with the field named."""
    value = claimed.input.get(field)
    if not isinstance(value, str):
        raise KeyError(f"job {claimed.job_type} input is missing '{field}'")
    return value


async def correlate_trace_job(database: Database, claimed: ClaimedJob) -> None:
    """Recompute one trace's correlation projection."""
    activation_id = _required_uuid(claimed, "activation_id")
    trace_id = _required_str(claimed, "trace_id")
    projection = await correlate_trace(
        database,
        client_id=claimed.client_id,
        activation_id=activation_id,
        trace_id=trace_id,
    )
    _log.info(
        "ingestion_trace_correlated",
        client_id=str(claimed.client_id),
        activation_id=str(activation_id),
        trace_id=trace_id,
        status=projection.status,
    )


async def evaluate_readiness_job(database: Database, claimed: ClaimedJob) -> None:
    """Evaluate and publish readiness, then recombine the session's record."""
    activation_id = _required_uuid(claimed, "activation_id")
    await evaluate_readiness(
        database, client_id=claimed.client_id, activation_id=activation_id
    )
    await refresh_session_readiness(
        database, client_id=claimed.client_id, activation_id=activation_id
    )


async def refresh_session_readiness(
    database: Database, *, client_id: UUID, activation_id: UUID
) -> None:
    """Recombine one open session's readiness record from the published projections.

    Only the telemetry half is recombined; see `refresh_telemetry_readiness`. A
    closed session keeps the record its closure left.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select sessions.id, sessions.state
            from public.telemetry_activations as activations
            join public.interview_sessions as sessions
              on sessions.id = activations.interview_session_id
            where activations.id = %s
            """,
            (activation_id,),
        )
        session = await cur.fetchone()
    if session is None:
        raise KeyError(f"observation activation {activation_id} does not exist")
    session_id = as_uuid(session, "id")
    if as_str(session, "state") == InterviewSessionState.CLOSED.value:
        return
    before = await load_readiness(database, interview_session_id=session_id)
    after = await refresh_telemetry_readiness(
        database,
        client_id=client_id,
        interview_session_id=session_id,
        observation=IngestionObservationReadiness(database),
    )
    if after.readiness_revision == before.readiness_revision:
        return
    await publish_projection(database, interview_session_id=session_id)
    _log.info(
        "session_readiness_refreshed_from_telemetry",
        client_id=str(client_id),
        interview_session_id=str(session_id),
        readiness_revision=after.readiness_revision,
        observation_ready=after.observation,
        blockers=list(after.blockers),
    )


async def evaluate_readiness(
    database: Database, *, client_id: UUID, activation_id: UUID
) -> tuple[str, ...]:
    """Evaluate every readiness kind for one activation's session.

    Browser readiness requires a current baseline, a continuous accepted sequence,
    bounded acknowledgement lag, and no unresolved critical delivery gap, all for
    the current document: a document the participant navigated away from ended its
    stream, and that is not a readiness failure. Server readiness requires every
    manifest-required service stream of the current document's preview, or of the
    session's own preview before the participant opens a document, with bounded
    export lag: once the participant moves to a candidate, the base preview's streams
    no longer describe what they see. Correlation readiness
    requires a browser ``CLIENT`` span, server ``SERVER`` span, and every required
    downstream hop to join under the current document's activation; a later trace
    that needs fewer hops, such as a page load, does not undo that proof, while an
    ambiguous trace does.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select activations.interview_session_id,
                   sessions.preview_runtime_allocation_id as session_allocation_id
            from public.telemetry_activations as activations
            join public.interview_sessions as sessions
              on sessions.id = activations.interview_session_id
            where activations.id = %s
            """,
            (activation_id,),
        )
        activation = await cur.fetchone()
        if activation is None:
            raise KeyError(f"observation activation {activation_id} does not exist")
        session_id = as_uuid(activation, "interview_session_id")
        # Each attested document gets its own browser activation, so the newest
        # one is the document the participant has open.
        await cur.execute(
            """
            select id, preview_runtime_allocation_id from public.telemetry_activations
            where interview_session_id = %s and kind = 'browser'
            order by created_at desc
            limit 1
            """,
            (session_id,),
        )
        current_document = await cur.fetchone()
        document_activation_id = (
            as_uuid(current_document, "id") if current_document is not None else None
        )
        preview_allocation_id = (
            as_uuid(current_document, "preview_runtime_allocation_id")
            if current_document is not None
            else as_optional_uuid(activation, "session_allocation_id")
        )
        await cur.execute(
            """
            select streams.id, streams.state,
                   streams.accepted_through_export_sequence,
                   streams.highest_received_export_sequence,
                   (
                     select count(*) from public.browser_observations as baselines
                     where baselines.browser_stream_id = streams.id
                       and baselines.observation_type = 'baseline.snapshot'
                   ) as baseline_count,
                   (
                     select count(*) from public.browser_stream_gaps as gaps
                     where gaps.browser_stream_id = streams.id
                       and gaps.resolved = false
                       and gaps.observation_class = 'critical'
                   ) as critical_gap_count
            from public.browser_streams as streams
            where streams.activation_id = %s
            """,
            (document_activation_id,),
        )
        browser_streams = await cur.fetchall()
        await cur.execute(
            """
            select streams.id, streams.state, streams.service_key,
                   streams.accepted_through_batch_sequence,
                   streams.highest_received_batch_sequence
            from public.server_streams as streams
            join public.telemetry_activations as activations
              on activations.id = streams.activation_id
            where activations.interview_session_id = %s
              and activations.preview_runtime_allocation_id = %s
            """,
            (session_id, preview_allocation_id),
        )
        server_streams = await cur.fetchall()
        await cur.execute(
            """
            select id, trace_id, status, gaps, revision
            from public.correlation_projections
            where activation_id = %s
            order by status = 'complete' desc, updated_at desc
            limit 1
            """,
            (document_activation_id,),
        )
        correlation = await cur.fetchone()
        await cur.execute(
            """
            select trace_id from public.correlation_projections
            where activation_id = %s and status = 'ambiguous'
            order by updated_at desc
            limit 1
            """,
            (document_activation_id,),
        )
        ambiguous = await cur.fetchone()
        await cur.execute(
            """
            select id, probe_id from public.telemetry_activations
            where kind = 'server' and interview_session_id = %s
              and preview_runtime_allocation_id = %s
            order by created_at limit 1
            """,
            (session_id, preview_allocation_id),
        )
        server_activation = await cur.fetchone()

    browser_failures: list[Mapping[str, JsonValue]] = []
    if len(browser_streams) == 0:
        browser_failures.append({"code": "no_browser_stream", "detail": {}})
    for stream in browser_streams:
        if as_str(stream, "state") != "open":
            browser_failures.append(
                {"code": "stream_not_open", "detail": {"state": as_str(stream, "state")}}
            )
        if as_int(stream, "baseline_count") == 0:
            browser_failures.append({"code": "baseline_missing", "detail": {}})
        lag = as_int(stream, "highest_received_export_sequence") - as_int(
            stream, "accepted_through_export_sequence"
        )
        if lag > 0:
            browser_failures.append(
                {"code": "acknowledgement_gap", "detail": {"outstanding": lag}}
            )
        if as_int(stream, "critical_gap_count") > 0:
            browser_failures.append({"code": "unresolved_critical_gap", "detail": {}})

    server_failures: list[Mapping[str, JsonValue]] = []
    if len(server_streams) == 0:
        server_failures.append({"code": "no_server_stream", "detail": {}})
    for stream in server_streams:
        if as_str(stream, "state") != "open":
            server_failures.append(
                {"code": "stream_not_open", "detail": {"state": as_str(stream, "state")}}
            )
        lag = as_int(stream, "highest_received_batch_sequence") - as_int(
            stream, "accepted_through_batch_sequence"
        )
        if lag > 0:
            server_failures.append(
                {"code": "export_lag", "detail": {"outstanding_batches": lag}}
            )

    correlation_failures: list[Mapping[str, JsonValue]] = []
    if ambiguous is not None:
        correlation_failures.append(
            {
                "code": "correlation_ambiguous",
                "detail": {"trace_id": as_str(ambiguous, "trace_id")},
            }
        )
    if correlation is None:
        correlation_failures.append({"code": "no_correlated_trace", "detail": {}})
    elif as_str(correlation, "status") != "complete":
        correlation_failures.append(
            {
                "code": "correlation_incomplete",
                "detail": {
                    "status": as_str(correlation, "status"),
                    "trace_id": as_str(correlation, "trace_id"),
                },
            }
        )

    published: list[str] = []
    for kind, failures, resources, watermarks, probe in (
        (
            "browser",
            browser_failures,
            [str(as_uuid(stream, "id")) for stream in browser_streams],
            {
                "browser_accepted_through": max(
                    (
                        as_int(stream, "accepted_through_export_sequence")
                        for stream in browser_streams
                    ),
                    default=0,
                )
            },
            None,
        ),
        (
            "server",
            server_failures,
            [str(as_uuid(stream, "id")) for stream in server_streams],
            {
                "server_accepted_through": max(
                    (
                        as_int(stream, "accepted_through_batch_sequence")
                        for stream in server_streams
                    ),
                    default=0,
                )
            },
            (
                as_uuid(server_activation, "probe_id")
                if server_activation is not None
                else None
            ),
        ),
        (
            "correlation",
            correlation_failures,
            [str(as_uuid(correlation, "id"))] if correlation is not None else [],
            {
                "correlation_revision": (
                    as_int(correlation, "revision") if correlation is not None else 0
                )
            },
            (
                as_uuid(server_activation, "probe_id")
                if server_activation is not None
                else None
            ),
        ),
    ):
        await publish_readiness(
            database,
            client_id=client_id,
            activation_id=activation_id,
            interview_session_id=session_id,
            fact=ReadinessFact(
                kind=kind,
                ready=len(failures) == 0,
                contributing_resource_ids=resources,
                accepted_watermarks=dict(watermarks),
                probe_id=probe,
                failures=failures,
            ),
        )
        published.append(kind)
    _log.info(
        "telemetry_readiness_evaluated",
        client_id=str(client_id),
        activation_id=str(activation_id),
        interview_session_id=str(session_id),
        checked_at=datetime.now(tz=UTC).isoformat(),
        kinds=published,
    )
    return tuple(published)


def build_ingestion_handlers() -> Mapping[
    str, Callable[[Database, ClaimedJob], Awaitable[None]]
]:
    """Return the ``ingestion.*`` job handlers for ``build_handlers()``."""
    return {
        CORRELATE_TRACE: correlate_trace_job,
        EVALUATE_READINESS: evaluate_readiness_job,
    }
