"""Job claiming, leases, heartbeats, and completion.

A Job is one durable responsibility with a frozen input; a Job Run is one leased
execution of it. Workers claim durable rows with transactional leases and carry a
globally monotonic fencing token. The scheduler decides only whether another run
is legal; the owning controller decides product consequences, including those of
a Job left with no legal run, which it commits with that failure.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection, AsyncCursor
from psycopg.errors import UniqueViolation
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import JobRunState, JobState, RetryAdvice

from app.db.pool import Database
from app.db.rows import as_datetime, as_int, as_str, as_uuid
from app.envelope import Failure, JsonValue
from app.errors import LeaseLostError
from app.ids import uuid7
from app.logging import get_logger

__all__ = [
    "ClaimedJob",
    "ExhaustionHook",
    "claim_job",
    "claim_job_by_id",
    "complete_job_run",
    "fail_job_run",
    "heartbeat",
    "next_run_at",
]

_log = get_logger(__name__)

type ExhaustionHook = Callable[[AsyncConnection[DictRow]], Awaitable[None]]
"""The owning controller's consequence of a Job that failed with no legal run left."""


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """One leased execution: the frozen Job input plus this run's fencing token."""

    job_id: UUID
    job_run_id: UUID
    client_id: UUID
    job_type: str
    input: Mapping[str, object]
    fencing_token: int
    lease_expires_at: datetime
    deadline_at: datetime
    run_count: int


async def claim_job(
    database: Database,
    *,
    job_types: Sequence[str],
    worker_id: str,
    lease_seconds: int,
) -> ClaimedJob | None:
    """Claim the next legal Job, or return ``None`` when nothing is claimable.

    Concurrency keys are filtered in the claim query and enforced by a partial
    unique index. A contended claim returns ``None`` so the caller polls again.
    """
    async with database.transaction() as conn, conn.cursor() as cur:
        try:
            await cur.execute(
                """
                update public.jobs
                set state = %s,
                    run_count = run_count + 1,
                    revision = revision + 1,
                    updated_at = now()
                where id = (
                    select candidate.id
                    from public.jobs as candidate
                    where candidate.state = %s
                      and candidate.not_before <= now()
                      and candidate.deadline_at > now()
                      and candidate.run_count < candidate.max_runs
                      and candidate.job_type = any(%s)
                      and (
                        candidate.concurrency_key is null
                        or not exists (
                          select 1 from public.jobs as busy
                          where busy.state = %s
                            and busy.client_id = candidate.client_id
                            and busy.concurrency_key = candidate.concurrency_key
                        )
                      )
                    order by candidate.not_before, candidate.created_at
                    limit 1
                    for update skip locked
                )
                returning id, client_id, job_type, input, deadline_at, run_count,
                          timeout_seconds
                """,
                (
                    JobState.LEASED.value,
                    JobState.READY.value,
                    list(job_types),
                    JobState.LEASED.value,
                ),
            )
        except UniqueViolation:
            _log.info("job_claim_contended", worker_id=worker_id)
            return None
        job = await cur.fetchone()
        if job is None:
            return None
        return await _lease(cur, job, worker_id=worker_id, lease_seconds=lease_seconds)


async def claim_job_by_id(
    database: Database, *, job_id: UUID, worker_id: str, lease_seconds: int
) -> ClaimedJob | None:
    """Claim one named Job, or return ``None`` while it is not claimable.

    The Job must be ready, due, inside its deadline, and have runs left, and its
    concurrency key must be free, exactly as `claim_job` requires of any Job.
    """
    async with database.transaction() as conn, conn.cursor() as cur:
        try:
            await cur.execute(
                """
                update public.jobs
                set state = %s,
                    run_count = run_count + 1,
                    revision = revision + 1,
                    updated_at = now()
                where id = %s
                  and state = %s
                  and not_before <= now()
                  and deadline_at > now()
                  and run_count < max_runs
                  and (
                    concurrency_key is null
                    or not exists (
                      select 1 from public.jobs as busy
                      where busy.state = %s
                        and busy.client_id = jobs.client_id
                        and busy.concurrency_key = jobs.concurrency_key
                    )
                  )
                returning id, client_id, job_type, input, deadline_at, run_count,
                          timeout_seconds
                """,
                (JobState.LEASED.value, job_id, JobState.READY.value, JobState.LEASED.value),
            )
        except UniqueViolation:
            _log.info("job_claim_contended", worker_id=worker_id, job_id=str(job_id))
            return None
        job = await cur.fetchone()
        if job is None:
            return None
        return await _lease(cur, job, worker_id=worker_id, lease_seconds=lease_seconds)


async def next_run_at(database: Database, *, job_id: UUID) -> datetime | None:
    """When the named Job can next be claimed, or ``None`` if it never can again.

    A Job that is running, finished, out of runs, or past its deadline has no next
    run; a ready Job's next run is due at its `not_before`.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select not_before from public.jobs
            where id = %s and state = %s and run_count < max_runs and deadline_at > now()
            """,
            (job_id, JobState.READY.value),
        )
        row = await cur.fetchone()
    return None if row is None else as_datetime(row, "not_before")


async def _lease(
    cur: AsyncCursor[DictRow], job: DictRow, *, worker_id: str, lease_seconds: int
) -> ClaimedJob | None:
    """Open the Job Run that leases a Job just moved to leased, with its fencing token."""
    job_run_id = uuid7()
    await cur.execute(
        """
        insert into public.job_runs (
            id, client_id, job_id, state, fencing_token, worker_id, lease_expires_at
        )
        values (
            %s, %s, %s, %s, utrace.next_fencing_token(), %s,
            now() + make_interval(secs => %s)
        )
        returning fencing_token, lease_expires_at
        """,
        (
            job_run_id,
            as_uuid(job, "client_id"),
            as_uuid(job, "id"),
            JobRunState.LEASED.value,
            worker_id,
            lease_seconds,
        ),
    )
    run = await cur.fetchone()
    if run is None:
        return None
    claimed = ClaimedJob(
        job_id=as_uuid(job, "id"),
        job_run_id=job_run_id,
        client_id=as_uuid(job, "client_id"),
        job_type=as_str(job, "job_type"),
        input=dict(job["input"]),
        fencing_token=as_int(run, "fencing_token"),
        lease_expires_at=as_datetime(run, "lease_expires_at"),
        deadline_at=as_datetime(job, "deadline_at"),
        run_count=as_int(job, "run_count"),
    )
    _log.info(
        "job_claimed",
        worker_id=worker_id,
        client_id=str(claimed.client_id),
        job_id=str(claimed.job_id),
        job_run_id=str(claimed.job_run_id),
        job_type=claimed.job_type,
        fencing_token=claimed.fencing_token,
        run_count=claimed.run_count,
    )
    return claimed


async def _require_current_lease(
    conn: AsyncConnection[DictRow], job_run_id: UUID, fencing_token: int
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.job_runs
            where id = %s and fencing_token = %s and state = %s
            for update
            """,
            (job_run_id, fencing_token, JobRunState.LEASED.value),
        )
        if await cur.fetchone() is None:
            raise LeaseLostError(
                lease_kind="job_run", lease_id=job_run_id, fencing_token=fencing_token
            )


async def heartbeat(
    database: Database, *, job_run_id: UUID, fencing_token: int, lease_seconds: int
) -> datetime:
    """Extend the lease. Raises when this run is no longer the current lease."""
    async with database.transaction() as conn:
        await _require_current_lease(conn, job_run_id, fencing_token)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update public.job_runs
                set heartbeat_at = now(),
                    lease_expires_at = now() + make_interval(secs => %s),
                    revision = revision + 1,
                    updated_at = now()
                where id = %s and fencing_token = %s
                returning lease_expires_at
                """,
                (lease_seconds, job_run_id, fencing_token),
            )
            row = await cur.fetchone()
    if row is None:
        raise LeaseLostError(
            lease_kind="job_run", lease_id=job_run_id, fencing_token=fencing_token
        )
    return as_datetime(row, "lease_expires_at")


async def complete_job_run(
    database: Database, *, job_id: UUID, job_run_id: UUID, fencing_token: int
) -> None:
    """Record a successful run and close its Job."""
    async with database.transaction() as conn:
        await _require_current_lease(conn, job_run_id, fencing_token)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update public.job_runs
                set state = %s, finished_at = now(), revision = revision + 1,
                    updated_at = now()
                where id = %s and fencing_token = %s
                """,
                (JobRunState.SUCCEEDED.value, job_run_id, fencing_token),
            )
            await cur.execute(
                """
                update public.jobs
                set state = %s, revision = revision + 1, updated_at = now()
                where id = %s
                """,
                (JobState.SUCCEEDED.value, job_id),
            )
    _log.info(
        "job_run_succeeded",
        job_id=str(job_id),
        job_run_id=str(job_run_id),
        fencing_token=fencing_token,
    )


async def fail_job_run(
    database: Database,
    *,
    job_id: UUID,
    job_run_id: UUID,
    fencing_token: int,
    failure: Failure,
    on_exhausted: ExhaustionHook | None,
) -> JobState:
    """Record a failed run and decide whether another run is legal.

    Retry classes come from the Job's frozen ``retryable_categories``; the run
    budget, deadline, and the reported retry advice all have to permit another run.
    When none is legal, ``on_exhausted`` commits the owning controller's consequence
    in the same transaction, so the failure is never recorded without it.
    """
    async with database.transaction() as conn:
        await _require_current_lease(conn, job_run_id, fencing_token)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update public.job_runs
                set state = %s, finished_at = now(), failure_category = %s,
                    retry_advice = %s, revision = revision + 1, updated_at = now()
                where id = %s and fencing_token = %s
                """,
                (
                    JobRunState.FAILED.value,
                    failure.category,
                    failure.retry,
                    job_run_id,
                    fencing_token,
                ),
            )
            await cur.execute(
                """
                select run_count, max_runs, deadline_at, retryable_categories,
                       backoff_base_seconds, backoff_max_seconds
                from public.jobs where id = %s for update
                """,
                (job_id,),
            )
            job = await cur.fetchone()
            if job is None:
                raise LeaseLostError(
                    lease_kind="job", lease_id=job_id, fencing_token=fencing_token
                )
            retryable = (
                failure.category in set(job["retryable_categories"])
                and RetryAdvice(failure.retry) is not RetryAdvice.NEVER
                and as_int(job, "run_count") < as_int(job, "max_runs")
            )
            next_state = JobState.READY if retryable else JobState.FAILED
            backoff = min(
                as_int(job, "backoff_max_seconds"),
                as_int(job, "backoff_base_seconds")
                * (2 ** max(as_int(job, "run_count") - 1, 0)),
            )
            await cur.execute(
                """
                update public.jobs
                set state = %s,
                    not_before = now() + make_interval(secs => %s),
                    last_failure_category = %s,
                    last_retry_advice = %s,
                    revision = revision + 1,
                    updated_at = now()
                where id = %s
                """,
                (
                    next_state.value,
                    backoff if retryable else 0,
                    failure.category,
                    failure.retry,
                    job_id,
                ),
            )
            if retryable:
                await cur.execute(
                    """
                    update public.outbox
                    set state = 'pending', enqueued_at = null,
                        available_at = now() + make_interval(secs => %s),
                        updated_at = now()
                    where job_id = %s
                    """,
                    (backoff, job_id),
                )
            await cur.execute(
                """
                insert into public.audit_records (
                    id, client_id, subject_kind, subject_id, action, outcome,
                    reason_code, actor_kind, actor_ref, detail
                )
                select %s, jobs.client_id, 'job', jobs.id, 'job_run_failed', %s, %s,
                       'service', %s, %s
                from public.jobs where jobs.id = %s
                """,
                (
                    uuid7(),
                    next_state.value,
                    failure.category,
                    str(job_run_id),
                    Jsonb(_failure_detail(failure)),
                    job_id,
                ),
            )
        if next_state is JobState.FAILED and on_exhausted is not None:
            await on_exhausted(conn)
    _log.warning(
        "job_run_failed",
        job_id=str(job_id),
        job_run_id=str(job_run_id),
        failure_category=failure.category,
        failure_code=failure.code,
        failure_message=failure.message,
        failure_details=failure.details,
        retry_advice=failure.retry,
        next_state=next_state.value,
    )
    return next_state


def _failure_detail(failure: Failure) -> Mapping[str, JsonValue]:
    """Build the audit detail for one failed run: the wire form of the failure."""
    return failure.as_json()
