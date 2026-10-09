"""The Validation Attempt controller.

Each attempt binds one Product Signal, originating user, Implementation Attempt,
immutable Preview Version, candidate runtime allocation, dataset manifest and its
hashes, authenticated product identity, acceptance-criteria revision, workflow,
and Interview Session, exactly as the
[validation binding](../../../../docs/arch-components/validation.md#validation-binding)
requires. The binding is checked against durable rows when the attempt opens, so
a later transition cannot be talked into a different candidate.

Interruption keeps the attempt; it is never a verdict. The first interruption or
departure records the case-level return-and-closure deadline, and a later one
never extends or resets it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    ImplementationAttemptState,
    PreviewAllocationKind,
    PreviewRuntimeAllocationState,
    PreviewVersionState,
    ProductSignalState,
    ValidationAttemptState,
)

from app.db.kernel import append_fact, apply_transition_on_connection
from app.db.pool import Database
from app.db.rows import as_str, as_uuid
from app.envelope import (
    TRANSIENT_CATEGORIES,
    Actor,
    AuditIntent,
    EventAppend,
    JobIntent,
    JsonValue,
    StreamRef,
)
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.ids import uuid7
from app.ingestion.readiness import open_page_readiness
from app.lifecycle.machine import plan_transition
from app.lifecycle.product_signal import PRODUCT_SIGNAL_MACHINE, ProductSignalFacts
from app.lifecycle.validation import VALIDATION_ATTEMPT_MACHINE, ValidationAttemptFacts
from app.logging import get_logger
from app.validation.records import (
    CaseBudgets,
    ValidationAttemptRow,
    load_attempt,
    load_budgets,
    load_criteria_on_connection,
    session_is_bound,
)

__all__ = [
    "EXPIRE_VALIDATION_RETURN",
    "ValidationBinding",
    "abandon_validation_attempt",
    "begin_validation",
    "interrupt_validation",
    "open_attempt_id",
    "open_validation_attempt",
    "record_departure",
    "resume_validation",
]

_log = get_logger(__name__)

EXPIRE_VALIDATION_RETURN = "validation.expire_return"
"""Job type that closes a case whose return-and-closure deadline has passed."""

_AGENT_ACTOR = Actor(kind=ActorKind.SERVICE, ref="validation.controller")


@dataclass(frozen=True, slots=True)
class ValidationBinding:
    """The exact tuple one Validation Attempt is bound to."""

    client_id: UUID
    product_signal_id: UUID
    client_user_id: UUID
    implementation_attempt_id: UUID
    preview_version_id: UUID
    preview_runtime_allocation_id: UUID
    interview_session_id: UUID
    dataset_manifest_digest: str
    authenticated_identity_ref: str
    acceptance_criteria_revision: int
    workflow_ref: str


def _refuse(trigger: str, reason: str) -> GuardRejectedError:
    return GuardRejectedError(
        machine=VALIDATION_ATTEMPT_MACHINE.name, trigger=trigger, reason=reason
    )


async def _check_binding(
    conn: AsyncConnection[DictRow], binding: ValidationBinding
) -> None:
    """Refuse a binding that any durable row contradicts."""
    budgets = await load_budgets(conn, product_signal_id=binding.product_signal_id)
    if budgets.originating_client_user_id != binding.client_user_id:
        raise _refuse(
            "bind", "only the immutable originating user may validate this Product Signal"
        )
    if budgets.state != ProductSignalState.AWAITING_VALIDATION.value:
        raise _refuse(
            "bind",
            "a Validation Attempt opens only while the case awaits validation; "
            f"the Product Signal is in state '{budgets.state}'",
        )
    if budgets.acceptance_criteria_revision != binding.acceptance_criteria_revision:
        raise _refuse(
            "bind",
            "the binding names acceptance-criteria revision "
            f"{binding.acceptance_criteria_revision}; the case pins "
            f"{budgets.acceptance_criteria_revision}",
        )

    criteria = await load_criteria_on_connection(
        conn,
        product_signal_id=binding.product_signal_id,
        criteria_revision=binding.acceptance_criteria_revision,
    )
    if not criteria:
        raise _refuse(
            "bind",
            "criteria revision "
            f"{binding.acceptance_criteria_revision} has no frozen acceptance criteria",
        )

    async with conn.cursor() as cur:
        await cur.execute(
            """
            select versions.state as version_state,
                   versions.product_signal_id as version_signal,
                   versions.implementation_attempt_id as version_attempt,
                   versions.seed_manifest_digest as version_seed,
                   implementations.state as implementation_state,
                   implementations.product_signal_id as implementation_signal
            from public.preview_versions as versions
            join public.implementation_attempts as implementations
              on implementations.id = versions.implementation_attempt_id
            where versions.id = %s
            """,
            (binding.preview_version_id,),
        )
        candidate = await cur.fetchone()
    if candidate is None:
        raise AggregateNotFoundError(
            table="preview_versions", row_id=binding.preview_version_id
        )
    if as_str(candidate, "version_state") != PreviewVersionState.READY.value:
        raise _refuse(
            "bind",
            "criterion-level validation requires the exact ready candidate; the "
            f"Preview Version is '{as_str(candidate, 'version_state')}'",
        )
    if as_uuid(candidate, "version_signal") != binding.product_signal_id:
        raise _refuse("bind", "the Preview Version belongs to a different Product Signal")
    if as_uuid(candidate, "version_attempt") != binding.implementation_attempt_id:
        raise _refuse(
            "bind", "the Preview Version was not built by the named Implementation Attempt"
        )
    if (
        as_str(candidate, "implementation_state")
        != ImplementationAttemptState.CANDIDATE_READY.value
    ):
        raise _refuse(
            "bind",
            "the Implementation Attempt has not published a ready candidate; it is "
            f"'{as_str(candidate, 'implementation_state')}'",
        )
    if as_uuid(candidate, "implementation_signal") != binding.product_signal_id:
        raise _refuse(
            "bind", "the Implementation Attempt belongs to a different Product Signal"
        )
    if candidate["version_seed"] != binding.dataset_manifest_digest:
        raise _refuse(
            "bind",
            "the binding's dataset manifest digest does not match the candidate build",
        )

    async with conn.cursor() as cur:
        await cur.execute(
            """
            select allocation_kind, state, product_signal_id, preview_version_id,
                   seed_manifest_digest
            from public.preview_runtime_allocations
            where id = %s
            """,
            (binding.preview_runtime_allocation_id,),
        )
        allocation = await cur.fetchone()
    if allocation is None:
        raise AggregateNotFoundError(
            table="preview_runtime_allocations",
            row_id=binding.preview_runtime_allocation_id,
        )
    if as_str(allocation, "allocation_kind") != PreviewAllocationKind.CANDIDATE.value:
        raise _refuse("bind", "validation runs against a candidate allocation, not the base")
    if as_str(allocation, "state") not in {
        PreviewRuntimeAllocationState.READY.value,
        PreviewRuntimeAllocationState.BOUND.value,
    }:
        raise _refuse(
            "bind",
            "the candidate allocation is not ready; it is "
            f"'{as_str(allocation, 'state')}'",
        )
    if as_uuid(allocation, "product_signal_id") != binding.product_signal_id:
        raise _refuse(
            "bind", "the candidate allocation belongs to a different Product Signal"
        )
    if allocation["preview_version_id"] != binding.preview_version_id:
        raise _refuse(
            "bind", "the candidate allocation does not serve the named Preview Version"
        )
    if allocation["seed_manifest_digest"] != binding.dataset_manifest_digest:
        raise _refuse(
            "bind",
            "the candidate allocation was seeded from a different dataset manifest",
        )

    if not await session_is_bound(
        conn,
        interview_session_id=binding.interview_session_id,
        product_signal_id=binding.product_signal_id,
        client_user_id=binding.client_user_id,
    ):
        raise _refuse(
            "bind",
            "the Interview Session is not this case's session for the originating user",
        )


async def open_validation_attempt(
    database: Database, *, binding: ValidationBinding
) -> UUID:
    """Open one pending Validation Attempt bound to the exact candidate."""
    attempt_id = uuid7()
    async with database.transaction() as conn:
        await _check_binding(conn, binding)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.validation_attempts (
                    id, client_id, product_signal_id, implementation_attempt_id,
                    preview_version_id, preview_runtime_allocation_id,
                    interview_session_id, client_user_id, state,
                    dataset_manifest_digest, acceptance_criteria_revision,
                    authenticated_identity_ref, workflow_ref
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    attempt_id,
                    binding.client_id,
                    binding.product_signal_id,
                    binding.implementation_attempt_id,
                    binding.preview_version_id,
                    binding.preview_runtime_allocation_id,
                    binding.interview_session_id,
                    binding.client_user_id,
                    ValidationAttemptState.PENDING.value,
                    binding.dataset_manifest_digest,
                    binding.acceptance_criteria_revision,
                    binding.authenticated_identity_ref,
                    binding.workflow_ref,
                ),
            )
    _log.info(
        "validation_attempt_opened",
        client_id=str(binding.client_id),
        product_signal_id=str(binding.product_signal_id),
        validation_attempt_id=str(attempt_id),
        preview_version_id=str(binding.preview_version_id),
        acceptance_criteria_revision=binding.acceptance_criteria_revision,
    )
    return attempt_id


def _facts(
    attempt: ValidationAttemptRow,
    *,
    originating_user_matches: bool,
    candidate_ready: bool,
    observation_ready: bool,
    same_candidate: bool,
) -> ValidationAttemptFacts:
    """Build the machine facts for a non-completing transition."""
    return ValidationAttemptFacts(
        originating_user_matches=originating_user_matches,
        candidate_ready=candidate_ready,
        observation_ready=observation_ready,
        same_candidate=same_candidate,
        outcome=attempt.outcome,
        criteria_total=0,
        criteria_affirmatively_proven=0,
        user_confirmed_every_criterion=False,
        unresolved_contradiction=False,
    )


async def _readiness(
    conn: AsyncConnection[DictRow], attempt: ValidationAttemptRow
) -> tuple[bool, bool]:
    """Return whether the exact candidate and the observation path on it are ready.

    Observation counts only for the page the participant has open, and only when
    that page is on the candidate's own preview: while the previous version's page
    is still open, or the candidate's page has not yet been evaluated, readiness
    the session kept from the page before is not readiness of the candidate.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select versions.state as version_state,
                   allocations.state as allocation_state,
                   allocations.preview_version_id as allocation_version,
                   coalesce(readiness.observation_ready, false) as observation_ready
            from public.validation_attempts as attempts
            join public.preview_versions as versions
              on versions.id = attempts.preview_version_id
            join public.preview_runtime_allocations as allocations
              on allocations.id = attempts.preview_runtime_allocation_id
            left join public.session_readiness as readiness
              on readiness.interview_session_id = attempts.interview_session_id
            where attempts.id = %s
            """,
            (attempt.id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="validation_attempts", row_id=attempt.id)
    candidate_ready = (
        as_str(row, "version_state") == PreviewVersionState.READY.value
        and as_str(row, "allocation_state")
        in {
            PreviewRuntimeAllocationState.READY.value,
            PreviewRuntimeAllocationState.BOUND.value,
        }
        and row["allocation_version"] == attempt.preview_version_id
    )
    page = await open_page_readiness(
        conn,
        client_id=attempt.client_id,
        interview_session_id=attempt.interview_session_id,
        observed_at=datetime.now(tz=UTC),
    )
    observation_ready = (
        bool(row["observation_ready"])
        and page.preview_runtime_allocation_id == attempt.preview_runtime_allocation_id
        and page.ready
    )
    return candidate_ready, observation_ready


async def _transition(
    conn: AsyncConnection[DictRow],
    attempt: ValidationAttemptRow,
    *,
    trigger: str,
    facts: ValidationAttemptFacts,
    mutation: Mapping[str, object],
    event_type: str,
    payload: Mapping[str, JsonValue],
    reason_code: str | None,
    jobs: Sequence[JobIntent],
) -> str:
    """Apply one Validation Attempt transition inside the caller's transaction."""
    stream = StreamRef(kind=AggregateKind.VALIDATION_ATTEMPT, id=attempt.id)
    request = plan_transition(
        VALIDATION_ATTEMPT_MACHINE,
        facts,
        trigger=trigger,
        current_state=attempt.state.value,
        client_id=attempt.client_id,
        aggregate_kind=AggregateKind.VALIDATION_ATTEMPT,
        aggregate_id=attempt.id,
        expected_revision=attempt.revision,
        state_column="state",
        mutation=dict(mutation),  # type: ignore[arg-type]
        event=EventAppend(
            message_id=uuid7(),
            stream=stream,
            event_type=event_type,
            payload=payload,
            actor=_AGENT_ACTOR,
            causation_id=None,
            correlation_id=attempt.product_signal_id,
            occurred_at=datetime.now(tz=UTC),
        ),
        jobs=jobs,
        commands=(),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=stream,
            action=f"validation_{trigger}",
            outcome=trigger,
            reason_code=reason_code,
            actor=_AGENT_ACTOR,
            pinned_configuration_release_id=None,
            detail=payload,
        ),
        additional_locks=(),
    )
    outcome = await apply_transition_on_connection(conn, request)
    return outcome.to_state


async def begin_validation(
    database: Database, *, attempt_id: UUID, originating_user_id: UUID
) -> None:
    """Start criterion-level validation, moving the case into ``validating``.

    ``originating_user_id`` is the identity the session authenticated, checked
    against the attempt's own binding rather than trusted.
    """
    async with database.transaction() as conn:
        attempt = await load_attempt(conn, attempt_id=attempt_id)
        candidate_ready, observation_ready = await _readiness(conn, attempt)
        await _transition(
            conn,
            attempt,
            trigger="begin",
            facts=_facts(
                attempt,
                originating_user_matches=originating_user_id == attempt.client_user_id,
                candidate_ready=candidate_ready,
                observation_ready=observation_ready,
                same_candidate=True,
            ),
            mutation={},
            event_type="validation_attempt_began",
            payload={"preview_version_id": str(attempt.preview_version_id)},
            reason_code=None,
            jobs=(),
        )
        budgets = await load_budgets(conn, product_signal_id=attempt.product_signal_id)
        if budgets.state == ProductSignalState.AWAITING_VALIDATION.value:
            await _advance_signal_to_validating(conn, attempt, budgets_revision=budgets.revision)


async def _advance_signal_to_validating(
    conn: AsyncConnection[DictRow], attempt: ValidationAttemptRow, *, budgets_revision: int
) -> None:
    """Move the Product Signal from awaiting_validation into validating."""
    stream = StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=attempt.product_signal_id)
    request = plan_transition(
        PRODUCT_SIGNAL_MACHINE,
        ProductSignalFacts(
            participation_occurred=True,
            case_file_published=False,
        ),
        trigger="participant_ready",
        current_state=ProductSignalState.AWAITING_VALIDATION.value,
        client_id=attempt.client_id,
        aggregate_kind=AggregateKind.PRODUCT_SIGNAL,
        aggregate_id=attempt.product_signal_id,
        expected_revision=budgets_revision,
        state_column="state",
        mutation={},
        event=EventAppend(
            message_id=uuid7(),
            stream=stream,
            event_type="validation_participant_ready",
            payload={"validation_attempt_id": str(attempt.id)},
            actor=_AGENT_ACTOR,
            causation_id=None,
            correlation_id=attempt.product_signal_id,
            occurred_at=datetime.now(tz=UTC),
        ),
        jobs=(),
        commands=(),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=stream,
            action="validation_participant_ready",
            outcome="validating",
            reason_code=None,
            actor=_AGENT_ACTOR,
            pinned_configuration_release_id=None,
            detail={"validation_attempt_id": str(attempt.id)},
        ),
        additional_locks=(),
    )
    await apply_transition_on_connection(conn, request)


async def interrupt_validation(
    database: Database,
    *,
    attempt_id: UUID,
    reason: str,
    return_window_seconds: int,
) -> datetime:
    """Record an interruption and the case-level return-and-closure deadline.

    Returns the deadline in force. A first interruption records it; a repeated
    one reads the existing deadline back and never extends or resets it.
    """
    async with database.transaction() as conn:
        return await _interrupt_on_connection(
            conn, attempt_id=attempt_id, reason=reason, return_window_seconds=return_window_seconds
        )


async def record_departure(
    conn: AsyncConnection[DictRow],
    *,
    product_signal_id: UUID,
    reason: str,
    return_window_seconds: int,
) -> datetime:
    """Record the originating user's departure inside the caller's transaction.

    An in-progress Validation Attempt is interrupted; otherwise only the case's
    return-and-closure deadline is recorded. Either way the first departure
    commits the Job that closes the case at that deadline, and a later one never
    extends it.
    """
    attempt_id = await open_attempt_id(conn, product_signal_id=product_signal_id)
    if attempt_id is not None:
        attempt = await load_attempt(conn, attempt_id=attempt_id)
        if attempt.state is ValidationAttemptState.IN_PROGRESS:
            return await _interrupt_on_connection(
                conn,
                attempt_id=attempt_id,
                reason=reason,
                return_window_seconds=return_window_seconds,
            )
    budgets = await load_budgets(conn, product_signal_id=product_signal_id)
    deadline, jobs = await _record_return_deadline(
        conn, budgets=budgets, return_window_seconds=return_window_seconds
    )
    if jobs:
        stream = StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=product_signal_id)
        await append_fact(
            conn,
            client_id=budgets.client_id,
            event=EventAppend(
                message_id=uuid7(),
                stream=stream,
                event_type="case_return_deadline_recorded",
                payload={"reason": reason, "return_deadline_at": deadline.isoformat()},
                actor=_AGENT_ACTOR,
                causation_id=None,
                correlation_id=product_signal_id,
                occurred_at=datetime.now(tz=UTC),
            ),
            audit=AuditIntent(
                audit_id=uuid7(),
                subject=stream,
                action="case_return_deadline_recorded",
                outcome="deadline_recorded",
                reason_code=reason,
                actor=_AGENT_ACTOR,
                pinned_configuration_release_id=None,
                detail={"return_deadline_at": deadline.isoformat()},
            ),
            jobs=jobs,
        )
    return deadline


async def _interrupt_on_connection(
    conn: AsyncConnection[DictRow],
    *,
    attempt_id: UUID,
    reason: str,
    return_window_seconds: int,
) -> datetime:
    """Interrupt one attempt and record the case's deadline, in one transaction."""
    attempt = await load_attempt(conn, attempt_id=attempt_id)
    budgets = await load_budgets(conn, product_signal_id=attempt.product_signal_id)
    deadline, jobs = await _record_return_deadline(
        conn, budgets=budgets, return_window_seconds=return_window_seconds
    )
    await _transition(
        conn,
        attempt,
        trigger="interrupt",
        facts=_facts(
            attempt,
            originating_user_matches=True,
            candidate_ready=True,
            observation_ready=True,
            same_candidate=True,
        ),
        mutation={"interrupted_at": datetime.now(tz=UTC)},
        event_type="validation_attempt_interrupted",
        payload={"reason": reason, "return_deadline_at": deadline.isoformat()},
        reason_code=reason,
        jobs=jobs,
    )
    return deadline


async def _record_return_deadline(
    conn: AsyncConnection[DictRow], *, budgets: CaseBudgets, return_window_seconds: int
) -> tuple[datetime, tuple[JobIntent, ...]]:
    """Record the case's deadline once and return the expiry Job the caller commits.

    A case that already has a deadline keeps it and obliges no second Job.
    """
    if budgets.return_deadline_at is not None:
        return budgets.return_deadline_at, ()
    deadline = datetime.now(tz=UTC) + timedelta(seconds=return_window_seconds)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.product_signals
            set return_deadline_at = %s, updated_at = now()
            where id = %s and return_deadline_at is null
            """,
            (deadline, budgets.product_signal_id),
        )
    return deadline, (
        JobIntent(
            job_id=uuid7(),
            job_type=EXPIRE_VALIDATION_RETURN,
            input={
                "product_signal_id": str(budgets.product_signal_id),
                "reason": "validation_return_deadline_expired",
            },
            deadline_at=deadline + timedelta(hours=24),
            timeout_seconds=300,
            max_runs=5,
            resource_budget={"kind": "closure"},
            retryable_categories=TRANSIENT_CATEGORIES,
            backoff_base_seconds=30,
            backoff_max_seconds=900,
            concurrency_key=f"close_case:{budgets.product_signal_id}",
            cancellation_scope_id=None,
            not_before=deadline,
        ),
    )


async def resume_validation(
    database: Database,
    *,
    attempt_id: UUID,
    originating_user_id: UUID,
    preview_version_id: UUID,
) -> None:
    """Resume an interrupted attempt against the same exact candidate."""
    async with database.transaction() as conn:
        attempt = await load_attempt(conn, attempt_id=attempt_id)
        candidate_ready, observation_ready = await _readiness(conn, attempt)
        await _transition(
            conn,
            attempt,
            trigger="resume",
            facts=_facts(
                attempt,
                originating_user_matches=originating_user_id == attempt.client_user_id,
                candidate_ready=candidate_ready,
                observation_ready=observation_ready,
                same_candidate=preview_version_id == attempt.preview_version_id,
            ),
            mutation={},
            event_type="validation_attempt_resumed",
            payload={"preview_version_id": str(preview_version_id)},
            reason_code=None,
            jobs=(),
        )


async def abandon_validation_attempt(
    conn: AsyncConnection[DictRow], *, attempt_id: UUID, reason: str
) -> None:
    """Abandon an open attempt without recording a verdict."""
    attempt = await load_attempt(conn, attempt_id=attempt_id)
    await _transition(
        conn,
        attempt,
        trigger="abandon",
        facts=_facts(
            attempt,
            originating_user_matches=True,
            candidate_ready=False,
            observation_ready=False,
            same_candidate=True,
        ),
        mutation={},
        event_type="validation_attempt_abandoned",
        payload={"reason": reason},
        reason_code=reason,
        jobs=(),
    )


async def open_attempt_id(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID
) -> UUID | None:
    """Return the case's one open Validation Attempt, if it has one."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.validation_attempts
            where product_signal_id = %s
              and state in ('pending', 'in_progress', 'interrupted')
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    return None if row is None else as_uuid(row, "id")
