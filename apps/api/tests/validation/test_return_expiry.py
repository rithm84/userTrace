"""The terminal expiry of a case's return-and-closure deadline."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    InterviewSessionState,
    ProductSignalState,
    ValidationAttemptState,
)

from app.db.pool import Database
from app.execution.jobs import ClaimedJob
from app.ids import uuid7
from app.validation.controller import (
    EXPIRE_VALIDATION_RETURN,
    begin_validation,
    interrupt_validation,
    open_validation_attempt,
)
from app.validation.return_expiry import expire_validation_return
from tests.factories import create_case, create_interview_session
from tests.helpers import count_rows, read_state
from tests.supabase_auth import SignedInUser
from tests.validation.scenario import ValidationScenario, binding_of, build_scenario

ONE_CRITERION: tuple[tuple[str, CriterionObservability], ...] = (
    ("the saved view reopens with every filter", CriterionObservability.OBSERVED),
)


async def _scenario(
    database: Database,
    auth_users: dict[str, SignedInUser],
    *,
    name: str,
    contact_budget: int,
) -> ValidationScenario:
    return await build_scenario(
        database,
        name=name,
        contact_budget=contact_budget,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=auth_users["participant"].auth_user_id,
        criteria=ONE_CRITERION,
        signal_state=ProductSignalState.AWAITING_VALIDATION,
        candidate_ready=True,
    )


def _claimed(scenario: ValidationScenario, *, product_signal_id: UUID) -> ClaimedJob:
    now = datetime.now(tz=UTC)
    return ClaimedJob(
        job_id=uuid7(),
        job_run_id=uuid7(),
        client_id=scenario.client_id,
        job_type=EXPIRE_VALIDATION_RETURN,
        input={
            "product_signal_id": str(product_signal_id),
            "reason": "validation_return_deadline_expired",
        },
        fencing_token=1,
        lease_expires_at=now + timedelta(minutes=5),
        deadline_at=now + timedelta(hours=1),
        run_count=1,
    )


async def test_terminal_expiry_revokes_authority_and_obliges_a_case_file(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Expiry is terminal: no verdict, no resume path, one explanatory Case File."""
    scenario = await _scenario(
        database, auth_users, name="recontact-expiry", contact_budget=2
    )
    attempt_id = await open_validation_attempt(database, binding=binding_of(scenario))
    await begin_validation(
        database, attempt_id=attempt_id, originating_user_id=scenario.client_user_id
    )
    await interrupt_validation(
        database,
        attempt_id=attempt_id,
        reason="finish_later",
        return_window_seconds=0,
    )
    await expire_validation_return(
        database, _claimed(scenario, product_signal_id=scenario.product_signal_id)
    )

    attempt_state, _ = await read_state(
        database, table="validation_attempts", row_id=attempt_id, column="state"
    )
    assert attempt_state == ValidationAttemptState.ABANDONED.value

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select outcome from public.validation_attempts where id = %s",
            (attempt_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["outcome"] is None

    assert (
        await count_rows(
            database,
            table="jobs",
            where="job_type = 'outputs.assemble_case_file' and client_id = %s",
            params=(scenario.client_id,),
        )
        == 1
    )
    assert (
        await count_rows(
            database,
            table="jobs",
            where="job_type = 'offers.evaluate_entitlement' and client_id = %s",
            params=(scenario.client_id,),
        )
        == 1
    )

    signal_state, _ = await read_state(
        database,
        table="product_signals",
        row_id=scenario.product_signal_id,
        column="state",
    )
    assert signal_state == ProductSignalState.VALIDATING.value


async def test_expiry_before_participation_cancels_without_a_case_file(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A case that never reached participation ends like any pre-participation stop."""
    case = await create_case(
        database,
        name="expiry-before-participation",
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=auth_users["participant"].auth_user_id,
    )
    await create_interview_session(
        database,
        case=case,
        state=InterviewSessionState.CLOSED,
        conversational_ready=False,
    )
    now = datetime.now(tz=UTC)
    await expire_validation_return(
        database,
        ClaimedJob(
            job_id=uuid7(),
            job_run_id=uuid7(),
            client_id=case.client_id,
            job_type=EXPIRE_VALIDATION_RETURN,
            input={
                "product_signal_id": str(case.product_signal_id),
                "reason": "contact_expiry_without_return",
            },
            fencing_token=1,
            lease_expires_at=now + timedelta(minutes=5),
            deadline_at=now + timedelta(hours=1),
            run_count=1,
        ),
    )

    signal_state, _ = await read_state(
        database, table="product_signals", row_id=case.product_signal_id, column="state"
    )
    assert signal_state == ProductSignalState.CANCELED.value
    for job_type, expected in (
        ("outputs.assemble_case_file", 0),
        ("offers.evaluate_entitlement", 1),
    ):
        queued = await count_rows(
            database,
            table="jobs",
            where="job_type = %s and client_id = %s",
            params=(job_type, case.client_id),
        )
        assert queued == expected, job_type
