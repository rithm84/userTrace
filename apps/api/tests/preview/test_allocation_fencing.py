"""Only the holder of an allocation's current fencing token may move it."""

from uuid import UUID

import pytest
from utrace_contracts.domain.lifecycle import InterviewSessionState, ProductSignalState

from app.db.pool import Database
from app.errors import LeaseLostError
from app.preview.machines import PreviewRuntimeAllocationFacts
from app.preview.pool import commit_allocation_transition
from tests.factories import CaseFixture, create_case, create_interview_session
from tests.interview.fixtures import create_preview_allocation
from tests.supabase_auth import SignedInUser


async def _take_over(database: Database, *, allocation_id: UUID) -> int:
    """Mint a new fencing token, as `acquire_allocation` does for a new holder."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "select utrace.acquire_preview_allocation(%s) as token", (allocation_id,)
        )
        row = await cur.fetchone()
    assert row is not None
    return int(row["token"])


async def _release(
    database: Database, *, case: CaseFixture, allocation_id: UUID, fencing_token: int
) -> str:
    async with database.transaction() as conn:
        return await commit_allocation_transition(
            conn,
            client_id=case.client_id,
            allocation_id=allocation_id,
            fencing_token=fencing_token,
            trigger="release",
            facts=PreviewRuntimeAllocationFacts(
                runtime_reachable=True,
                seed_manifest_matches=True,
                readiness_admits_traffic=False,
                routes_revoked=True,
                evidence_extracted=True,
            ),
            mutation={},
            event_type="preview_runtime_allocation_released",
            payload={"reason": "case_ended"},
            reason_code="case_ended",
            correlation_id=None,
            additional_locks=(),
        )


async def test_a_worker_whose_allocation_was_taken_over_cannot_move_it(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await create_case(
        database,
        name="allocation-fencing",
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    allocation_id = await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="bound"
    )
    superseded = await _take_over(database, allocation_id=allocation_id)
    current = await _take_over(database, allocation_id=allocation_id)

    with pytest.raises(LeaseLostError):
        await _release(database, case=case, allocation_id=allocation_id, fencing_token=superseded)
    released = await _release(
        database, case=case, allocation_id=allocation_id, fencing_token=current
    )

    assert released == "released"
