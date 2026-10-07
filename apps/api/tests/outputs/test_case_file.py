"""Case File assembly, sealing, publication, and what may change afterwards."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    CaseFileStage,
    CaseFileVariant,
    CriterionObservability,
    EvidenceClass,
    InterviewSessionState,
    ProductSignalState,
)

from app.db.pool import Database
from app.errors import GuardRejectedError
from app.ids import uuid7
from app.outputs.amendments import load_amendments, record_amendment
from app.outputs.assembly import load_case_file_row
from app.outputs.bundle import build_bundle
from app.outputs.core_builder import build_core, verify_core
from app.outputs.drafting import DraftedClaim, deterministic_narrative
from app.outputs.handlers import assemble
from app.validation.completion import CriterionConfirmation, complete_validation
from app.validation.controller import begin_validation, open_validation_attempt
from app.validation.evidence import DurableEvidenceQuery, EvidenceStance
from tests.helpers import read_state
from tests.supabase_auth import SignedInUser
from tests.validation.scenario import (
    ValidationScenario,
    add_check_result,
    add_criterion_observation,
    add_implementation_artifact,
    add_intake_source,
    add_transcript_fragment,
    add_workflow_observation,
    binding_of,
    build_scenario,
)

ASSEMBLY_DEADLINE_SECONDS = 7 * 24 * 60 * 60

TWO_CRITERIA: tuple[tuple[str, CriterionObservability], ...] = (
    ("the saved view reopens with every filter", CriterionObservability.OBSERVED),
    ("restored state excludes adjacent segments", CriterionObservability.OBSERVED),
)

SOURCE_MESSAGE = (
    "A growth lead rebuilds the same conversion filter stack every Monday and asks "
    "to save a view and reopen it later."
)


async def _case_with_evidence(
    database: Database,
    auth_users: dict[str, SignedInUser],
    *,
    name: str,
) -> ValidationScenario:
    scenario = await build_scenario(
        database,
        name=name,
        contact_budget=2,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=auth_users["participant"].auth_user_id,
        criteria=TWO_CRITERIA,
        signal_state=ProductSignalState.AWAITING_VALIDATION,
        candidate_ready=True,
    )
    await add_intake_source(database, scenario, summary=SOURCE_MESSAGE)
    await add_transcript_fragment(
        database,
        scenario,
        field="expected_behavior",
        summary="the filter stack should still be there after a refresh",
    )
    await add_transcript_fragment(
        database,
        scenario,
        field="actual_behavior",
        summary="leaving the dashboard clears every applied filter",
    )
    await add_transcript_fragment(
        database,
        scenario,
        field="impact",
        summary="about twenty minutes of rebuilding every week",
    )
    await add_workflow_observation(
        database,
        scenario,
        milestone="reopen-saved-view",
        summary="the chart re-rendered with the saved filter values",
        preview_version_id=scenario.preview_version_id,
        coverage_limit="server spans were not captured for the export endpoint",
    )
    return scenario


async def _complete_validation(
    database: Database,
    scenario: ValidationScenario,
    *,
    confirmations: list[CriterionConfirmation],
) -> UUID:
    attempt_id = await open_validation_attempt(database, binding=binding_of(scenario))
    await begin_validation(
        database, attempt_id=attempt_id, originating_user_id=scenario.client_user_id
    )
    window = await DurableEvidenceQuery(database).freeze_window(
        client_id=scenario.client_id,
        product_signal_id=scenario.product_signal_id,
        interview_session_id=scenario.interview_session_id,
        purpose="validation",
        starts_at=datetime.now(tz=UTC) - timedelta(hours=2),
        ends_at=datetime.now(tz=UTC) + timedelta(minutes=5),
    )
    await complete_validation(
        database,
        DurableEvidenceQuery(database),
        attempt_id=attempt_id,
        evidence_window_id=window.evidence_window_id,
        confirmations=confirmations,
        assembly_deadline_seconds=ASSEMBLY_DEADLINE_SECONDS,
    )
    return attempt_id


def _confirm(criterion_id: UUID, *, confirmed: bool) -> CriterionConfirmation:
    return CriterionConfirmation(
        criterion_id=criterion_id,
        user_confirmed=confirmed,
        user_rejected=not confirmed,
        confirmation_evidence_record_id=None,
        workflow_observed=True,
    )


async def test_a_blocked_case_file_seals_publishes_and_closes_the_case(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Publication precedes closure, and the digest is stable across a retry."""
    scenario = await _case_with_evidence(
        database, auth_users, name="blocked-seal"
    )
    first, second = scenario.criteria
    await add_criterion_observation(
        database,
        scenario,
        criterion=first,
        stance=EvidenceStance.SUPPORTS,
        evidence_class=EvidenceClass.VALIDATION_OBSERVATION,
        summary="the saved view reopened with the agreed filters",
        supersedes_id=None,
    )
    attempt_id = await _complete_validation(
        database,
        scenario,
        confirmations=[
            _confirm(first.id, confirmed=True),
            _confirm(second.id, confirmed=False),
        ],
    )

    result = await assemble(
        database,
        DurableEvidenceQuery(database),
        product_signal_id=scenario.product_signal_id,
        variant=CaseFileVariant.BLOCKED,
        validation_attempt_id=attempt_id,
        blocker_reason="validation outcome 'partially_validated' left acceptance criteria unmet",
        closure_trigger="validation_unsuccessful",
        closure_reason="validation_unsuccessful",
        narrative_source="evidence_derived",
        drafter=None,
        repository=None,
    )
    assert result.stage is CaseFileStage.PUBLISHED
    assert result.core_digest is not None and len(result.core_digest) == 64

    state, _ = await read_state(
        database,
        table="product_signals",
        row_id=scenario.product_signal_id,
        column="state",
    )
    assert state == ProductSignalState.CLOSED_BLOCKED.value
    session_state, _ = await read_state(
        database,
        table="interview_sessions",
        row_id=scenario.interview_session_id,
        column="state",
    )
    closure_reason, _ = await read_state(
        database,
        table="interview_sessions",
        row_id=scenario.interview_session_id,
        column="closure_reason",
    )
    assert (session_state, closure_reason) == (
        InterviewSessionState.CLOSED.value,
        "not_validated",
    )

    replay = await assemble(
        database,
        DurableEvidenceQuery(database),
        product_signal_id=scenario.product_signal_id,
        variant=CaseFileVariant.BLOCKED,
        validation_attempt_id=attempt_id,
        blocker_reason="validation outcome 'partially_validated' left acceptance criteria unmet",
        closure_trigger="validation_unsuccessful",
        closure_reason="validation_unsuccessful",
        narrative_source="evidence_derived",
        drafter=None,
        repository=None,
    )
    assert replay.case_file_id == result.case_file_id
    assert replay.core_digest == result.core_digest

    async with database.connection() as conn:
        row = await load_case_file_row(conn, case_file_id=result.case_file_id)
    assert row.core_canonical is not None
    assert row.authenticated_url is not None
    assert str(result.case_file_id) in row.authenticated_url


async def test_a_validated_case_file_states_the_change_its_checks_and_its_scope(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Files, checks, cleanup, and gate scope come from the records that own them."""
    scenario = await _case_with_evidence(database, auth_users, name="validated-change")
    await add_check_result(database, scenario, name="lint", outcome="passed")
    await add_check_result(database, scenario, name="test", outcome="failed")
    await add_implementation_artifact(
        database,
        scenario,
        files=[
            ("src/components/dashboard/SavedViews.tsx", b"export {};\n"),
            ("src/components/dashboard/FilterBar.tsx", b"export {};\n"),
        ],
    )
    first, second = scenario.criteria
    for criterion in (first, second):
        await add_criterion_observation(
            database,
            scenario,
            criterion=criterion,
            stance=EvidenceStance.SUPPORTS,
            evidence_class=EvidenceClass.VALIDATION_OBSERVATION,
            summary="the saved view reopened with the agreed filters",
            supersedes_id=None,
        )
    attempt_id = await _complete_validation(
        database,
        scenario,
        confirmations=[_confirm(first.id, confirmed=True), _confirm(second.id, confirmed=True)],
    )
    window = await DurableEvidenceQuery(database).freeze_window(
        client_id=scenario.client_id,
        product_signal_id=scenario.product_signal_id,
        interview_session_id=scenario.interview_session_id,
        purpose="case_file",
        starts_at=datetime.now(tz=UTC) - timedelta(hours=2),
        ends_at=datetime.now(tz=UTC) + timedelta(minutes=5),
    )

    async with database.connection() as conn:
        bundle = await build_bundle(
            conn,
            client_id=scenario.client_id,
            product_signal_id=scenario.product_signal_id,
            variant=CaseFileVariant.VALIDATED_IMPLEMENTATION,
            evidence_window_id=window.evidence_window_id,
            validation_attempt_id=attempt_id,
            blocker_reason=None,
        )

    assert bundle.implementation is not None
    assert bundle.implementation.files_touched == (
        "src/components/dashboard/FilterBar.tsx",
        "src/components/dashboard/SavedViews.tsx",
    )
    assert [(check.name, check.outcome) for check in bundle.implementation.checks] == [
        ("lint", "passed"),
        ("test", "failed"),
    ]
    assert bundle.implementation.cleanup_state == "workspace destroyed"
    assert bundle.gate is not None
    assert bundle.gate.approved_scope == ("Save and reopen named conversion views.",)
    assert bundle.gate.excluded_scope == ("Sharing and notifications.",)
    assert bundle.gate.required_checks == ("pnpm test", "pnpm lint")


async def test_a_no_implementation_case_file_ends_the_session_without_a_change(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The session tab must not present a case the gate declined as a validated change."""
    scenario = await _case_with_evidence(database, auth_users, name="declined")
    async with database.transaction() as conn, conn.cursor() as cur:
        # The gate leaves a declined case here, with its Case File still to publish.
        await cur.execute(
            "update public.product_signals set state = %s where id = %s",
            (ProductSignalState.GATE_PENDING.value, scenario.product_signal_id),
        )
        await cur.execute(
            """
            insert into public.gate_decisions (
                id, client_id, product_signal_id, decision, blocker_reason,
                classification_revision, acceptance_criteria_revision,
                pinned_configuration_release_id, evidence_window_id, approved_scope,
                required_checks, budgets, cited_evidence, decided_at
            )
            select %s, client_id, product_signal_id, 'no_implementation',
                   'the bundle lacks persistence findings', classification_revision,
                   acceptance_criteria_revision, pinned_configuration_release_id,
                   evidence_window_id, '{}', '{"checks": []}', '{}', cited_evidence,
                   now()
            from public.gate_decisions where product_signal_id = %s
            """,
            (uuid7(), scenario.product_signal_id),
        )

    await assemble(
        database,
        DurableEvidenceQuery(database),
        product_signal_id=scenario.product_signal_id,
        variant=CaseFileVariant.NO_IMPLEMENTATION,
        validation_attempt_id=None,
        blocker_reason="the bundle lacks persistence findings",
        closure_trigger="no_implementation",
        closure_reason="the bundle lacks persistence findings",
        narrative_source="evidence_derived",
        drafter=None,
        repository=None,
    )

    closure_reason, _ = await read_state(
        database,
        table="interview_sessions",
        row_id=scenario.interview_session_id,
        column="closure_reason",
    )
    assert closure_reason == "no_implementation"


async def test_an_amendment_is_a_separate_record(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A correction never rewrites the published core."""
    scenario = await _case_with_evidence(
        database, auth_users, name="amendment"
    )
    first, second = scenario.criteria
    attempt_id = await _complete_validation(
        database,
        scenario,
        confirmations=[
            _confirm(first.id, confirmed=False),
            _confirm(second.id, confirmed=False),
        ],
    )
    result = await assemble(
        database,
        DurableEvidenceQuery(database),
        product_signal_id=scenario.product_signal_id,
        variant=CaseFileVariant.BLOCKED,
        validation_attempt_id=attempt_id,
        blocker_reason="validation left acceptance criteria unmet",
        closure_trigger="validation_unsuccessful",
        closure_reason="validation_unsuccessful",
        narrative_source="evidence_derived",
        drafter=None,
        repository=None,
    )
    amendment = await record_amendment(
        database,
        client_id=scenario.client_id,
        case_file_id=result.case_file_id,
        reason="the impact estimate was corrected after the interview",
        body={"field": "impact", "correction": "about thirty minutes every week"},
        actor_kind=ActorKind.SERVICE,
        actor_ref="outputs.amendment-test",
    )
    assert amendment.ordinal == 1
    amendments = await load_amendments(
        database, client_id=scenario.client_id, case_file_id=result.case_file_id
    )
    assert len(amendments) == 1

    async with database.connection() as conn:
        row = await load_case_file_row(conn, case_file_id=result.case_file_id)
    assert row.core_digest == result.core_digest


async def test_a_citation_outside_the_frozen_bundle_is_refused(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Only an authorized evidence slice may back a material claim."""
    scenario = await _case_with_evidence(
        database, auth_users, name="citations"
    )
    window = await DurableEvidenceQuery(database).freeze_window(
        client_id=scenario.client_id,
        product_signal_id=scenario.product_signal_id,
        interview_session_id=None,
        purpose="case_file",
        starts_at=datetime.now(tz=UTC) - timedelta(hours=2),
        ends_at=datetime.now(tz=UTC) + timedelta(minutes=5),
    )
    async with database.connection() as conn:
        bundle = await build_bundle(
            conn,
            client_id=scenario.client_id,
            product_signal_id=scenario.product_signal_id,
            variant=CaseFileVariant.BLOCKED,
            evidence_window_id=window.evidence_window_id,
            validation_attempt_id=None,
            blocker_reason="unreachable participant",
        )
    narrative = deterministic_narrative(bundle)
    forged = narrative.model_copy(
        update={
            "impact": DraftedClaim(
                statement="this costs the account a day a week",
                evidence_record_ids=[str(uuid7())],
            )
        }
    )
    with pytest.raises(GuardRejectedError) as refusal:
        build_core(
            bundle=bundle,
            narrative=forged,
            comparison=None,
            delivery=None,
            narrative_source="evidence_derived",
        )
    assert "not in this Case File's frozen evidence bundle" in refusal.value.reason


async def test_a_direct_personal_identifier_is_refused(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The published core carries opaque references, never a person's name."""
    scenario = await _case_with_evidence(
        database, auth_users, name="redaction"
    )
    window = await DurableEvidenceQuery(database).freeze_window(
        client_id=scenario.client_id,
        product_signal_id=scenario.product_signal_id,
        interview_session_id=None,
        purpose="case_file",
        starts_at=datetime.now(tz=UTC) - timedelta(hours=2),
        ends_at=datetime.now(tz=UTC) + timedelta(minutes=5),
    )
    async with database.connection() as conn:
        bundle = await build_bundle(
            conn,
            client_id=scenario.client_id,
            product_signal_id=scenario.product_signal_id,
            variant=CaseFileVariant.BLOCKED,
            evidence_window_id=window.evidence_window_id,
            validation_attempt_id=None,
            blocker_reason="unreachable participant",
        )
    narrative = deterministic_narrative(bundle)
    leaked = narrative.model_copy(
        update={
            "impact": DraftedClaim(
                statement="Maya Okonkwo rebuilds the stack every Monday",
                evidence_record_ids=[str(bundle.source_message.evidence_record_id)],
            )
        }
    )
    core = build_core(
        bundle=bundle,
        narrative=leaked,
        comparison=None,
        delivery=None,
        narrative_source="evidence_derived",
    )
    with pytest.raises(GuardRejectedError) as refusal:
        verify_core(
            core, bundle=bundle, forbidden_identifiers=("Maya Okonkwo",)
        )
    assert "direct personal identifier" in refusal.value.reason
