"""Every client that still tracks a live sandbox gets one deadline Job a minute.

A runtime past its planned end, or one no row tracks any more, gets none, so its
sandbox stops at its short deadline.
"""

from uuid import UUID

from utrace_contracts.domain.lifecycle import InterviewSessionState, ProductSignalState

from app.db.pool import Database
from app.execution.sandbox_deadlines import EXTEND_SANDBOX_DEADLINES
from tests.factories import CaseFixture, create_case, create_interview_session
from tests.helpers import count_rows
from tests.interview.fixtures import create_preview_allocation
from tests.supabase_auth import SignedInUser


async def _case_with_runtime(
    database: Database, owner: SignedInUser, *, name: str, planned_end_sql: str
) -> CaseFixture:
    case = await create_case(
        database,
        name=name,
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=owner.auth_user_id,
        participant_auth_user_id=None,
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    allocation_id: UUID = await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="bound"
    )
    async with database.transaction() as conn:
        await conn.execute(
            f"update public.preview_runtime_allocations set runtime_expires_at = {planned_end_sql} where id = %s",
            (allocation_id,),
        )
    return case


async def _scan(database: Database) -> int:
    async with database.connection() as conn:
        row = await (
            await conn.execute("select utrace.scan_sandbox_deadlines() as queued")
        ).fetchone()
    assert row is not None
    return int(row["queued"])


async def test_a_client_with_a_live_runtime_gets_one_deadline_job(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    live = await _case_with_runtime(
        database, auth_users["owner_a"], name="deadline-live", planned_end_sql="now() + interval '1 hour'"
    )
    ended = await _case_with_runtime(
        database, auth_users["owner_b"], name="deadline-ended", planned_end_sql="now() - interval '1 minute'"
    )

    assert await _scan(database) == 1
    # A Job already queued for the client is not queued again.
    assert await _scan(database) == 0
    for case, expected in ((live, 1), (ended, 0)):
        queued = await count_rows(
            database,
            table="jobs",
            where="job_type = %s and client_id = %s",
            params=[EXTEND_SANDBOX_DEADLINES, case.client_id],
        )
        assert queued == expected
