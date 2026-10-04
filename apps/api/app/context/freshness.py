"""Rejecting results whose dependencies moved while they ran.

Every worker result names the bundle it used. Before the control plane applies
one, it rechecks that bundle: the revisions its purpose depends on must still be
current (`BundlePurpose.dependencies`) and no correction may have invalidated it.
A stale result stays diagnostic evidence and cannot update the current scope or
cause an effect.

Unchanged independent results remain usable after this check, which is why the
check reports the exact dependency that moved rather than a single boolean. A
controller that owns a decision rebuilds its context and decides again when its
result went stale (`decide_on_fresh_context`).
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.context.bundles import BundleDependencies, current_dependencies
from app.context.purposes import BundleDependency, BundlePurpose
from app.db.pool import Database
from app.db.rows import as_uuid
from app.errors import StaleContextError
from app.logging import get_logger

__all__ = [
    "FreshnessOutcome",
    "check_bundle_freshness",
    "check_bundle_freshness_on_connection",
    "decide_on_fresh_context",
    "invalidate_bundles_citing",
]

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FreshnessOutcome:
    """Whether a bundle may still authorize an effect, and what moved if not."""

    fresh: bool
    stale_dependencies: tuple[str, ...]
    invalidation_reason: str | None

    @property
    def detail(self) -> str:
        """A log-safe explanation naming the exact dependencies that moved."""
        if self.fresh:
            return "every pinned dependency is still current"
        parts: list[str] = []
        if self.invalidation_reason is not None:
            parts.append(f"invalidated: {self.invalidation_reason}")
        if self.stale_dependencies:
            parts.append(f"moved: {', '.join(self.stale_dependencies)}")
        return "; ".join(parts)


async def check_bundle_freshness(
    database: Database, *, bundle_id: UUID
) -> FreshnessOutcome:
    """Recheck one bundle's dependencies and its invalidation record."""
    async with database.connection() as conn:
        return await check_bundle_freshness_on_connection(conn, bundle_id=bundle_id)


async def check_bundle_freshness_on_connection(
    conn: AsyncConnection[DictRow], *, bundle_id: UUID
) -> FreshnessOutcome:
    """Recheck one bundle inside the caller's transaction.

    A caller holding the Product Signal's row lock applies its result against
    exactly the revisions this read, because nothing can move them before it
    commits.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select product_signal_id, interview_session_id, purpose, dependencies
            from public.context_bundles
            where id = %s
            """,
            (bundle_id,),
        )
        header = await cur.fetchone()
        if header is None:
            raise LookupError(f"context bundle {bundle_id} does not exist")
        await cur.execute(
            "select reason from public.context_bundle_invalidations where context_bundle_id = %s",
            (bundle_id,),
        )
        invalidation = await cur.fetchone()
    product_signal_id = as_uuid(header, "product_signal_id")
    if header["interview_session_id"] is None:
        raise LookupError(f"context bundle {bundle_id} belongs to no Interview Session")
    dependency_payload = header["dependencies"]
    if not isinstance(dependency_payload, dict):
        raise TypeError("context_bundles.dependencies is not an object")
    purpose = BundlePurpose(str(header["purpose"]))
    pinned = BundleDependencies.from_payload(dependency_payload).as_payload()
    current = (
        await current_dependencies(
            conn,
            product_signal_id=product_signal_id,
            interview_session_id=as_uuid(header, "interview_session_id"),
        )
    ).as_payload()
    moved = tuple(
        dependency.value
        for dependency in BundleDependency
        if dependency in purpose.dependencies and current[dependency] != pinned[dependency]
    )

    reason = str(invalidation["reason"]) if invalidation is not None else None
    outcome = FreshnessOutcome(
        fresh=not moved and reason is None,
        stale_dependencies=moved,
        invalidation_reason=reason,
    )
    if not outcome.fresh:
        _log.info(
            "context_bundle_stale",
            context_bundle_id=str(bundle_id),
            product_signal_id=str(product_signal_id),
            purpose=purpose.value,
            stale_dependencies=list(outcome.stale_dependencies),
            invalidation_reason=outcome.invalidation_reason,
        )
    return outcome


async def decide_on_fresh_context[T](
    *,
    step: str,
    product_signal_id: UUID,
    attempts: int,
    decide: Callable[[], Awaitable[T]],
) -> T:
    """Make one decision, deciding again on fresh context while the case moves under it.

    ``decide`` assembles its own context on every call. A `StaleContextError` means
    a dependency moved while it ran, so the decision is made again, logged, up to
    ``attempts`` times in all; the last refusal is raised.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, got {attempts}")
    attempt = 1
    while True:
        try:
            return await decide()
        except StaleContextError as stale:
            if attempt >= attempts:
                raise
            _log.warning(
                "context_stale_deciding_again",
                step=step,
                product_signal_id=str(product_signal_id),
                attempt=attempt,
                attempts=attempts,
                trigger=stale.trigger,
                reason=stale.reason,
            )
            attempt += 1


async def invalidate_bundles_citing(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    product_signal_id: UUID,
    evidence_ids: Sequence[UUID],
    reason: str,
    caused_by_evidence_id: UUID,
) -> tuple[UUID, ...]:
    """Invalidate every bundle that cited one of these evidence records.

    The bundles stay immutable and remain diagnostic evidence; the invalidation
    row is what withdraws their authority to cause an effect.
    """
    if not evidence_ids:
        return ()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select distinct items.context_bundle_id
            from public.context_bundle_items as items
            join public.context_bundles as bundles
              on bundles.id = items.context_bundle_id
            where bundles.product_signal_id = %s
              and items.source_kind = 'evidence_record'
              and items.included
              and items.source_id = any(%s)
              and not exists (
                select 1 from public.context_bundle_invalidations as existing
                where existing.context_bundle_id = items.context_bundle_id
              )
            """,
            (product_signal_id, [str(identifier) for identifier in evidence_ids]),
        )
        bundle_ids = tuple(as_uuid(row, "context_bundle_id") for row in await cur.fetchall())
        for bundle_id in bundle_ids:
            await cur.execute(
                """
                insert into public.context_bundle_invalidations (
                    context_bundle_id, client_id, product_signal_id, reason,
                    caused_by_evidence_id
                )
                values (%s, %s, %s, %s, %s)
                """,
                (bundle_id, client_id, product_signal_id, reason, caused_by_evidence_id),
            )
    if bundle_ids:
        _log.info(
            "context_bundles_invalidated",
            client_id=str(client_id),
            product_signal_id=str(product_signal_id),
            caused_by_evidence_id=str(caused_by_evidence_id),
            reason=reason,
            invalidated=len(bundle_ids),
        )
    return bundle_ids
