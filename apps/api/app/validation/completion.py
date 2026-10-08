"""Completing one Validation Attempt.

The completing transaction does four things at once, because separating them
would leave a window where an outcome exists without its consequence:

1. it records the criterion-level proof rows, joining the originating user's
   explicit confirmation with the affirmative observed evidence and any
   contradiction;
2. it commits the structured outcome and the frozen result, which carries the
   full binding tuple, the proofs, the contradiction check, and the candidate's
   source and artifact provenance;
3. it chooses the Case File the outcome requires: validated work gets the
   validated variant, and a partial, negative, or unclear outcome gets an
   explanatory unvalidated (blocked) Case File, because the demo grants no
   revisions;
4. it enqueues Case File assembly and the Outreach Offer entitlement evaluation.

Closure itself waits for publication: the Product Signal machine refuses every
``closed_*`` transition until its Case File is published.

Contract: docs/arch-components/validation.md#outcome-criteria
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts import payload_digest
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    CaseFileVariant,
    CriterionResult,
    ValidationOutcome,
)

from app.db.kernel import apply_transition_on_connection
from app.db.pool import Database
from app.envelope import Actor, AuditIntent, EventAppend, JobIntent, JsonValue, StreamRef
from app.errors import GuardRejectedError
from app.ids import uuid7
from app.interview.voice.session_changes import wake_voice_agents
from app.lifecycle.machine import plan_transition
from app.lifecycle.validation import VALIDATION_ATTEMPT_MACHINE, ValidationAttemptFacts
from app.logging import get_logger
from app.outputs.closure_jobs import case_file_assembly_job, entitlement_job
from app.validation.criteria import (
    CriterionAssessment,
    CriterionProof,
    assess_criterion,
    derive_outcome,
)
from app.validation.evidence import ADMISSIBLE_OBSERVED_CLASSES, EvidenceQueryPort
from app.validation.records import (
    CaseBudgets,
    ValidationAttemptRow,
    load_attempt,
    load_budgets,
    load_criteria_on_connection,
)

__all__ = [
    "UNSUCCESSFUL_VALIDATION",
    "CriterionConfirmation",
    "ValidationCompletion",
    "complete_validation",
    "gather_assessments",
]

_log = get_logger(__name__)

_AGENT_ACTOR = Actor(kind=ActorKind.UTRACE_AGENT_ROLE, ref="validation_controller")
_EVIDENCE_LIMIT = 200


UNSUCCESSFUL_VALIDATION = "validation_unsuccessful"
"""The Product Signal trigger that closes a validation which did not validate."""


@dataclass(frozen=True, slots=True)
class CriterionConfirmation:
    """What the originating user said about one criterion, and its source.

    ``workflow_observed`` says whether the agreed workflow was observed at all on
    the candidate: a subjective judgement needs something observed to judge.
    """

    criterion_id: UUID
    user_confirmed: bool
    user_rejected: bool
    confirmation_evidence_record_id: UUID | None
    workflow_observed: bool


@dataclass(frozen=True, slots=True)
class ValidationCompletion:
    """What the completing transaction committed."""

    validation_attempt_id: UUID
    outcome: ValidationOutcome
    proofs: tuple[CriterionProof, ...]
    frozen_result_digest: str
    case_file_variant: CaseFileVariant


async def gather_assessments(
    conn: AsyncConnection[DictRow],
    evidence: EvidenceQueryPort,
    *,
    attempt: ValidationAttemptRow,
    evidence_window_id: UUID,
    confirmations: Sequence[CriterionConfirmation],
) -> tuple[CriterionAssessment, ...]:
    """Join each frozen criterion with its confirmation and its observed evidence.

    Every criterion at the pinned revision is assessed. A criterion the caller
    supplied no confirmation for is assessed as unconfirmed rather than skipped,
    because an unmentioned criterion is not a met one.
    """
    criteria = await load_criteria_on_connection(
        conn,
        product_signal_id=attempt.product_signal_id,
        criteria_revision=attempt.acceptance_criteria_revision,
    )
    if not criteria:
        raise GuardRejectedError(
            machine=VALIDATION_ATTEMPT_MACHINE.name,
            trigger="complete",
            reason="the pinned criteria revision has no frozen acceptance criteria",
        )
    by_criterion = {item.criterion_id: item for item in confirmations}
    unknown = set(by_criterion) - {criterion.id for criterion in criteria}
    if unknown:
        raise GuardRejectedError(
            machine=VALIDATION_ATTEMPT_MACHINE.name,
            trigger="complete",
            reason=(
                "confirmations name criteria outside the pinned revision: "
                + ", ".join(sorted(str(item) for item in unknown))
            ),
        )

    assessments: list[CriterionAssessment] = []
    for criterion in criteria:
        items = await evidence.criterion_evidence(
            client_id=attempt.client_id,
            evidence_window_id=evidence_window_id,
            validation_criterion_id=criterion.id,
            allowed_source_classes=ADMISSIBLE_OBSERVED_CLASSES,
            limit=_EVIDENCE_LIMIT,
        )
        confirmation = by_criterion.get(criterion.id)
        assessments.append(
            CriterionAssessment(
                criterion_id=criterion.id,
                ordinal=criterion.ordinal,
                statement=criterion.statement,
                observability=criterion.observability,
                user_confirmed=(
                    confirmation.user_confirmed if confirmation is not None else False
                ),
                user_rejected=(
                    confirmation.user_rejected if confirmation is not None else False
                ),
                confirmation_evidence_record_id=(
                    confirmation.confirmation_evidence_record_id
                    if confirmation is not None
                    else None
                ),
                workflow_observed=(
                    confirmation.workflow_observed if confirmation is not None else False
                ),
                evidence=items,
            )
        )
    return tuple(assessments)


def _frozen_result(
    *,
    attempt: ValidationAttemptRow,
    budgets: CaseBudgets,
    evidence_window_id: UUID,
    outcome: ValidationOutcome,
    proofs: Sequence[CriterionProof],
    completed_at: datetime,
) -> dict[str, JsonValue]:
    """Build the record GitHub delivery and Case File assembly may begin from."""
    contradicted = [
        str(proof.criterion_id)
        for proof in proofs
        if proof.result is CriterionResult.CONTRADICTED
    ]
    return {
        "binding": {
            "client_id": str(attempt.client_id),
            "product_signal_id": str(attempt.product_signal_id),
            "originating_client_user_id": str(attempt.client_user_id),
            "originating_customer_account_id": str(
                budgets.originating_customer_account_id
            ),
            "implementation_attempt_id": str(attempt.implementation_attempt_id),
            "preview_version_id": str(attempt.preview_version_id),
            "preview_runtime_allocation_id": str(attempt.preview_runtime_allocation_id),
            "interview_session_id": str(attempt.interview_session_id),
            "dataset_manifest_digest": attempt.dataset_manifest_digest,
            "authenticated_identity_ref": attempt.authenticated_identity_ref,
            "acceptance_criteria_revision": attempt.acceptance_criteria_revision,
            "workflow_ref": attempt.workflow_ref,
            "evidence_window_id": str(evidence_window_id),
        },
        "outcome": outcome.value,
        "criterion_proofs": [
            {
                "criterion_id": str(proof.criterion_id),
                "ordinal": proof.ordinal,
                "observability": proof.observability.value,
                "result": proof.result.value,
                "user_confirmed": proof.user_confirmed,
                "confirmation_evidence_record_id": (
                    None
                    if proof.confirmation_evidence_record_id is None
                    else str(proof.confirmation_evidence_record_id)
                ),
                "supporting_evidence_record_ids": [
                    str(item) for item in proof.supporting_evidence_record_ids
                ],
                "contradicting_evidence_record_ids": [
                    str(item) for item in proof.contradicting_evidence_record_ids
                ],
                "superseded_claim_record_ids": [
                    str(item) for item in proof.superseded_claim_record_ids
                ],
                "reason": proof.reason,
            }
            for proof in proofs
        ],
        "contradiction_check": {
            "unresolved_contradictions": len(contradicted),
            "criterion_ids": contradicted,
        },
        "candidate_provenance": {
            "repository_ref": attempt.repository_ref,
            "source_commit": attempt.source_commit,
            "source_digest": attempt.source_digest,
            "build_digest": attempt.build_digest,
            "base_revision": attempt.candidate_base_revision,
        },
        "completed_at": completed_at.isoformat(),
    }


async def _write_proofs(
    conn: AsyncConnection[DictRow],
    *,
    attempt: ValidationAttemptRow,
    proofs: Sequence[CriterionProof],
) -> None:
    """Insert the immutable criterion-level proof records."""
    for proof in proofs:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.validation_outcomes (
                    id, client_id, validation_attempt_id, validation_criterion_id,
                    result, user_confirmed, evidence_record_ids, observability,
                    confirmation_evidence_record_id, contradicting_evidence_record_ids,
                    superseded_claim_record_ids, reason
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid7(),
                    attempt.client_id,
                    attempt.id,
                    proof.criterion_id,
                    proof.result.value,
                    proof.user_confirmed,
                    list(proof.supporting_evidence_record_ids),
                    proof.observability.value,
                    proof.confirmation_evidence_record_id,
                    list(proof.contradicting_evidence_record_ids),
                    list(proof.superseded_claim_record_ids),
                    proof.reason,
                ),
            )


async def complete_validation(
    database: Database,
    evidence: EvidenceQueryPort,
    *,
    attempt_id: UUID,
    evidence_window_id: UUID,
    confirmations: Sequence[CriterionConfirmation],
    assembly_deadline_seconds: int,
) -> ValidationCompletion:
    """Record the structured outcome, its proof, and its consequence atomically."""
    now = datetime.now(tz=UTC)
    async with database.transaction() as conn:
        attempt = await load_attempt(conn, attempt_id=attempt_id)
        budgets = await load_budgets(conn, product_signal_id=attempt.product_signal_id)
        assessments = await gather_assessments(
            conn,
            evidence,
            attempt=attempt,
            evidence_window_id=evidence_window_id,
            confirmations=confirmations,
        )
        proofs = tuple(assess_criterion(assessment) for assessment in assessments)
        outcome = derive_outcome(proofs)
        frozen = _frozen_result(
            attempt=attempt,
            budgets=budgets,
            evidence_window_id=evidence_window_id,
            outcome=outcome,
            proofs=proofs,
            completed_at=now,
        )
        digest = payload_digest(frozen)

        if outcome is ValidationOutcome.VALIDATED:
            variant = CaseFileVariant.VALIDATED_IMPLEMENTATION
            blocker_reason = None
            closure_trigger = "validated"
        else:
            variant = CaseFileVariant.BLOCKED
            blocker_reason = (
                f"validation outcome '{outcome.value}' left acceptance criteria unmet"
            )
            closure_trigger = UNSUCCESSFUL_VALIDATION

        await _write_proofs(conn, attempt=attempt, proofs=proofs)

        jobs = (
            case_file_assembly_job(
                product_signal_id=attempt.product_signal_id,
                validation_attempt_id=attempt.id,
                variant=variant,
                blocker_reason=blocker_reason,
                closure_trigger=closure_trigger,
                closure_reason=blocker_reason or "validated",
                now=now,
                deadline_seconds=assembly_deadline_seconds,
            ),
            # Completing validation is participation through validation, whatever
            # the outcome: honest negative feedback still earns the offer.
            entitlement_job(
                product_signal_id=attempt.product_signal_id,
                stop_reason="validation_completed",
                now=now,
                deadline_seconds=assembly_deadline_seconds,
            ),
        )

        await _complete_attempt(
            conn,
            attempt=attempt,
            proofs=proofs,
            outcome=outcome,
            evidence_window_id=evidence_window_id,
            frozen=frozen,
            digest=digest,
            completed_at=now,
            jobs=jobs,
        )

    _log.info(
        "validation_attempt_completed",
        client_id=str(attempt.client_id),
        product_signal_id=str(attempt.product_signal_id),
        validation_attempt_id=str(attempt.id),
        outcome=outcome.value,
        frozen_result_digest=digest,
        criteria_total=len(proofs),
        criteria_met=sum(1 for proof in proofs if proof.result is CriterionResult.MET),
    )
    return ValidationCompletion(
        validation_attempt_id=attempt.id,
        outcome=outcome,
        proofs=proofs,
        frozen_result_digest=digest,
        case_file_variant=variant,
    )


async def _complete_attempt(
    conn: AsyncConnection[DictRow],
    *,
    attempt: ValidationAttemptRow,
    proofs: Sequence[CriterionProof],
    outcome: ValidationOutcome,
    evidence_window_id: UUID,
    frozen: Mapping[str, JsonValue],
    digest: str,
    completed_at: datetime,
    jobs: Sequence[JobIntent],
) -> None:
    """Commit the Validation Attempt's terminal transition."""
    met = sum(1 for proof in proofs if proof.result is CriterionResult.MET)
    contradicted = any(
        proof.result is CriterionResult.CONTRADICTED for proof in proofs
    )
    stream = StreamRef(kind=AggregateKind.VALIDATION_ATTEMPT, id=attempt.id)
    payload: Mapping[str, JsonValue] = {
        "outcome": outcome.value,
        "frozen_result_digest": digest,
        "criteria_total": len(proofs),
        "criteria_met": met,
    }
    request = plan_transition(
        VALIDATION_ATTEMPT_MACHINE,
        ValidationAttemptFacts(
            originating_user_matches=True,
            candidate_ready=True,
            observation_ready=True,
            same_candidate=True,
            outcome=outcome,
            criteria_total=len(proofs),
            criteria_affirmatively_proven=met,
            user_confirmed_every_criterion=all(proof.user_confirmed for proof in proofs),
            unresolved_contradiction=contradicted,
        ),
        trigger="complete",
        current_state=attempt.state.value,
        client_id=attempt.client_id,
        aggregate_kind=AggregateKind.VALIDATION_ATTEMPT,
        aggregate_id=attempt.id,
        expected_revision=attempt.revision,
        state_column="state",
        mutation={
            "outcome": outcome.value,
            "completed_at": completed_at,
            "evidence_window_id": evidence_window_id,
            "frozen_result": Jsonb(frozen),
            "frozen_result_digest": digest,
        },
        event=EventAppend(
            message_id=uuid7(),
            stream=stream,
            event_type="validation_attempt_completed",
            payload=payload,
            actor=_AGENT_ACTOR,
            causation_id=None,
            correlation_id=attempt.product_signal_id,
            occurred_at=completed_at,
        ),
        jobs=jobs,
        commands=(),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=stream,
            action="complete_validation",
            outcome=outcome.value,
            reason_code=None,
            actor=_AGENT_ACTOR,
            pinned_configuration_release_id=None,
            detail=payload,
        ),
        additional_locks=(),
    )
    await apply_transition_on_connection(conn, request)
    # The voice agent closes the conversation on this outcome.
    await wake_voice_agents(conn, product_signal_id=attempt.product_signal_id)
