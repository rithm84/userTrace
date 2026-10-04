"""Criterion-level proof and the structured outcome derived from it.

Pure functions over already-gathered facts, so the
[outcome criteria](../../../../docs/arch-components/validation.md#outcome-criteria)
are testable without a database and cannot drift into a controller branch:

- ``validated`` needs the originating user's explicit confirmation **and**
  proportionate affirmative proof for every criterion on the exact candidate;
- an observable behaviour needs structured observed evidence, while a subjective
  criterion uses the user's explicit judgement about a workflow that was
  observed; an unobserved objective behaviour cannot be relabelled subjective;
- silence, elapsed time, and the absence of errors prove nothing;
- an enthusiastic statement over an observed error cannot validate;
- the newest observed run of a check decides it: a contradicting run the
  participant later repeated successfully is settled by that run and stays on
  record, while a contradiction with no later supporting run stands.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    CriterionResult,
    ValidationOutcome,
)

from app.validation.evidence import CriterionEvidenceItem, EvidenceStance

__all__ = [
    "CriterionAssessment",
    "CriterionProof",
    "assess_criterion",
    "derive_outcome",
]


@dataclass(frozen=True, slots=True)
class CriterionAssessment:
    """Everything gathered about one acceptance criterion during validation.

    ``user_confirmed`` is the originating user's explicit affirmation, and
    ``user_rejected`` their explicit denial. Both false means they said nothing
    decisive, which is not agreement.
    """

    criterion_id: UUID
    ordinal: int
    statement: str
    observability: CriterionObservability
    user_confirmed: bool
    user_rejected: bool
    confirmation_evidence_record_id: UUID | None
    workflow_observed: bool
    evidence: tuple[CriterionEvidenceItem, ...]


@dataclass(frozen=True, slots=True)
class CriterionProof:
    """The recorded proof for one criterion: result, sources, and reason."""

    criterion_id: UUID
    ordinal: int
    observability: CriterionObservability
    result: CriterionResult
    user_confirmed: bool
    confirmation_evidence_record_id: UUID | None
    supporting_evidence_record_ids: tuple[UUID, ...]
    contradicting_evidence_record_ids: tuple[UUID, ...]
    superseded_claim_record_ids: tuple[UUID, ...]
    reason: str


def _supporting(assessment: CriterionAssessment) -> tuple[CriterionEvidenceItem, ...]:
    return tuple(
        item
        for item in assessment.evidence
        if item.stance is EvidenceStance.SUPPORTS and item.is_observed_product_behaviour()
    )


def _contradicting(assessment: CriterionAssessment) -> tuple[CriterionEvidenceItem, ...]:
    """Contradictions no later supporting run settled."""
    return tuple(
        item
        for item in assessment.evidence
        if item.stance is EvidenceStance.CONTRADICTS and not _settled(item, assessment)
    )


def _settled(item: CriterionEvidenceItem, assessment: CriterionAssessment) -> bool:
    """Whether a later observed run of the same check supports it after this contradiction."""
    if item.observed_at is None:
        return False
    return any(
        later.observed_at is not None and later.observed_at > item.observed_at
        for later in _supporting(assessment)
    )


def _superseded(assessment: CriterionAssessment) -> tuple[UUID, ...]:
    corrected = tuple(
        item.supersedes_id for item in assessment.evidence if item.supersedes_id is not None
    )
    rerun = tuple(
        item.evidence_record_id
        for item in assessment.evidence
        if item.stance is EvidenceStance.CONTRADICTS and _settled(item, assessment)
    )
    return corrected + rerun


def assess_criterion(assessment: CriterionAssessment) -> CriterionProof:
    """Return the criterion-level proof for one gathered assessment."""
    supporting = _supporting(assessment)
    contradicting = _contradicting(assessment)
    supporting_ids = tuple(item.evidence_record_id for item in supporting)
    contradicting_ids = tuple(item.evidence_record_id for item in contradicting)

    result, reason = _decide(
        assessment=assessment, supporting=supporting, contradicting=contradicting
    )
    return CriterionProof(
        criterion_id=assessment.criterion_id,
        ordinal=assessment.ordinal,
        observability=assessment.observability,
        result=result,
        user_confirmed=assessment.user_confirmed,
        confirmation_evidence_record_id=assessment.confirmation_evidence_record_id,
        supporting_evidence_record_ids=supporting_ids,
        contradicting_evidence_record_ids=contradicting_ids,
        superseded_claim_record_ids=_superseded(assessment),
        reason=reason,
    )


def _decide(
    *,
    assessment: CriterionAssessment,
    supporting: Sequence[CriterionEvidenceItem],
    contradicting: Sequence[CriterionEvidenceItem],
) -> tuple[CriterionResult, str]:
    """Choose the criterion result and the sentence that explains it."""
    if contradicting:
        return (
            CriterionResult.CONTRADICTED,
            "observed evidence contradicts the claim: "
            + "; ".join(item.summary for item in contradicting),
        )
    if assessment.user_rejected:
        return (
            CriterionResult.UNMET,
            "the originating user rejected this criterion on the exact candidate",
        )
    if not assessment.user_confirmed:
        return (
            CriterionResult.UNCONFIRMED,
            "the originating user did not explicitly confirm this criterion",
        )
    if assessment.observability is CriterionObservability.SUBJECTIVE:
        if not assessment.workflow_observed:
            return (
                CriterionResult.UNCONFIRMED,
                "a subjective judgement needs an observed workflow to judge",
            )
        return (
            CriterionResult.MET,
            "the originating user judged an observed workflow to meet this criterion",
        )
    if not supporting:
        return (
            CriterionResult.UNCONFIRMED,
            (
                "an observable behaviour needs structured observed evidence; "
                "confirmation alone, silence, or an absence of errors is not proof"
            ),
        )
    return (
        CriterionResult.MET,
        "the originating user confirmed it and "
        f"{len(supporting)} observed item(s) affirm it: "
        + "; ".join(item.summary for item in supporting),
    )


def derive_outcome(proofs: Sequence[CriterionProof]) -> ValidationOutcome:
    """Derive the structured outcome from every criterion's proof.

    An empty criteria set cannot support a conclusion, so it is ``unclear``
    rather than a vacuous ``validated``.
    """
    if not proofs:
        return ValidationOutcome.UNCLEAR
    results = [proof.result for proof in proofs]
    if CriterionResult.CONTRADICTED in results:
        return ValidationOutcome.UNCLEAR
    met = results.count(CriterionResult.MET)
    if met == len(results):
        return ValidationOutcome.VALIDATED
    if met:
        return ValidationOutcome.PARTIALLY_VALIDATED
    if CriterionResult.UNMET in results:
        return ValidationOutcome.NOT_VALIDATED
    return ValidationOutcome.UNCLEAR
