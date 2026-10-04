"""Staff reads of one case's validation record.

The frozen result is what authorizes delivery, so the dashboard reads exactly
that record rather than a re-derived summary: the binding tuple, the
criterion-level proof, and the contradiction check, as committed.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict
from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    CriterionResult,
    ValidationAttemptState,
    ValidationOutcome,
)

from app.auth.dependencies import CurrentDatabase, StaffOfPath
from app.db.rows import as_bool, as_int, as_optional_str, as_str, as_uuid
from app.errors import AggregateNotFoundError

__all__ = ["router"]

router = APIRouter(prefix="/v1/clients", tags=["staff"])


class ApiModel(BaseModel):
    """Base for every response here: closed to extra fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CriterionProofResponse(ApiModel):
    """One criterion's recorded proof."""

    criterion_id: UUID
    ordinal: int
    statement: str
    observability: CriterionObservability
    result: CriterionResult
    user_confirmed: bool
    supporting_evidence_record_ids: list[UUID]
    contradicting_evidence_record_ids: list[UUID]
    reason: str


class ValidationRecordResponse(ApiModel):
    """One Validation Attempt, its binding, and its criterion-level proof."""

    validation_attempt_id: UUID
    product_signal_id: UUID
    state: ValidationAttemptState
    outcome: ValidationOutcome | None
    preview_version_id: UUID
    implementation_attempt_id: UUID
    interview_session_id: UUID
    client_user_id: UUID
    dataset_manifest_digest: str
    authenticated_identity_ref: str
    acceptance_criteria_revision: int
    workflow_ref: str
    evidence_window_id: UUID | None
    frozen_result_digest: str | None
    frozen_result: dict[str, object] | None
    criterion_proofs: list[CriterionProofResponse]


@router.get(
    "/{client_id}/cases/{product_signal_id}/validation",
    response_model=ValidationRecordResponse,
)
async def get_validation_record(
    staff: StaffOfPath,
    database: CurrentDatabase,
    product_signal_id: Annotated[UUID, Path()],
) -> ValidationRecordResponse:
    """Return the case's most recent Validation Attempt and its frozen proof."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
                select id, product_signal_id, state, outcome,
                       preview_version_id, implementation_attempt_id,
                       interview_session_id, client_user_id, dataset_manifest_digest,
                       authenticated_identity_ref, acceptance_criteria_revision,
                       workflow_ref, evidence_window_id, frozen_result_digest,
                       frozen_result
                from public.validation_attempts
                where product_signal_id = %s and client_id = %s
                order by created_at desc
                limit 1
                """,
            (product_signal_id, staff.client_id),
        )
        attempt = await cur.fetchone()
        if attempt is None:
            raise AggregateNotFoundError(
                table="validation_attempts", row_id=product_signal_id
            )
        await cur.execute(
            """
                select criteria.id as criterion_id, criteria.ordinal,
                       criteria.statement, outcomes.observability, outcomes.result,
                       outcomes.user_confirmed, outcomes.evidence_record_ids,
                       outcomes.contradicting_evidence_record_ids, outcomes.reason
                from public.validation_outcomes as outcomes
                join public.validation_criteria as criteria
                  on criteria.id = outcomes.validation_criterion_id
                where outcomes.validation_attempt_id = %s
                order by criteria.ordinal
                """,
            (as_uuid(attempt, "id"),),
        )
        proofs = list(await cur.fetchall())

    frozen = attempt["frozen_result"]
    outcome = as_optional_str(attempt, "outcome")
    return ValidationRecordResponse(
        validation_attempt_id=as_uuid(attempt, "id"),
        product_signal_id=as_uuid(attempt, "product_signal_id"),
        state=ValidationAttemptState(as_str(attempt, "state")),
        outcome=None if outcome is None else ValidationOutcome(outcome),
        preview_version_id=as_uuid(attempt, "preview_version_id"),
        implementation_attempt_id=as_uuid(attempt, "implementation_attempt_id"),
        interview_session_id=as_uuid(attempt, "interview_session_id"),
        client_user_id=as_uuid(attempt, "client_user_id"),
        dataset_manifest_digest=as_str(attempt, "dataset_manifest_digest"),
        authenticated_identity_ref=as_str(attempt, "authenticated_identity_ref"),
        acceptance_criteria_revision=as_int(attempt, "acceptance_criteria_revision"),
        workflow_ref=as_str(attempt, "workflow_ref"),
        evidence_window_id=(
            None
            if attempt["evidence_window_id"] is None
            else as_uuid(attempt, "evidence_window_id")
        ),
        frozen_result_digest=as_optional_str(attempt, "frozen_result_digest"),
        frozen_result=dict(frozen) if isinstance(frozen, dict) else None,
        criterion_proofs=[
            CriterionProofResponse(
                criterion_id=as_uuid(row, "criterion_id"),
                ordinal=as_int(row, "ordinal"),
                statement=as_str(row, "statement"),
                observability=CriterionObservability(as_str(row, "observability")),
                result=CriterionResult(as_str(row, "result")),
                user_confirmed=as_bool(row, "user_confirmed"),
                supporting_evidence_record_ids=list(row["evidence_record_ids"] or []),
                contradicting_evidence_record_ids=list(
                    row["contradicting_evidence_record_ids"] or []
                ),
                reason=as_str(row, "reason"),
            )
            for row in proofs
        ],
    )
