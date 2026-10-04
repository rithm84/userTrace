"""Enqueuing the derivation a committed receipt owes.

Normalization commits inside the ingress transaction; correlation and readiness
do not. They run as durable Jobs, and the Job is written in the same transaction
as the receipt, so a crash between the two cannot leave a durable record with no
derivation scheduled. The pg_cron outbox scan then wakes a worker even if the
in-transaction wake-up was lost.

The enqueue itself is ``utrace.enqueue_ingestion_job``: one SQL function shared
with the pg_cron readiness scan, so the dedupe rule and the outbox row have a
single implementation. Correlation is deduplicated by trace and readiness by
Interview Session: a repeated receipt on the same trace coalesces into the run
that has not happened yet, and a finished run never blocks the next one.

Contracts: docs/arch-components/control-plane.md#jobs-commands-and-scheduling
           docs/arch-components/telemetry-and-evidence.md#failure-reporting-and-recovery
"""

from collections.abc import Iterable
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from app.logging import get_logger

__all__ = [
    "CORRELATE_TRACE",
    "EVALUATE_READINESS",
    "browser_activation_for_session",
    "schedule_correlation",
    "schedule_readiness",
]

_log = get_logger(__name__)

CORRELATE_TRACE = "ingestion.correlate_trace"
EVALUATE_READINESS = "ingestion.evaluate_readiness"


async def browser_activation_for_session(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID
) -> UUID | None:
    """Return the session's browser activation, or ``None`` when it has none.

    A correlation projection is keyed by the browser activation, because that is
    the half the server spans have to join onto. A server export that arrives
    before the browser activates schedules nothing: there is no browser stream
    to correlate against yet, and the browser's own request completion schedules
    the run once there is.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.telemetry_activations
            where kind = 'browser' and interview_session_id = %s and state = 'active'
            order by created_at desc
            limit 1
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return UUID(str(row["id"]))


async def _enqueue(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    job_type: str,
    job_input: dict[str, str],
    concurrency_key: str,
) -> UUID | None:
    """Create one ingestion Job, or return ``None`` when one is already pending."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select utrace.enqueue_ingestion_job(%s, %s, %s, %s) as job_id",
            (client_id, job_type, Jsonb(job_input), concurrency_key),
        )
        row = await cur.fetchone()
    if row is None or row["job_id"] is None:
        return None
    return UUID(str(row["job_id"]))


async def schedule_correlation(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    activation_id: UUID,
    trace_ids: Iterable[str],
) -> tuple[UUID, ...]:
    """Schedule one correlation run per trace this batch touched.

    The projection is keyed by client, activation, and trace, so the Job input
    carries exactly those three. A batch naming the same trace twice enqueues
    once, and a trace already awaiting correlation is not enqueued again.
    """
    scheduled: list[UUID] = []
    for trace_id in sorted(set(trace_ids)):
        job_id = await _enqueue(
            conn,
            client_id=client_id,
            job_type=CORRELATE_TRACE,
            job_input={
                "activation_id": str(activation_id),
                "trace_id": trace_id,
            },
            concurrency_key=f"{CORRELATE_TRACE}:{activation_id}:{trace_id}",
        )
        if job_id is not None:
            scheduled.append(job_id)
    if len(scheduled) > 0:
        _log.info(
            "ingestion_correlation_scheduled",
            client_id=str(client_id),
            activation_id=str(activation_id),
            job_count=len(scheduled),
        )
    return tuple(scheduled)


async def schedule_readiness(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    activation_id: UUID,
    interview_session_id: UUID,
    trigger: str,
) -> UUID | None:
    """Schedule a readiness evaluation for one activation's Interview Session.

    Readiness is evaluated for the whole session, so the concurrency key is the
    session: an activation, a health report, a gap, and a stream change arriving
    together produce one evaluation rather than four identical ones.
    """
    job_id = await _enqueue(
        conn,
        client_id=client_id,
        job_type=EVALUATE_READINESS,
        job_input={"activation_id": str(activation_id), "trigger": trigger},
        concurrency_key=f"{EVALUATE_READINESS}:{interview_session_id}",
    )
    if job_id is not None:
        _log.info(
            "ingestion_readiness_scheduled",
            client_id=str(client_id),
            activation_id=str(activation_id),
            interview_session_id=str(interview_session_id),
            trigger=trigger,
        )
    return job_id
