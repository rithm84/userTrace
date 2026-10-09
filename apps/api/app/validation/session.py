"""Same-user validation driven from the live Interview Session.

The voice agent's model runs validation through three tools, and
these are the operations they call. Every identity a step binds comes from
durable rows, never from the model's arguments, and the validation controller
rechecks each of them:

- ``start_session_validation`` binds the exact candidate the case's ready
  Implementation Attempt published and begins a Validation Attempt on it, or
  resumes the interrupted one against the same candidate.
- ``cite_session_observation`` promotes one observation the model proposes as
  evidence for one criterion, after checking that it was observed in this
  session, on the bound candidate allocation, since the attempt opened.
- ``finish_session_validation`` records the originating user's criterion-level
  answers as claims, freezes the evidence window, and completes the attempt. It
  refuses while the person confirmed a check that what the product showed
  contradicts: the conflict is theirs to settle, by running that check again or
  by answering it differently, before an outcome is recorded.

Contract: docs/arch-components/validation.md
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import (
    CriterionResult,
    EvidenceClaimState,
    EvidenceClass,
    ImplementationAttemptState,
    InterviewPhase,
    PreviewAllocationKind,
    PreviewVersionState,
    ValidationAttemptState,
)

from app.adapters.telemetry_evidence import TelemetryEvidenceQuery
from app.db.pool import Database
from app.db.rows import as_datetime, as_int, as_optional_str, as_str, as_uuid
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.evidence.observed_time import SESSION_OBSERVATIONS
from app.interview.evidence import record_claim
from app.interview.projection import advance_phase_on_connection
from app.outputs.closure_jobs import CASE_FILE_DEADLINE_SECONDS
from app.validation.completion import (
    CriterionConfirmation,
    ValidationCompletion,
    complete_validation,
    gather_assessments,
)
from app.validation.controller import (
    ValidationBinding,
    begin_validation,
    open_attempt_id,
    open_validation_attempt,
    resume_validation,
)
from app.validation.criteria import CriterionProof, assess_criterion
from app.validation.evidence import EvidenceStance
from app.validation.records import (
    CriterionRow,
    ValidationAttemptRow,
    load_attempt,
    load_criteria_on_connection,
)

__all__ = [
    "CriterionAnswer",
    "SpokenAnswer",
    "StartedValidation",
    "cite_session_observation",
    "finish_session_validation",
    "start_session_validation",
]

_EVIDENCE_PURPOSE = "validation_outcome"
_CITATION_PURPOSE = "validation_citation"
_BROWSER_OBSERVATION = "browser_observation"


class CriterionAnswer(StrEnum):
    """What the originating user said about one criterion."""

    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNSURE = "unsure"


@dataclass(frozen=True, slots=True)
class SpokenAnswer:
    """The originating user's answer to one criterion, in their own words."""

    ordinal: int
    answer: CriterionAnswer
    statement: str


@dataclass(frozen=True, slots=True)
class StartedValidation:
    """The in-progress attempt and the criteria the user works through."""

    validation_attempt_id: UUID
    criteria: tuple[CriterionRow, ...]


async def start_session_validation(
    database: Database, *, interview_session_id: UUID
) -> StartedValidation:
    """Begin validation of the ready candidate, or resume the interrupted attempt.

    An attempt already in progress is returned as it is, so a repeated call from
    the agent is harmless rather than a second attempt.
    """
    binding = await _binding(database, interview_session_id=interview_session_id)
    async with database.connection() as conn:
        existing = await open_attempt_id(conn, product_signal_id=binding.product_signal_id)
        attempt = None if existing is None else await load_attempt(conn, attempt_id=existing)
    if attempt is None:
        attempt_id = await open_validation_attempt(database, binding=binding)
        await begin_validation(
            database, attempt_id=attempt_id, originating_user_id=binding.client_user_id
        )
    elif attempt.state is ValidationAttemptState.PENDING:
        attempt_id = attempt.id
        await begin_validation(
            database, attempt_id=attempt_id, originating_user_id=binding.client_user_id
        )
    elif attempt.state is ValidationAttemptState.INTERRUPTED:
        attempt_id = attempt.id
        await resume_validation(
            database,
            attempt_id=attempt_id,
            originating_user_id=binding.client_user_id,
            preview_version_id=binding.preview_version_id,
        )
    else:
        attempt_id = attempt.id
    async with database.transaction() as conn:
        await advance_phase_on_connection(
            conn, interview_session_id=interview_session_id, phase=InterviewPhase.VALIDATION
        )
        criteria = await load_criteria_on_connection(
            conn,
            product_signal_id=binding.product_signal_id,
            criteria_revision=binding.acceptance_criteria_revision,
        )
    return StartedValidation(validation_attempt_id=attempt_id, criteria=criteria)


async def cite_session_observation(
    database: Database,
    evidence: TelemetryEvidenceQuery,
    *,
    interview_session_id: UUID,
    ordinal: int,
    observation_id: UUID,
    stance: EvidenceStance,
    summary: str,
) -> UUID:
    """Promote one proposed observation as evidence for one criterion.

    The model proposes; this checks the proposal against durable rows before
    anything is written, and refuses an observation from another session, the
    base preview, or before the attempt opened. When it happened is read on the
    server's clock (`app.evidence.observed_time`), so a participant's machine whose
    clock is off can neither refuse their evidence nor admit an earlier action.
    """
    attempt, criterion = await _in_progress(
        database, interview_session_id=interview_session_id, ordinal=ordinal
    )
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "with "
            + SESSION_OBSERVATIONS
            + """
            select observations.payload ->> 'milestone_id' as milestone,
                   observations.observed_at
            from session_observations as observations
            where observations.id = %(observation)s
              and observations.preview_runtime_allocation_id = %(allocation)s
              and observations.observed_at >= %(opened)s
            """,
            {
                "client": attempt.client_id,
                "session": attempt.interview_session_id,
                "observation": observation_id,
                "allocation": attempt.preview_runtime_allocation_id,
                "opened": attempt.opened_at,
            },
        )
        observed = await cur.fetchone()
    if observed is None:
        raise GuardRejectedError(
            machine="validation_attempt",
            trigger="cite",
            reason=(
                f"observation {observation_id} was not observed in this session on the "
                "bound candidate since the Validation Attempt opened"
            ),
        )
    window = await evidence.open_window(
        client_id=attempt.client_id,
        product_signal_id=attempt.product_signal_id,
        interview_session_id=attempt.interview_session_id,
        purpose=_CITATION_PURPOSE,
    )
    return await evidence.promote_observation(
        client_id=attempt.client_id,
        product_signal_id=attempt.product_signal_id,
        interview_session_id=attempt.interview_session_id,
        telemetry_window_id=window.evidence_window_id,
        source_class=_BROWSER_OBSERVATION,
        source_record_id=observation_id,
        criterion_id=criterion.id,
        stance=stance,
        summary=summary,
        workflow_ref=attempt.workflow_ref,
        milestone=as_optional_str(observed, "milestone"),
        preview_version_id=attempt.preview_version_id,
        coverage_limit=None,
        observed_at=as_datetime(observed, "observed_at"),
    )


async def finish_session_validation(
    database: Database,
    evidence: TelemetryEvidenceQuery,
    *,
    interview_session_id: UUID,
    answers: Sequence[SpokenAnswer],
    source_ref: str,
    context_revision: int,
) -> ValidationCompletion:
    """Record the user's answers, freeze the evidence window, and complete.

    A criterion the user gave no answer for is completed as unconfirmed, never
    as met. Whether the agreed workflow was observed at all is read from the
    session's own observations on the candidate, not taken from the model.
    """
    attempt = await _current_attempt(database, interview_session_id=interview_session_id)
    async with database.transaction() as conn:
        criteria = {
            criterion.ordinal: criterion
            for criterion in await load_criteria_on_connection(
                conn,
                product_signal_id=attempt.product_signal_id,
                criteria_revision=attempt.acceptance_criteria_revision,
            )
        }
        unknown = sorted({answer.ordinal for answer in answers} - set(criteria))
        if unknown:
            raise GuardRejectedError(
                machine="validation_attempt",
                trigger="complete",
                reason=f"answers name criteria that do not exist: ordinals {unknown}",
            )
        workflow_observed = await _candidate_observed(conn, attempt)
        confirmations = [
            await _record_answer(
                conn,
                attempt=attempt,
                criterion=criteria[answer.ordinal],
                answer=answer,
                source_ref=source_ref,
                context_revision=context_revision,
                workflow_observed=workflow_observed,
            )
            for answer in answers
        ]
    window = await evidence.freeze_window(
        client_id=attempt.client_id,
        product_signal_id=attempt.product_signal_id,
        interview_session_id=attempt.interview_session_id,
        purpose=_EVIDENCE_PURPOSE,
        starts_at=attempt.opened_at,
        ends_at=await database.now(),
    )
    async with database.connection() as conn:
        conflicts = await _confirmed_but_contradicted(
            conn,
            evidence,
            attempt=attempt,
            evidence_window_id=window.evidence_window_id,
            confirmations=confirmations,
        )
    if conflicts:
        raise GuardRejectedError(
            machine="validation_attempt",
            trigger="complete",
            reason=(
                "the person confirmed checks that what the preview showed contradicts, "
                "so no outcome is recorded yet. "
                + " ".join(
                    f"Check {proof.ordinal + 1}: {proof.reason}." for proof in conflicts
                )
                + " Tell them what the preview showed and ask them to run that check "
                "again; if they will not, record their answer for it as unsure."
            ),
        )
    return await complete_validation(
        database,
        evidence,
        attempt_id=attempt.id,
        evidence_window_id=window.evidence_window_id,
        confirmations=confirmations,
        assembly_deadline_seconds=CASE_FILE_DEADLINE_SECONDS,
    )


async def _confirmed_but_contradicted(
    conn: AsyncConnection[DictRow],
    evidence: TelemetryEvidenceQuery,
    *,
    attempt: ValidationAttemptRow,
    evidence_window_id: UUID,
    confirmations: Sequence[CriterionConfirmation],
) -> tuple[CriterionProof, ...]:
    """Checks the person confirmed while the newest observed run of them contradicts it."""
    assessments = await gather_assessments(
        conn,
        evidence,
        attempt=attempt,
        evidence_window_id=evidence_window_id,
        confirmations=confirmations,
    )
    proofs = (assess_criterion(assessment) for assessment in assessments)
    return tuple(
        proof
        for proof in proofs
        if proof.user_confirmed and proof.result is CriterionResult.CONTRADICTED
    )


async def _record_answer(
    conn: AsyncConnection[DictRow],
    *,
    attempt: ValidationAttemptRow,
    criterion: CriterionRow,
    answer: SpokenAnswer,
    source_ref: str,
    context_revision: int,
    workflow_observed: bool,
) -> CriterionConfirmation:
    """Append the user's answer as a claim and return the confirmation citing it."""
    record_id = await record_claim(
        conn,
        client_id=attempt.client_id,
        product_signal_id=attempt.product_signal_id,
        interview_session_id=attempt.interview_session_id,
        evidence_class=EvidenceClass.TRANSCRIPT_FRAGMENT,
        claim_state=_CLAIM_STATES[answer.answer],
        source_ref=source_ref,
        context_revision=context_revision,
        body={
            "kind": "validation_answer",
            "criterion_id": str(criterion.id),
            "validation_attempt_id": str(attempt.id),
            "answer": answer.answer.value,
            "statement": answer.statement,
        },
        supersedes_id=None,
    )
    return CriterionConfirmation(
        criterion_id=criterion.id,
        user_confirmed=answer.answer is CriterionAnswer.CONFIRMED,
        user_rejected=answer.answer is CriterionAnswer.REJECTED,
        confirmation_evidence_record_id=record_id,
        workflow_observed=workflow_observed,
    )


_CLAIM_STATES = {
    CriterionAnswer.CONFIRMED: EvidenceClaimState.CONFIRMED,
    CriterionAnswer.REJECTED: EvidenceClaimState.CONTRADICTED,
    CriterionAnswer.UNSURE: EvidenceClaimState.PROPOSED,
}


async def _candidate_observed(
    conn: AsyncConnection[DictRow], attempt: ValidationAttemptRow
) -> bool:
    """Whether the session observed anything on the candidate since the attempt opened."""
    async with conn.cursor() as cur:
        await cur.execute(
            "with "
            + SESSION_OBSERVATIONS
            + """
            select exists (
              select 1 from session_observations as observations
              where observations.preview_runtime_allocation_id = %(allocation)s
                and observations.observed_at >= %(opened)s
            ) as observed
            """,
            {
                "client": attempt.client_id,
                "session": attempt.interview_session_id,
                "allocation": attempt.preview_runtime_allocation_id,
                "opened": attempt.opened_at,
            },
        )
        row = await cur.fetchone()
    return row is not None and bool(row["observed"])


async def _in_progress(
    database: Database, *, interview_session_id: UUID, ordinal: int
) -> tuple[ValidationAttemptRow, CriterionRow]:
    """Return the session's in-progress attempt and one of its criteria."""
    attempt = await _current_attempt(database, interview_session_id=interview_session_id)
    if attempt.state is not ValidationAttemptState.IN_PROGRESS:
        raise GuardRejectedError(
            machine="validation_attempt",
            trigger="cite",
            reason=f"evidence is cited only while validation is in progress, not '{attempt.state}'",
        )
    async with database.connection() as conn:
        criteria = await load_criteria_on_connection(
            conn,
            product_signal_id=attempt.product_signal_id,
            criteria_revision=attempt.acceptance_criteria_revision,
        )
    criterion = next((item for item in criteria if item.ordinal == ordinal), None)
    if criterion is None:
        raise GuardRejectedError(
            machine="validation_attempt",
            trigger="cite",
            reason=f"criteria revision {attempt.acceptance_criteria_revision} has no ordinal {ordinal}",
        )
    return attempt, criterion


async def _current_attempt(
    database: Database, *, interview_session_id: UUID
) -> ValidationAttemptRow:
    """Return the open Validation Attempt of the session's Product Signal."""
    async with database.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select product_signal_id from public.interview_sessions where id = %s",
                (interview_session_id,),
            )
            row = await cur.fetchone()
        if row is None:
            raise AggregateNotFoundError(table="interview_sessions", row_id=interview_session_id)
        attempt_id = await open_attempt_id(
            conn, product_signal_id=as_uuid(row, "product_signal_id")
        )
        if attempt_id is None:
            raise GuardRejectedError(
                machine="validation_attempt",
                trigger="complete",
                reason="the Product Signal has no open Validation Attempt; begin validation first",
            )
        attempt = await load_attempt(conn, attempt_id=attempt_id)
    if attempt.interview_session_id != interview_session_id:
        raise GuardRejectedError(
            machine="validation_attempt",
            trigger="complete",
            reason="the open Validation Attempt is bound to a different Interview Session",
        )
    return attempt


async def _binding(database: Database, *, interview_session_id: UUID) -> ValidationBinding:
    """Resolve the exact candidate tuple from the case's durable rows.

    Each lookup names the one row the case has in that role now, the newest ready
    attempt's candidate when the person asked for changes; the controller then
    rechecks the whole tuple against those rows when the attempt opens.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select sessions.client_id, sessions.product_signal_id, sessions.client_user_id,
                   signals.acceptance_criteria_revision, agreements.agreed_scope,
                   implementations.id as implementation_attempt_id,
                   versions.id as preview_version_id,
                   versions.seed_manifest_digest,
                   allocations.id as preview_runtime_allocation_id,
                   allocations.authenticated_identity
            from public.interview_sessions as sessions
            join public.product_signals as signals on signals.id = sessions.product_signal_id
            left join public.acceptance_criteria_agreements as agreements
              on agreements.product_signal_id = signals.id
             and agreements.acceptance_criteria_revision = signals.acceptance_criteria_revision
            left join lateral (
              select attempts.id from public.implementation_attempts as attempts
              where attempts.product_signal_id = signals.id and attempts.state = %s
              order by attempts.created_at desc
              limit 1
            ) as implementations on true
            left join public.preview_versions as versions
              on versions.implementation_attempt_id = implementations.id
             and versions.state = %s
            left join public.preview_runtime_allocations as allocations
              on allocations.preview_version_id = versions.id
             and allocations.allocation_kind = %s
             and allocations.state in ('ready', 'bound')
            where sessions.id = %s
            """,
            (
                ImplementationAttemptState.CANDIDATE_READY.value,
                PreviewVersionState.READY.value,
                PreviewAllocationKind.CANDIDATE.value,
                interview_session_id,
            ),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="interview_sessions", row_id=interview_session_id)
    missing = [
        role
        for role, column in (
            ("an agreed scope", "agreed_scope"),
            ("a ready Implementation Attempt", "implementation_attempt_id"),
            ("a ready Preview Version", "preview_version_id"),
            ("a ready candidate allocation", "preview_runtime_allocation_id"),
        )
        if row[column] is None
    ]
    if missing:
        raise GuardRejectedError(
            machine="validation_attempt",
            trigger="bind",
            reason="validation needs " + ", ".join(missing) + "; the case has none yet",
        )
    scope = row["agreed_scope"]
    workflow_ref = scope.get("workflow_ref") if isinstance(scope, dict) else None
    if not isinstance(workflow_ref, str):
        raise GuardRejectedError(
            machine="validation_attempt",
            trigger="bind",
            reason="the agreed scope names no workflow for validation to observe",
        )
    return ValidationBinding(
        client_id=as_uuid(row, "client_id"),
        product_signal_id=as_uuid(row, "product_signal_id"),
        client_user_id=as_uuid(row, "client_user_id"),
        implementation_attempt_id=as_uuid(row, "implementation_attempt_id"),
        preview_version_id=as_uuid(row, "preview_version_id"),
        preview_runtime_allocation_id=as_uuid(row, "preview_runtime_allocation_id"),
        interview_session_id=interview_session_id,
        dataset_manifest_digest=as_str(row, "seed_manifest_digest"),
        authenticated_identity_ref=as_str(row, "authenticated_identity"),
        acceptance_criteria_revision=as_int(row, "acceptance_criteria_revision"),
        workflow_ref=workflow_ref,
    )
