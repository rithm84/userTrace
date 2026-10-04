"""The outcome rules, proved without a database.

These are the rules the whole validation contract turns on, so they are exercised
directly rather than only through a controller: confirmation without observed
evidence is not proof, enthusiasm over an observed error cannot validate, and an
unobserved objective behaviour cannot be relabelled subjective.
"""

from __future__ import annotations

from datetime import UTC, datetime

from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    CriterionResult,
    EvidenceClaimState,
    EvidenceClass,
    ValidationOutcome,
)

from app.ids import uuid7
from app.validation.criteria import (
    CriterionAssessment,
    assess_criterion,
    derive_outcome,
)
from app.validation.evidence import CriterionEvidenceItem, EvidenceStance


def _item(
    *,
    stance: EvidenceStance,
    evidence_class: EvidenceClass,
    summary: str,
    observed_at: datetime | None,
) -> CriterionEvidenceItem:
    return CriterionEvidenceItem(
        evidence_record_id=uuid7(),
        evidence_class=evidence_class,
        claim_state=EvidenceClaimState.CONFIRMED,
        source_ref="observation:test",
        stance=stance,
        summary=summary,
        recorded_at=datetime.now(tz=UTC),
        observed_at=observed_at,
        supersedes_id=None,
    )


def _assessment(
    *,
    observability: CriterionObservability,
    user_confirmed: bool,
    user_rejected: bool,
    workflow_observed: bool,
    evidence: tuple[CriterionEvidenceItem, ...],
) -> CriterionAssessment:
    return CriterionAssessment(
        criterion_id=uuid7(),
        ordinal=1,
        statement="reopening the saved view restores every filter",
        observability=observability,
        user_confirmed=user_confirmed,
        user_rejected=user_rejected,
        confirmation_evidence_record_id=uuid7() if user_confirmed else None,
        workflow_observed=workflow_observed,
        evidence=evidence,
    )


def test_confirmation_plus_observed_evidence_meets_an_observable_criterion() -> None:
    proof = assess_criterion(
        _assessment(
            observability=CriterionObservability.OBSERVED,
            user_confirmed=True,
            user_rejected=False,
            workflow_observed=True,
            evidence=(
                _item(
                    stance=EvidenceStance.SUPPORTS,
                    evidence_class=EvidenceClass.BROWSER_OBSERVATION,
                    summary="the restored chart state matched the saved filters",
                    observed_at=None,
                ),
            ),
        )
    )
    assert proof.result is CriterionResult.MET
    assert len(proof.supporting_evidence_record_ids) == 1
    assert derive_outcome((proof,)) is ValidationOutcome.VALIDATED


def test_confirmation_alone_is_not_proof_of_an_observable_behaviour() -> None:
    proof = assess_criterion(
        _assessment(
            observability=CriterionObservability.OBSERVED,
            user_confirmed=True,
            user_rejected=False,
            workflow_observed=True,
            evidence=(),
        )
    )
    assert proof.result is CriterionResult.UNCONFIRMED
    assert "structured observed evidence" in proof.reason
    assert derive_outcome((proof,)) is ValidationOutcome.UNCLEAR


def test_a_transcript_fragment_is_not_observed_product_behaviour() -> None:
    """The user saying it worked is their statement, not the product's behaviour."""
    proof = assess_criterion(
        _assessment(
            observability=CriterionObservability.OBSERVED,
            user_confirmed=True,
            user_rejected=False,
            workflow_observed=True,
            evidence=(
                _item(
                    stance=EvidenceStance.SUPPORTS,
                    evidence_class=EvidenceClass.TRANSCRIPT_FRAGMENT,
                    summary="that looks right to me",
                    observed_at=None,
                ),
            ),
        )
    )
    assert proof.result is CriterionResult.UNCONFIRMED
    assert not proof.supporting_evidence_record_ids


def test_enthusiasm_over_an_observed_error_cannot_validate() -> None:
    proof = assess_criterion(
        _assessment(
            observability=CriterionObservability.OBSERVED,
            user_confirmed=True,
            user_rejected=False,
            workflow_observed=True,
            evidence=(
                _item(
                    stance=EvidenceStance.SUPPORTS,
                    evidence_class=EvidenceClass.BROWSER_OBSERVATION,
                    summary="the saved view opened",
                    observed_at=None,
                ),
                _item(
                    stance=EvidenceStance.CONTRADICTS,
                    evidence_class=EvidenceClass.SERVER_SPAN,
                    summary="the restore endpoint returned 500",
                    observed_at=None,
                ),
            ),
        )
    )
    assert proof.result is CriterionResult.CONTRADICTED
    assert derive_outcome((proof,)) is ValidationOutcome.UNCLEAR


def test_the_newest_observed_run_of_a_check_decides_it() -> None:
    first_run = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
    second_run = datetime(2026, 10, 3, 9, 2, tzinfo=UTC)
    failed = _item(
        stance=EvidenceStance.CONTRADICTS,
        evidence_class=EvidenceClass.BROWSER_OBSERVATION,
        summary="the reopened view lacked the segment filter",
        observed_at=first_run,
    )
    repeated = _item(
        stance=EvidenceStance.SUPPORTS,
        evidence_class=EvidenceClass.BROWSER_OBSERVATION,
        summary="the reopened view restored every saved filter",
        observed_at=second_run,
    )
    settled = assess_criterion(
        _assessment(
            observability=CriterionObservability.OBSERVED,
            user_confirmed=True,
            user_rejected=False,
            workflow_observed=True,
            evidence=(failed, repeated),
        )
    )
    assert settled.result is CriterionResult.MET
    assert settled.superseded_claim_record_ids == (failed.evidence_record_id,)

    regressed = assess_criterion(
        _assessment(
            observability=CriterionObservability.OBSERVED,
            user_confirmed=True,
            user_rejected=False,
            workflow_observed=True,
            evidence=(
                _item(
                    stance=EvidenceStance.SUPPORTS,
                    evidence_class=EvidenceClass.BROWSER_OBSERVATION,
                    summary="the reopened view restored every saved filter",
                    observed_at=first_run,
                ),
                _item(
                    stance=EvidenceStance.CONTRADICTS,
                    evidence_class=EvidenceClass.BROWSER_OBSERVATION,
                    summary="the reopened view lacked the segment filter",
                    observed_at=second_run,
                ),
            ),
        )
    )
    assert regressed.result is CriterionResult.CONTRADICTED


def test_a_subjective_criterion_needs_an_observed_workflow_to_judge() -> None:
    unobserved = assess_criterion(
        _assessment(
            observability=CriterionObservability.SUBJECTIVE,
            user_confirmed=True,
            user_rejected=False,
            workflow_observed=False,
            evidence=(),
        )
    )
    assert unobserved.result is CriterionResult.UNCONFIRMED

    observed = assess_criterion(
        _assessment(
            observability=CriterionObservability.SUBJECTIVE,
            user_confirmed=True,
            user_rejected=False,
            workflow_observed=True,
            evidence=(),
        )
    )
    assert observed.result is CriterionResult.MET


def test_an_explicit_rejection_is_not_validated() -> None:
    proof = assess_criterion(
        _assessment(
            observability=CriterionObservability.OBSERVED,
            user_confirmed=False,
            user_rejected=True,
            workflow_observed=True,
            evidence=(),
        )
    )
    assert proof.result is CriterionResult.UNMET
    assert derive_outcome((proof,)) is ValidationOutcome.NOT_VALIDATED


def test_some_met_and_some_not_is_partially_validated() -> None:
    met = assess_criterion(
        _assessment(
            observability=CriterionObservability.OBSERVED,
            user_confirmed=True,
            user_rejected=False,
            workflow_observed=True,
            evidence=(
                _item(
                    stance=EvidenceStance.SUPPORTS,
                    evidence_class=EvidenceClass.VALIDATION_OBSERVATION,
                    summary="the filters came back",
                    observed_at=None,
                ),
            ),
        )
    )
    unmet = assess_criterion(
        _assessment(
            observability=CriterionObservability.OBSERVED,
            user_confirmed=False,
            user_rejected=True,
            workflow_observed=True,
            evidence=(),
        )
    )
    assert derive_outcome((met, unmet)) is ValidationOutcome.PARTIALLY_VALIDATED


def test_no_criteria_cannot_support_a_conclusion() -> None:
    assert derive_outcome(()) is ValidationOutcome.UNCLEAR
