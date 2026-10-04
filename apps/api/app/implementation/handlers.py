"""Loading an attempt specification, and the Job handler that runs one attempt.

`load_attempt_specification` rebuilds the typed specification from durable rows
alone: the attempt, its frozen gate decision and the three scope columns, the
pinned policy on the Product Signal's configuration release, and the attempt's
current active Environment Lease with its fencing token. A worker that cannot
build it does not start, and a run never infers a scope from anything else.

`run_implementation_attempt` is the registered Job handler. It grants the run a
fresh fenced lease, drives the attempt to its typed outcome in a Vercel Sandbox
workspace with Codex, hands an accepted candidate to the preview
area for the live Interview Session, and commits the outcome: a candidate moves
the Product Signal to `awaiting_validation`; a terminal outcome without one commits
the blocker Case File and the Outreach Offer entitlement evaluation it obliges.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    CaseFileVariant,
    ImplementationAttemptState,
)

from app.connectors.github_delivery import github_app_from_environment
from app.connectors.openai_roles import OpenAIRole, openai_api_key, openai_configuration_for
from app.connectors.vercel_sandboxes import VercelSandboxes, sandbox_grant_from_environment
from app.db.kernel import append_fact
from app.db.pool import Database
from app.db.rows import as_int, as_optional_str, as_str, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, StreamRef
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.execution.jobs import ClaimedJob
from app.gate.policy import ImplementationPolicy, read_implementation_policy
from app.gate.scope import ApprovedScope
from app.ids import uuid7
from app.implementation.agent_author import AgentAuthor
from app.implementation.artifact_store import ArtifactStore
from app.implementation.attempt import AttemptRunner, AttemptRunResult, WorkspaceDependencies
from app.implementation.budgets import BudgetLedger
from app.implementation.environment import WORKSPACE_ROOT
from app.implementation.specification import (
    AttemptSpecification,
    CodeFindingHint,
    LeaseBinding,
    RepositoryCheckout,
)
from app.implementation.state import advance_attempt, current_attempt
from app.lifecycle.attempts import grant_environment_lease
from app.lifecycle.implementation import ImplementationAttemptFacts
from app.lifecycle.product_signal import ProductSignalFacts
from app.lifecycle.signal_transitions import advance_product_signal
from app.logging import get_logger
from app.offers.entitlement import CaseStopReason
from app.outputs.closure_jobs import (
    CASE_FILE_DEADLINE_SECONDS,
    case_file_assembly_job,
    entitlement_job,
)
from app.preview.candidate_intake import PreviewCandidateDeployer
from app.preview.jobs import preview_session
from app.settings import get_settings

__all__ = [
    "RUN_IMPLEMENTATION_ATTEMPT",
    "load_attempt_specification",
    "run_implementation_attempt",
]

_log = get_logger(__name__)

RUN_IMPLEMENTATION_ATTEMPT = "implementation.run_attempt"
_ACTOR: Final[Actor] = Actor(kind=ActorKind.SERVICE, ref="implementation.controller")
_GITHUB_TIMEOUT_SECONDS: Final[float] = 30.0

_ENTRY_PATHS: Final[Mapping[ImplementationAttemptState, tuple[tuple[str, ...], str | None]]] = {
    ImplementationAttemptState.AUTHORIZED: (("queue", "acquire_environment"), "begin_execution"),
    ImplementationAttemptState.QUEUED: (("acquire_environment",), "begin_execution"),
    ImplementationAttemptState.ACQUIRING_ENVIRONMENT: ((), "begin_execution"),
    ImplementationAttemptState.EXECUTING: ((), None),
    ImplementationAttemptState.CHECKING: ((), "correction_cycle"),
}
"""From each state a run may enter: the triggers before its lease, then the one to `executing`.

A run that died mid-cycle (a lost worker or a dropped provider connection) left its
attempt `executing` or `checking`; the Job's next run resumes it under a new lease,
which fences the old one, with the budgets already consumed carried forward.
"""

_OUTCOME_TRIGGERS: Final[Mapping[ImplementationAttemptState, tuple[str, ...]]] = {
    ImplementationAttemptState.CANDIDATE_READY: ("run_checks", "checks_passed", "publish_candidate"),
    ImplementationAttemptState.CHECKS_FAILED: ("run_checks", "checks_failed"),
    ImplementationAttemptState.SCOPE_CHANGE_REQUIRED: ("require_scope_change",),
    ImplementationAttemptState.UNSAFE: ("mark_unsafe",),
    ImplementationAttemptState.BUDGET_EXHAUSTED: ("exhaust_budget",),
    ImplementationAttemptState.INFRASTRUCTURE_BLOCKED: ("block_infrastructure",),
}
"""The transitions that commit each typed run outcome from `executing`."""

_BLOCKERS: Final[Mapping[ImplementationAttemptState, tuple[str, CaseStopReason]]] = {
    ImplementationAttemptState.CHECKS_FAILED: (
        "the candidate did not pass the required checks",
        CaseStopReason.TECHNICAL_BLOCKER,
    ),
    ImplementationAttemptState.SCOPE_CHANGE_REQUIRED: (
        "the change needs an outcome or scope the approval does not cover",
        CaseStopReason.SCOPE_LIMIT,
    ),
    ImplementationAttemptState.UNSAFE: (
        "the attempt reached hosts the network policy does not permit",
        CaseStopReason.TECHNICAL_BLOCKER,
    ),
    ImplementationAttemptState.BUDGET_EXHAUSTED: (
        "the work did not fit the live session's execution budgets",
        CaseStopReason.SCOPE_LIMIT,
    ),
}
"""Each terminal outcome without a candidate: its Case File blocker and why the case stopped."""


async def load_attempt_specification(
    database: Database, *, implementation_attempt_id: UUID
) -> tuple[AttemptSpecification, ImplementationPolicy]:
    """Rebuild one attempt's typed specification from durable rows."""
    async with database.connection() as conn:
        attempt = await _attempt(conn, implementation_attempt_id)
        decision = await _decision(conn, as_uuid(attempt, "gate_decision_id"))
        policy = read_implementation_policy(
            configuration_release_id=as_uuid(decision, "pinned_configuration_release_id"),
            policy=await _policy_document(
                conn, as_uuid(decision, "pinned_configuration_release_id")
            ),
        )
        lease = await _active_lease(conn, implementation_attempt_id)
        findings = await _cited_findings(conn, _object(decision, "cited_evidence"))
    scope = ApprovedScope.from_columns(
        approved_scope=_object(decision, "approved_scope"),
        required_checks=_object(decision, "required_checks"),
        budgets=_object(decision, "budgets"),
    )
    specification = AttemptSpecification(
        client_id=as_uuid(attempt, "client_id"),
        product_signal_id=as_uuid(attempt, "product_signal_id"),
        implementation_attempt_id=implementation_attempt_id,
        gate_decision_id=as_uuid(attempt, "gate_decision_id"),
        interview_session_id=_optional_uuid(attempt, "interview_session_id"),
        pinned_configuration_release_id=as_uuid(decision, "pinned_configuration_release_id"),
        checkout=RepositoryCheckout(
            repository_ref=policy.repository_ref,
            clone_url=policy.clone_url,
            base_revision=as_str(attempt, "base_revision"),
            workspace_root=WORKSPACE_ROOT,
            application_port=policy.build_recipe.application_port,
        ),
        approved_scope=scope,
        lease=LeaseBinding(
            lease_id=as_uuid(lease, "id"), fencing_token=as_int(lease, "fencing_token")
        ),
        workspace_snapshot_id=(
            as_optional_str(attempt, "workspace_snapshot_id") or policy.workspace_snapshot_id
        ),
        context_bundle_refs=(f"gate_decision:{as_uuid(attempt, 'gate_decision_id')}",),
        code_findings=findings,
        callback_hosts=policy.callback_hosts,
    )
    return (specification, policy)


async def run_implementation_attempt(database: Database, claimed: ClaimedJob) -> None:
    """Run one authorized attempt to its outcome and commit what it produced."""
    attempt_id = await _attempt_for_signal(
        database, product_signal_id=UUID(str(claimed.input["product_signal_id"]))
    )
    entered = (await current_attempt(database, attempt_id=attempt_id)).state
    entry = _ENTRY_PATHS.get(entered)
    if entry is None:
        raise GuardRejectedError(
            machine="implementation_attempt",
            trigger="begin_execution",
            reason=f"an attempt in {entered.value} cannot be run",
        )
    before_lease, to_execute = entry
    for trigger in before_lease:
        await _advance(
            database, client_id=claimed.client_id, attempt_id=attempt_id, trigger=trigger, reason=None
        )
    await grant_environment_lease(
        database,
        client_id=claimed.client_id,
        implementation_attempt_id=attempt_id,
        workspace_ref=None,
        duration_seconds=max(int((claimed.deadline_at - datetime.now(tz=UTC)).total_seconds()), 1),
    )
    specification, policy = await load_attempt_specification(
        database, implementation_attempt_id=attempt_id
    )
    if specification.interview_session_id is None:
        raise GuardRejectedError(
            machine="implementation_attempt",
            trigger="begin_execution",
            reason="live implementation needs the originating user's live Interview Session",
        )
    if to_execute is not None:
        await _advance(
            database,
            client_id=claimed.client_id,
            attempt_id=attempt_id,
            trigger=to_execute,
            reason=None,
        )
    state = await current_attempt(database, attempt_id=attempt_id)
    ledger = BudgetLedger(
        budgets=specification.approved_scope.budgets,
        already_consumed=state.consumed,
        started_at=datetime.now(tz=UTC),
    )
    settings = get_settings()
    artifacts = ArtifactStore(
        database=database,
        supabase_url=settings.supabase_url,
        service_key=settings.supabase_secret_key,
    )
    async with (
        VercelSandboxes(grant=sandbox_grant_from_environment()) as sandboxes,
        github_app_from_environment(
            repository_ref=policy.repository_ref, request_timeout_seconds=_GITHUB_TIMEOUT_SECONDS
        ) as github,
        preview_session(database) as preview,
    ):
        runner = AttemptRunner(
            database=database,
            dependencies=WorkspaceDependencies(
                sandboxes=sandboxes,
                artifacts=artifacts,
                preview_runtime=PreviewCandidateDeployer(
                    database=database,
                    artifacts=artifacts,
                    session=preview,
                    interview_session_id=specification.interview_session_id,
                ),
                change_author=AgentAuthor(
                    configuration=openai_configuration_for(OpenAIRole.IMPLEMENTATION),
                    ledger=ledger,
                ),
            ),
            specification=specification,
            build_recipe=policy.build_recipe,
            repository_access_token=await github.installation_token(),
            openai_api_key=openai_api_key(),
            ledger=ledger,
        )
        result = await runner.run()
    await _commit_outcome(database, specification=specification, result=result)


async def _commit_outcome(
    database: Database, *, specification: AttemptSpecification, result: AttemptRunResult
) -> None:
    """Commit the run's typed outcome, and what it obliges for the Product Signal."""
    triggers = _OUTCOME_TRIGGERS.get(result.outcome)
    if triggers is None:
        _log.info(
            "implementation_attempt_stopped_by_session",
            implementation_attempt_id=str(specification.implementation_attempt_id),
            outcome=result.outcome.value,
        )
        return
    for trigger in triggers:
        await _advance(
            database,
            client_id=specification.client_id,
            attempt_id=specification.implementation_attempt_id,
            trigger=trigger,
            reason=result.reason,
        )
    blocker = _BLOCKERS.get(result.outcome)
    if result.outcome is ImplementationAttemptState.CANDIDATE_READY:
        await advance_product_signal_to_validation(database, specification=specification, result=result)
    elif blocker is not None:
        text, stop_reason = blocker
        await close_with_blocker_case_file(
            database,
            specification=specification,
            blocker_reason=f"{text}: {result.reason}" if result.reason else text,
            stop_reason=stop_reason,
        )


async def advance_product_signal_to_validation(
    database: Database, *, specification: AttemptSpecification, result: AttemptRunResult
) -> None:
    """Move the Product Signal to `awaiting_validation` behind its ready candidate."""
    await advance_product_signal(
        database,
        client_id=specification.client_id,
        product_signal_id=specification.product_signal_id,
        trigger="candidate_ready",
        facts=ProductSignalFacts(participation_occurred=True, case_file_published=False),
        actor=_ACTOR,
        payload={
            "implementation_attempt_id": str(specification.implementation_attempt_id),
            "preview_version_id": (
                None if result.handoff is None else str(result.handoff.preview_version_id)
            ),
        },
        jobs=(),
        reason_code=None,
    )


async def close_with_blocker_case_file(
    database: Database,
    *,
    specification: AttemptSpecification,
    blocker_reason: str,
    stop_reason: CaseStopReason,
) -> None:
    """Commit the blocker Case File and entitlement evaluation a failed attempt obliges.

    Assembling the Case File is what closes the Product Signal `closed_blocked`; the
    participant took part, so the Outreach Offer earning rules apply.
    """
    now = datetime.now(tz=UTC)
    stream = StreamRef(
        kind=AggregateKind.IMPLEMENTATION_ATTEMPT, id=specification.implementation_attempt_id
    )
    async with database.transaction() as conn:
        await append_fact(
            conn,
            client_id=specification.client_id,
            event=EventAppend(
                message_id=uuid7(),
                stream=stream,
                event_type="implementation_attempt_closed_case",
                payload={"blocker_reason": blocker_reason},
                actor=_ACTOR,
                causation_id=specification.implementation_attempt_id,
                correlation_id=specification.product_signal_id,
                occurred_at=now,
            ),
            audit=AuditIntent(
                audit_id=uuid7(),
                subject=stream,
                action="close_case_with_blocker",
                outcome="case_file_obliged",
                reason_code="implementation_blocked",
                actor=_ACTOR,
                pinned_configuration_release_id=specification.pinned_configuration_release_id,
                detail={"blocker_reason": blocker_reason},
            ),
            jobs=(
                case_file_assembly_job(
                    product_signal_id=specification.product_signal_id,
                    validation_attempt_id=None,
                    variant=CaseFileVariant.BLOCKED,
                    blocker_reason=blocker_reason,
                    closure_trigger="close_blocked",
                    closure_reason="implementation_blocked",
                    now=now,
                    deadline_seconds=CASE_FILE_DEADLINE_SECONDS,
                ),
                entitlement_job(
                    product_signal_id=specification.product_signal_id,
                    stop_reason=stop_reason.value,
                    now=now,
                    deadline_seconds=CASE_FILE_DEADLINE_SECONDS,
                ),
            ),
        )


async def _advance(
    database: Database,
    *,
    client_id: UUID,
    attempt_id: UUID,
    trigger: str,
    reason: str | None,
) -> None:
    """Commit one attempt transition under the current lease's facts."""
    await advance_attempt(
        database,
        client_id=client_id,
        attempt_id=attempt_id,
        trigger=trigger,
        facts=ImplementationAttemptFacts(
            lease_active=True,
            fencing_token_current=True,
            execution_budget_remaining=True,
            within_return_deadline=True,
            readiness_fresh=True,
            diff_within_approved_scope=True,
        ),
        mutation={},
        payload={} if reason is None else {"reason": reason},
        reason_code=reason,
    )


async def _attempt_for_signal(database: Database, *, product_signal_id: UUID) -> UUID:
    """Return the Product Signal's newest Implementation Attempt, the one its Job runs."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.implementation_attempts
            where product_signal_id = %s
            order by created_at desc
            limit 1
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="implementation_attempts", row_id=product_signal_id)
    return as_uuid(row, "id")


async def _attempt(conn: AsyncConnection[DictRow], attempt_id: UUID) -> DictRow:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select attempts.id, attempts.client_id, attempts.product_signal_id,
                   attempts.gate_decision_id, attempts.base_revision,
                   attempts.workspace_snapshot_id,
                   (select sessions.id from public.interview_sessions as sessions
                     where sessions.product_signal_id = attempts.product_signal_id
                       and sessions.state = 'live'
                     order by sessions.created_at desc limit 1) as interview_session_id
            from public.implementation_attempts as attempts
            where attempts.id = %s
            """,
            (attempt_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="implementation_attempts", row_id=attempt_id)
    return row


async def _decision(conn: AsyncConnection[DictRow], decision_id: UUID) -> DictRow:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select pinned_configuration_release_id, approved_scope, required_checks, budgets,
                   cited_evidence
            from public.gate_decisions where id = %s
            """,
            (decision_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="gate_decisions", row_id=decision_id)
    return row


async def _cited_findings(
    conn: AsyncConnection[DictRow], cited_evidence: Mapping[str, object]
) -> tuple[CodeFindingHint, ...]:
    """Load the investigation findings the gate decision cited, in path order."""
    cited = cited_evidence.get("code_finding_ids")
    if not isinstance(cited, list) or not all(isinstance(item, str) for item in cited):
        raise TypeError("gate_decisions.cited_evidence.code_finding_ids is not a list of ids")
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select path, first_line, last_line, note
            from public.code_investigation_findings
            where id = any(%s)
            order by path, first_line
            """,
            ([UUID(item) for item in cited],),
        )
        rows = await cur.fetchall()
    if len(rows) != len(cited):
        raise GuardRejectedError(
            machine="implementation_attempt",
            trigger="load_specification",
            reason=f"the gate cited {len(cited)} code findings but {len(rows)} exist",
        )
    return tuple(
        CodeFindingHint(
            path=as_str(row, "path"),
            first_line=as_int(row, "first_line"),
            last_line=as_int(row, "last_line"),
            note=as_str(row, "note"),
        )
        for row in rows
    )


async def _policy_document(
    conn: AsyncConnection[DictRow], release_id: UUID
) -> Mapping[str, object]:
    async with conn.cursor() as cur:
        await cur.execute(
            "select policy from public.configuration_releases where id = %s", (release_id,)
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="configuration_releases", row_id=release_id)
    document = row["policy"]
    if not isinstance(document, Mapping):
        raise TypeError("configuration_releases.policy is not a JSON object")
    return document


async def _active_lease(conn: AsyncConnection[DictRow], attempt_id: UUID) -> DictRow:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, fencing_token from public.environment_leases
            where implementation_attempt_id = %s and state = 'active'
            """,
            (attempt_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="environment_leases", row_id=attempt_id)
    return row


def _object(row: DictRow, column: str) -> Mapping[str, object]:
    value = row[column]
    if not isinstance(value, Mapping):
        raise TypeError(f"gate_decisions.{column} is not a JSON object")
    return value


def _optional_uuid(row: DictRow, column: str) -> UUID | None:
    return None if row[column] is None else as_uuid(row, column)

