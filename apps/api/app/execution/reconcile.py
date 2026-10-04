"""Restart reconciliation.

No in-memory state is authoritative. Before a worker or API process becomes
ready, it reconciles expired leases, stale runs, overdue Jobs, stranded outbox
rows, and dispatched Commands whose outcome is unknown. Remediation is limited to
idempotent, evidence-preserving transitions.
"""

from dataclasses import dataclass

from utrace_contracts.domain.lifecycle import CommandState

from app.db.pool import Database
from app.db.rows import as_int
from app.logging import get_logger

__all__ = ["ReconciliationReport", "reconcile_startup"]

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """What one startup reconciliation pass repaired."""

    reclaimed_leases: int
    expired_jobs: int
    requeued_outbox: int
    commands_in_reconciliation: int


async def reconcile_startup(
    database: Database, *, stale_outbox_seconds: int, stale_dispatch_seconds: int
) -> ReconciliationReport:
    """Reconcile durable execution state and return what changed."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute("select utrace.reclaim_expired_job_leases() as reclaimed")
        reclaimed_row = await cur.fetchone()
        await cur.execute("select utrace.expire_overdue_jobs() as expired")
        expired_row = await cur.fetchone()
        await cur.execute(
            "select utrace.requeue_stale_outbox(make_interval(secs => %s)) as requeued",
            (stale_outbox_seconds,),
        )
        requeued_row = await cur.fetchone()
        await cur.execute(
            """
            with stalled as (
                update public.commands
                set state = %s, reconciling_at = now(), revision = revision + 1,
                    updated_at = now()
                where state in (%s, %s)
                  and coalesce(dispatched_at, dispatching_at)
                      < now() - make_interval(secs => %s)
                returning 1
            )
            select count(*) as reconciling from stalled
            """,
            (
                CommandState.RECONCILING.value,
                CommandState.DISPATCHING.value,
                CommandState.DISPATCHED.value,
                stale_dispatch_seconds,
            ),
        )
        reconciling_row = await cur.fetchone()

    report = ReconciliationReport(
        reclaimed_leases=as_int(reclaimed_row, "reclaimed") if reclaimed_row else 0,
        expired_jobs=as_int(expired_row, "expired") if expired_row else 0,
        requeued_outbox=as_int(requeued_row, "requeued") if requeued_row else 0,
        commands_in_reconciliation=(
            as_int(reconciling_row, "reconciling") if reconciling_row else 0
        ),
    )
    _log.info(
        "startup_reconciliation_complete",
        reclaimed_leases=report.reclaimed_leases,
        expired_jobs=report.expired_jobs,
        requeued_outbox=report.requeued_outbox,
        commands_in_reconciliation=report.commands_in_reconciliation,
    )
    return report
