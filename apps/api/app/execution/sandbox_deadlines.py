"""Short sandbox deadlines that a scheduled Job keeps extending.

Contract: docs/arch-components/preview-runtime-and-seeding.md#sandbox-deadlines

A sandbox that outlives one call, a preview runtime or a prepared workspace, is
created with a deadline at most `DEADLINE_SECONDS` away rather than its whole planned
lifetime. `utrace.scan_sandbox_deadlines` queues `EXTEND_SANDBOX_DEADLINES` for every
client that tracks one, and the Job extends each tracked sandbox whose deadline is
within `EXTEND_WITHIN_SECONDS`, to `DEADLINE_SECONDS` from now but never past the
planned end its row records. A sandbox no row tracks any more, because its test or
script died, a reset truncated its row, or a worker crashed between creating it and
recording it, is never extended, so it stops within `DEADLINE_SECONDS` instead of
idling to the end of a long fixed limit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from app.connectors.failures import ConnectorFailure
from app.connectors.vercel_sandboxes import (
    SandboxHandle,
    VercelSandboxes,
    sandbox_grant_from_environment,
)
from app.db.pool import Database
from app.db.rows import as_datetime, as_str
from app.execution.jobs import ClaimedJob
from app.logging import get_logger

__all__ = [
    "DEADLINE_SECONDS",
    "EXTEND_SANDBOX_DEADLINES",
    "EXTEND_WITHIN_SECONDS",
    "extend_sandbox_deadlines",
    "extension_seconds",
    "initial_deadline_seconds",
    "keep_alive",
]

_log = get_logger(__name__)

DEADLINE_SECONDS: Final[int] = 1200
"""How far ahead a tracked sandbox's deadline is kept: what an untracked one can idle."""

EXTEND_WITHIN_SECONDS: Final[int] = 600
"""A deadline closer than this is extended. The scan runs every minute, so a tracked
sandbox survives about ten missed runs before it stops."""

EXTEND_SANDBOX_DEADLINES: Final[str] = "sandboxes.extend_deadlines"
"""Extend the deadlines of one client's tracked sandboxes. Queued by a scheduled scan."""


def initial_deadline_seconds(planned_seconds: int) -> int:
    """Return the execution limit a sandbox with `planned_seconds` of life is created with."""
    return min(planned_seconds, DEADLINE_SECONDS)


def extension_seconds(*, deadline: datetime, planned_end: datetime, now: datetime) -> int:
    """Return how far to extend `deadline`, or 0 when it is not yet due or already at its end."""
    if deadline - now >= timedelta(seconds=EXTEND_WITHIN_SECONDS):
        return 0
    target = min(planned_end, now + timedelta(seconds=DEADLINE_SECONDS))
    return max(0, int((target - deadline).total_seconds()))


async def keep_alive(
    sandboxes: VercelSandboxes, handle: SandboxHandle, *, planned_end: datetime
) -> None:
    """Extend a running sandbox's deadline toward `planned_end` when it is due."""
    deadline = sandboxes.running_deadline(handle)
    if deadline is None:
        return
    seconds = extension_seconds(
        deadline=deadline, planned_end=planned_end, now=datetime.now(tz=UTC)
    )
    if seconds >= 1:
        await sandboxes.extend_execution_time(handle, seconds=seconds)


async def extend_sandbox_deadlines(database: Database, claimed: ClaimedJob) -> None:
    """Extend the due deadlines of every sandbox the client still tracks.

    Every tracked sandbox is tried even when one fails, so one provider error does not
    let the others lapse; the first failure is raised once all have been tried.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select runtime_ref as sandbox_name, runtime_expires_at as planned_end
            from public.preview_runtime_allocations
            where client_id = %s
              and state <> 'terminated'
              and runtime_ref is not null
              and runtime_expires_at > now()
            union all
            select sandbox_name, expires_at as planned_end
            from public.prepared_workspaces
            where client_id = %s
              and state in ('preparing', 'ready', 'drafting')
              and expires_at > now()
            """,
            (claimed.client_id, claimed.client_id),
        )
        rows = await cur.fetchall()
    failures: list[ConnectorFailure] = []
    async with VercelSandboxes(grant=sandbox_grant_from_environment()) as sandboxes:
        for row in rows:
            name = as_str(row, "sandbox_name")
            try:
                handle = await sandboxes.find(name=name)
                if handle is not None:
                    await keep_alive(
                        sandboxes, handle, planned_end=as_datetime(row, "planned_end")
                    )
            except ConnectorFailure as failure:
                _log.warning(
                    "sandbox_deadline_extension_failed",
                    client_id=str(claimed.client_id),
                    sandbox=name,
                    category=failure.detail.category.value,
                    message=failure.detail.message,
                )
                failures.append(failure)
    _log.info(
        "sandbox_deadlines_extended",
        client_id=str(claimed.client_id),
        tracked=len(rows),
        failed=len(failures),
    )
    if failures:
        raise failures[0]
