"""Same-user validation against the real database.

Binding, readiness, interruption, and the completing transaction are transactional
guarantees, so they are proved against real rows rather than a substitute.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest
from utrace_contracts.domain.lifecycle import (
    CaseFileVariant,
    CriterionObservability,
    EvidenceClass,
    ProductSignalState,
    ValidationOutcome,
)

from app.db.pool import Database
from app.errors import GuardRejectedError
from app.ids import uuid7
from app.validation.completion import CriterionConfirmation, complete_validation
from app.validation.controller import (
    EXPIRE_VALIDATION_RETURN,
    begin_validation,
    interrupt_validation,
    open_validation_attempt,
    resume_validation,
)
from app.validation.evidence import DurableEvidenceQuery, EvidenceStance
from tests.helpers import count_rows, read_state
from tests.supabase_auth import SignedInUser
from tests.validation.scenario import (
    ScenarioCriterion,
    ValidationScenario,
    add_criterion_observation,
    binding_of,
    build_scenario,
)

ASSEMBLY_DEADLINE_SECONDS = 7 * 24 * 60 * 60

TWO_CRITERIA: tuple[tuple[str, CriterionObservability], ...] = (
    ("the saved view is restored with every filter", CriterionObservability.OBSERVED),
    (
        "restored state excludes adjacent seeded segments",
        CriterionObservability.OBSERVED,
    ),
)


async def _scenario(
    database: Database,
    auth_users: dict[str, SignedInUser],
    *,
    name: str,
    candidate_ready: bool,
) -> ValidationScenario:
    return await build_scenario(
        database,
        name=name,
        contact_budget=2,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=auth_users["participant"].auth_user_id,
        criteria=TWO_CRITERIA,
        signal_state=ProductSignalState.AWAITING_VALIDATION,
        candidate_ready=candidate_ready,
    )


async def _freeze(database: Database, scenario: ValidationScenario) -> UUID:
    from datetime import UTC, datetime, timedelta

    window = await DurableEvidenceQuery(database).freeze_window(
        client_id=scenario.client_id,
        product_signal_id=scenario.product_signal_id,
        interview_session_id=scenario.interview_session_id,
        purpose="validation",
        starts_at=datetime.now(tz=UTC) - timedelta(hours=2),
        ends_at=datetime.now(tz=UTC) + timedelta(minutes=5),
    )
    return window.evidence_window_id


def _confirm(criterion: ScenarioCriterion, *, confirmed: bool) -> CriterionConfirmation:
    return CriterionConfirmation(
        criterion_id=criterion.id,
        user_confirmed=confirmed,
        user_rejected=not confirmed,
        confirmation_evidence_record_id=None,
        workflow_observed=True,
    )


async def _support(
    database: Database, scenario: ValidationScenario, criterion: ScenarioCriterion
) -> UUID:
    return await add_criterion_observation(
        database,
        scenario,
        criterion=criterion,
        stance=EvidenceStance.SUPPORTS,
        evidence_class=EvidenceClass.VALIDATION_OBSERVATION,
        summary=f"the candidate satisfied criterion {criterion.ordinal}",
        supersedes_id=None,
    )


async def test_only_the_originating_user_may_be_bound(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A colleague or operator cannot be substituted into the binding."""
    scenario = await _scenario(
        database,
        auth_users,
        name="bind-user",
        candidate_ready=True,
    )
    with pytest.raises(GuardRejectedError) as refusal:
        await open_validation_attempt(
            database, binding=replace(binding_of(scenario), client_user_id=uuid7())
        )
    assert "immutable originating user" in refusal.value.reason


async def test_an_unready_candidate_cannot_be_bound(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Criterion-level validation requires the exact ready candidate."""
    scenario = await _scenario(
        database,
        auth_users,
        name="bind-unready",
        candidate_ready=False,
    )
    with pytest.raises(GuardRejectedError) as refusal:
        await open_validation_attempt(database, binding=binding_of(scenario))
    assert "ready candidate" in refusal.value.reason


async def test_a_mismatched_dataset_manifest_is_refused(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The agreed dataset is part of the binding, not a label."""
    scenario = await _scenario(
        database,
        auth_users,
        name="bind-dataset",
        candidate_ready=True,
    )
    with pytest.raises(GuardRejectedError) as refusal:
        await open_validation_attempt(
            database,
            binding=replace(
                binding_of(scenario), dataset_manifest_digest="sha256:another-dataset"
            ),
        )
    assert "dataset manifest digest" in refusal.value.reason


async def test_beginning_validation_moves_the_case_into_validating(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    scenario = await _scenario(
        database,
        auth_users,
        name="begin",
        candidate_ready=True,
    )
    attempt_id = await open_validation_attempt(database, binding=binding_of(scenario))
    await begin_validation(
        database, attempt_id=attempt_id, originating_user_id=scenario.client_user_id
    )
    state, _ = await read_state(
        database,
        table="product_signals",
        row_id=scenario.product_signal_id,
        column="state",
    )
    assert state == ProductSignalState.VALIDATING.value


async def test_interruption_records_one_case_deadline_and_never_extends_it(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Pause, retry, and repeated departures do not reset the closure deadline."""
    scenario = await _scenario(
        database,
        auth_users,
        name="interrupt",
        candidate_ready=True,
    )
    attempt_id = await open_validation_attempt(database, binding=binding_of(scenario))
    await begin_validation(
        database, attempt_id=attempt_id, originating_user_id=scenario.client_user_id
    )
    first = await interrupt_validation(
        database,
        attempt_id=attempt_id,
        reason="transport_lost",
        return_window_seconds=3600,
    )
    await resume_validation(
        database,
        attempt_id=attempt_id,
        originating_user_id=scenario.client_user_id,
        preview_version_id=scenario.preview_version_id,
    )
    second = await interrupt_validation(
        database,
        attempt_id=attempt_id,
        reason="finish_later",
        return_window_seconds=99999,
    )
    assert second == first
    assert (
        await count_rows(
            database,
            table="jobs",
            where="job_type = %s and client_id = %s",
            params=(EXPIRE_VALIDATION_RETURN, scenario.client_id),
        )
        == 1
    )


async def test_resuming_against_a_different_candidate_is_refused(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    scenario = await _scenario(
        database,
        auth_users,
        name="resume",
        candidate_ready=True,
    )
    attempt_id = await open_validation_attempt(database, binding=binding_of(scenario))
    await begin_validation(
        database, attempt_id=attempt_id, originating_user_id=scenario.client_user_id
    )
    await interrupt_validation(
        database,
        attempt_id=attempt_id,
        reason="transport_lost",
        return_window_seconds=3600,
    )
    with pytest.raises(GuardRejectedError) as refusal:
        await resume_validation(
            database,
            attempt_id=attempt_id,
            originating_user_id=scenario.client_user_id,
            preview_version_id=uuid7(),
        )
    assert "same exact candidate" in refusal.value.reason


async def test_validated_requires_confirmation_and_affirmative_evidence(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Both criteria confirmed and observed: the frozen result carries the binding."""
    scenario = await _scenario(
        database,
        auth_users,
        name="validated",
        candidate_ready=True,
    )
    for criterion in scenario.criteria:
        await _support(database, scenario, criterion)
    attempt_id = await open_validation_attempt(database, binding=binding_of(scenario))
    await begin_validation(
        database, attempt_id=attempt_id, originating_user_id=scenario.client_user_id
    )
    window_id = await _freeze(database, scenario)

    completion = await complete_validation(
        database,
        DurableEvidenceQuery(database),
        attempt_id=attempt_id,
        evidence_window_id=window_id,
        confirmations=[
            _confirm(criterion, confirmed=True) for criterion in scenario.criteria
        ],
        assembly_deadline_seconds=ASSEMBLY_DEADLINE_SECONDS,
    )
    assert completion.outcome is ValidationOutcome.VALIDATED
    assert len(completion.frozen_result_digest) == 64

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select frozen_result, frozen_result_digest, evidence_window_id "
            "from public.validation_attempts where id = %s",
            (attempt_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    frozen = row["frozen_result"]
    assert frozen["binding"]["preview_version_id"] == str(scenario.preview_version_id)
    assert frozen["binding"]["dataset_manifest_digest"] == scenario.dataset_manifest_digest
    assert frozen["binding"]["evidence_window_id"] == str(window_id)
    assert frozen["candidate_provenance"]["source_commit"] == scenario.source_commit
    assert frozen["contradiction_check"]["unresolved_contradictions"] == 0
    assert row["frozen_result_digest"] == completion.frozen_result_digest

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


async def test_enthusiasm_over_an_observed_error_does_not_validate(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A contradicted criterion prevents a supported conclusion."""
    scenario = await _scenario(
        database,
        auth_users,
        name="contradicted",
        candidate_ready=True,
    )
    first, second = scenario.criteria
    await _support(database, scenario, first)
    await _support(database, scenario, second)
    await add_criterion_observation(
        database,
        scenario,
        criterion=second,
        stance=EvidenceStance.CONTRADICTS,
        evidence_class=EvidenceClass.SERVER_SPAN,
        summary="the restore endpoint returned 500 on the candidate",
        supersedes_id=None,
    )
    attempt_id = await open_validation_attempt(database, binding=binding_of(scenario))
    await begin_validation(
        database, attempt_id=attempt_id, originating_user_id=scenario.client_user_id
    )
    window_id = await _freeze(database, scenario)

    completion = await complete_validation(
        database,
        DurableEvidenceQuery(database),
        attempt_id=attempt_id,
        evidence_window_id=window_id,
        confirmations=[
            _confirm(criterion, confirmed=True) for criterion in scenario.criteria
        ],
        assembly_deadline_seconds=ASSEMBLY_DEADLINE_SECONDS,
    )
    assert completion.outcome is ValidationOutcome.UNCLEAR
    assert completion.case_file_variant is CaseFileVariant.BLOCKED


async def test_a_partial_outcome_closes_with_an_unvalidated_case_file(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A partial outcome closes with a blocker Case File."""
    scenario = await _scenario(
        database,
        auth_users,
        name="exhausted",
        candidate_ready=True,
    )
    first, second = scenario.criteria
    await _support(database, scenario, first)
    attempt_id = await open_validation_attempt(database, binding=binding_of(scenario))
    await begin_validation(
        database, attempt_id=attempt_id, originating_user_id=scenario.client_user_id
    )
    window_id = await _freeze(database, scenario)

    completion = await complete_validation(
        database,
        DurableEvidenceQuery(database),
        attempt_id=attempt_id,
        evidence_window_id=window_id,
        confirmations=[
            _confirm(first, confirmed=True),
            _confirm(second, confirmed=False),
        ],
        assembly_deadline_seconds=ASSEMBLY_DEADLINE_SECONDS,
    )
    assert completion.outcome is ValidationOutcome.PARTIALLY_VALIDATED
    assert completion.case_file_variant is CaseFileVariant.BLOCKED
    state, _ = await read_state(
        database,
        table="product_signals",
        row_id=scenario.product_signal_id,
        column="state",
    )
    assert state == ProductSignalState.VALIDATING.value

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select input from public.jobs "
            "where job_type = 'outputs.assemble_case_file' and client_id = %s",
            (scenario.client_id,),
        )
        job = await cur.fetchone()
    assert job is not None
    assert job["input"]["variant"] == "blocked"
    assert job["input"]["closure_trigger"] == "validation_unsuccessful"
