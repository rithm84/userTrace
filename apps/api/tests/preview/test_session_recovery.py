"""A session preview that never recovers closes the session on the blocker path.

The recovery run is claimed by the real worker loop. Its deadline has already
passed, so the run takes the technical-blocker path without reaching a sandbox.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from utrace_contracts.domain.lifecycle import (
    InterviewSessionState,
    JobState,
    ProductSignalState,
)

from app.db.pool import Database
from app.db.rows import as_str
from app.execution.worker import Worker, WorkerConfig
from app.offers.job_types import EVALUATE_ENTITLEMENT
from app.outputs.job_types import ASSEMBLE_CASE_FILE
from app.preview.jobs import recover_session_preview_job
from app.preview.session_recovery import (
    PREVIEW_UNRECOVERABLE,
    RECOVER_SESSION_PREVIEW,
    schedule_recovery,
)
from tests.factories import CaseFixture, create_case, create_interview_session
from tests.helpers import count_rows, read_state
from tests.interview.fixtures import create_preview_allocation
from tests.supabase_auth import SignedInUser


async def _failed_preview(
    database: Database, owner: SignedInUser, *, name: str, participated: bool
) -> tuple[CaseFixture, UUID]:
    """A case whose session holds a bound runtime that never became ready."""
    case = await create_case(
        database,
        name=name,
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=owner.auth_user_id,
        participant_auth_user_id=None,
    )
    session_id = await create_interview_session(
        database,
        case=case,
        state=InterviewSessionState.LIVE if participated else InterviewSessionState.PREPARING,
        conversational_ready=participated,
    )
    await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="bound"
    )
    await schedule_recovery(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        recover_by=datetime.now(tz=UTC) - timedelta(minutes=1),
        failure="GuardRejectedError: a gating readiness probe did not pass",
    )
    return case, session_id


async def _jobs(database: Database, case: CaseFixture, job_type: str) -> int:
    return await count_rows(
        database,
        table="jobs",
        where="job_type = %s and input->>'product_signal_id' = %s",
        params=[job_type, str(case.product_signal_id)],
    )


async def _session_end(database: Database, session_id: UUID) -> tuple[str, str]:
    """Return the session's closure reason and the end reason its tab shows."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select sessions.closure_reason, projections.projection->>'end_reason' as end_reason
            from public.interview_sessions as sessions
            join public.session_projections as projections
              on projections.session_id = sessions.id
            where sessions.id = %s
            """,
            (session_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    return as_str(row, "closure_reason"), as_str(row, "end_reason")


async def test_an_expired_preview_recovery_takes_the_blocker_path(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    participated, participated_session = await _failed_preview(
        database, auth_users["owner_a"], name="recovery-after", participated=True
    )
    early, early_session = await _failed_preview(
        database, auth_users["owner_a"], name="recovery-before", participated=False
    )
    assert await _jobs(database, participated, RECOVER_SESSION_PREVIEW) == 1

    worker = Worker(
        database,
        WorkerConfig(
            worker_id="recovery-worker",
            lease_seconds=60,
            heartbeat_seconds=30,
            poll_interval_seconds=0.1,
            wakeup_batch_size=10,
            outbox_scan_limit=100,
            stale_outbox_seconds=300,
            stale_dispatch_seconds=900,
            concurrency=1,
        ),
        {RECOVER_SESSION_PREVIEW: recover_session_preview_job},
        {},
    )
    assert await worker.run_once() is True
    assert await worker.run_once() is True
    assert await worker.run_once() is False
    assert (
        await count_rows(
            database,
            table="jobs",
            where="job_type = %s and state = %s",
            params=[RECOVER_SESSION_PREVIEW, JobState.SUCCEEDED.value],
        )
        == 2
    )

    for session_id in (participated_session, early_session):
        assert await _session_end(database, session_id) == ("blocked", "blocked")

    # After participation the case waits for its blocker Case File.
    state, _ = await read_state(
        database, table="product_signals", row_id=participated.product_signal_id, column="state"
    )
    assert state == ProductSignalState.INTERVIEWING.value
    assert (
        await count_rows(
            database,
            table="jobs",
            where=(
                "job_type = %s and input->>'product_signal_id' = %s "
                "and input->>'variant' = 'blocked' and input->>'closure_reason' = %s"
            ),
            params=[ASSEMBLE_CASE_FILE, str(participated.product_signal_id), PREVIEW_UNRECOVERABLE],
        )
        == 1
    )
    assert await _jobs(database, participated, EVALUATE_ENTITLEMENT) == 1

    # Before participation it is canceled with no Case File.
    state, _ = await read_state(
        database, table="product_signals", row_id=early.product_signal_id, column="state"
    )
    assert state == ProductSignalState.CANCELED.value
    assert await _jobs(database, early, ASSEMBLE_CASE_FILE) == 0
    assert await _jobs(database, early, EVALUATE_ENTITLEMENT) == 1
