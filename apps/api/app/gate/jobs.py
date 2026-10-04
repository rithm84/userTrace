"""The Implementation Gate Job: investigate, propose, decide, and act on the decision.

A Product Signal reaches `gate_pending` when the interview has agreed the scope and
its acceptance criteria. This Job then runs the gate end to end:

1. The findings come from the investigation that ran while the interview did
   (`run_early_investigation`, for the concern intake recorded), waited for while
   it is still running. Only when it left none does an OpenAI model investigate
   the authorized checkout read-only in Codex here, for the agreed concern. Code
   records the exact-revision findings it can verify either way.
2. The gate bundle is assembled from durable rows, and an OpenAI model proposes a typed,
   cited decision over it.
3. The deterministic controller validates, screens, and commits the decision.
4. `approved_live` creates the one Implementation Attempt, which dispatches the
   implementation Job; `no_implementation` commits the explanatory Case File, which
   closes the case, and the Outreach Offer entitlement evaluation.

The interview carries on throughout, so the case can move under a classification
or a proposal; that step is then decided again on fresh context, a bounded number
of times. A gate Job left with no legal run takes the technical-blocker path in
the transaction that records its failure (`settle_failed_gate`).

Contract: docs/arch-components/implementation.md#enforcement-model
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    CaseFileVariant,
    GateDecisionKind,
    InterviewPhase,
    ProductSignalState,
)

from app.connectors.github_delivery import github_app_from_environment
from app.connectors.openai_roles import (
    OpenAIRole,
    openai_api_key,
    openai_configuration_for,
    openai_gateway_for,
)
from app.connectors.typesafe_jev import JevClient
from app.connectors.vercel_sandboxes import VercelSandboxes, sandbox_grant_from_environment
from app.context.freshness import decide_on_fresh_context
from app.db.kernel import append_fact
from app.db.pool import Database
from app.db.rows import as_int, as_str, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, Failure, JobIntent, JsonValue, StreamRef
from app.errors import AggregateNotFoundError
from app.execution.jobs import ClaimedJob
from app.gate.bundle import assemble_gate_bundle
from app.gate.classification import establish_flow_classification
from app.gate.controller import GateOutcome, decide_gate
from app.gate.job_types import (
    GATE_FAILED_EVENT,
    GATE_NO_IMPLEMENTATION_EVENT,
    GateFailureOutcome,
)
from app.gate.policy import ImplementationPolicy, load_implementation_policy
from app.gate.proposal import request_gate_proposal
from app.ids import uuid7
from app.implementation.budgets import ConsumedBudgets
from app.implementation.handlers import RUN_IMPLEMENTATION_ATTEMPT
from app.implementation.investigation import InvestigationRequest, investigate_repository
from app.implementation.job_types import INVESTIGATE_CODE
from app.interview.projection import (
    advance_phase_on_connection,
    return_to_validation_on_connection,
)
from app.interview.settings import get_jev_settings
from app.interview.voice.session_changes import wake_case_sidebands
from app.lifecycle.attempts import AttemptRequest, create_implementation_attempt
from app.lifecycle.product_signal import ProductSignalFacts
from app.lifecycle.signal_transitions import advance_product_signal_on_connection
from app.logging import get_logger
from app.offers.entitlement import CaseStopReason
from app.outputs.closure_jobs import (
    CASE_FILE_DEADLINE_SECONDS,
    case_file_assembly_job,
    entitlement_job,
)

__all__ = ["run_early_investigation", "run_gate", "settle_failed_gate"]

_log = get_logger(__name__)

_ACTOR: Final[Actor] = Actor(kind=ActorKind.SERVICE, ref="gate.controller")
_GITHUB_TIMEOUT_SECONDS: Final[float] = 30.0
_PROPOSAL_MAX_OUTPUT_TOKENS: Final[int] = 16_000
_ATTEMPT_TIMEOUT_MARGIN_SECONDS: Final[int] = 900
_EARLY_INVESTIGATION_WAIT_SECONDS: Final[float] = 240.0
"""How long the gate waits for an investigation still running before running its own."""
_EARLY_INVESTIGATION_POLL_SECONDS: Final[float] = 2.0
_DECISION_ATTEMPTS: Final[int] = 3
"""How many times one run decides in all while the case moves under its proposal."""
_GATE_FAILED: Final[str] = "implementation_gate_failed"
"""The reason code a failed gate's blocker and declined change carry."""
_ENDED_STATES: Final[frozenset[str]] = frozenset(
    {"closed_validated", "closed_no_implementation", "closed_blocked", "canceled"}
)


async def run_gate(database: Database, claimed: ClaimedJob) -> None:
    """Run the Implementation Gate for one Product Signal and act on its decision."""
    product_signal_id = UUID(str(claimed.input["product_signal_id"]))
    jev_settings = get_jev_settings()
    async with JevClient(
        api_key=jev_settings.api_key,
        model_id=jev_settings.model_id,
        timeout_seconds=jev_settings.timeout_seconds,
    ) as jev:
        # The gate screens against the interview-established flow, so it is
        # confirmed first when the interview has not recorded one.
        await establish_flow_classification(
            database, jev, client_id=claimed.client_id, product_signal_id=product_signal_id
        )
    signal = await _signal(database, product_signal_id=product_signal_id)
    policy = await load_implementation_policy(
        database, configuration_release_id=as_uuid(signal, "pinned_configuration_release_id")
    )
    if not await _early_findings(database, product_signal_id=product_signal_id):
        await _investigate(
            database,
            client_id=claimed.client_id,
            product_signal_id=product_signal_id,
            policy=policy,
            concern=await _agreed_concern(database, product_signal_id=product_signal_id),
        )
    outcome = await decide_on_fresh_context(
        step="implementation_gate_decision",
        product_signal_id=product_signal_id,
        attempts=_DECISION_ATTEMPTS,
        decide=lambda: _decide(
            database,
            client_id=claimed.client_id,
            product_signal_id=product_signal_id,
            policy=policy,
        ),
    )
    _log.info(
        "gate_decided",
        client_id=str(claimed.client_id),
        product_signal_id=str(product_signal_id),
        gate_decision_id=str(outcome.gate_decision_id),
        decision=outcome.decision.value,
        blocker=None if outcome.blocker is None else outcome.blocker.value,
    )
    if outcome.decision is GateDecisionKind.APPROVED_LIVE:
        await _authorize_attempt(
            database, claimed=claimed, signal_row=signal, policy=policy, outcome=outcome
        )
    elif await _has_ready_version(database, product_signal_id=product_signal_id):
        await _decline_change(
            database, client_id=claimed.client_id, product_signal_id=product_signal_id, outcome=outcome
        )
    else:
        await _close_without_implementation(
            database, client_id=claimed.client_id, product_signal_id=product_signal_id, outcome=outcome
        )


async def _decide(
    database: Database, *, client_id: UUID, product_signal_id: UUID, policy: ImplementationPolicy
) -> GateOutcome:
    """Assemble the gate bundle now, ask for a proposal over it, and decide against it."""
    now = datetime.now(tz=UTC)
    bundle = await assemble_gate_bundle(
        database, client_id=client_id, product_signal_id=product_signal_id, now=now
    )
    async with openai_gateway_for(OpenAIRole.GATE_PROPOSAL) as gateway:
        proposal = await request_gate_proposal(
            gateway, bundle=bundle, max_output_tokens=_PROPOSAL_MAX_OUTPUT_TOKENS
        )
    return await decide_gate(
        database, bundle=bundle, proposal=proposal, base_revision=policy.base_revision, now=now
    )


async def run_early_investigation(database: Database, claimed: ClaimedJob) -> None:
    """Investigate the code for the concern intake recorded, while the interview runs."""
    product_signal_id = UUID(str(claimed.input["product_signal_id"]))
    signal = await _signal(database, product_signal_id=product_signal_id)
    state = as_str(signal, "state")
    if state in _ENDED_STATES:
        _log.info(
            "early_investigation_skipped",
            product_signal_id=str(product_signal_id),
            reason=f"the case is {state}",
        )
        return
    await _investigate(
        database,
        client_id=claimed.client_id,
        product_signal_id=product_signal_id,
        policy=await load_implementation_policy(
            database, configuration_release_id=as_uuid(signal, "pinned_configuration_release_id")
        ),
        concern=await _intake_concern(database, product_signal_id=product_signal_id),
    )


async def _investigate(
    database: Database,
    *,
    client_id: UUID,
    product_signal_id: UUID,
    policy: ImplementationPolicy,
    concern: str,
) -> None:
    """Investigate the authorized checkout read-only for one concern."""
    async with (
        VercelSandboxes(grant=sandbox_grant_from_environment()) as sandboxes,
        github_app_from_environment(
            repository_ref=policy.repository_ref, request_timeout_seconds=_GITHUB_TIMEOUT_SECONDS
        ) as github,
    ):
        await investigate_repository(
            database,
            sandboxes,
            request=InvestigationRequest(
                client_id=client_id,
                product_signal_id=product_signal_id,
                repository_ref=policy.repository_ref,
                clone_url=policy.clone_url,
                base_revision=policy.base_revision,
                workspace_snapshot_id=policy.workspace_snapshot_id,
                callback_hosts=policy.callback_hosts,
                concern=concern,
            ),
            configuration=openai_configuration_for(OpenAIRole.CODE_INVESTIGATION),
            repository_token=await github.installation_token(),
            openai_api_key=openai_api_key(),
        )


async def _early_findings(database: Database, *, product_signal_id: UUID) -> bool:
    """Whether findings exist, waiting for an investigation the interview started."""
    deadline = time.monotonic() + _EARLY_INVESTIGATION_WAIT_SECONDS
    while True:
        async with database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select
                  exists (
                    select 1 from public.code_investigation_findings
                    where product_signal_id = %s
                  ) as found,
                  exists (
                    select 1 from public.jobs
                    where job_type = %s and input->>'product_signal_id' = %s
                      and state in ('pending', 'ready', 'leased')
                  ) as running
                """,
                (product_signal_id, INVESTIGATE_CODE, str(product_signal_id)),
            )
            row = await cur.fetchone()
        if row is None:
            raise RuntimeError("the findings query returned no row")
        if row["found"]:
            return True
        if not row["running"] or time.monotonic() >= deadline:
            _log.info(
                "early_investigation_unavailable",
                product_signal_id=str(product_signal_id),
                still_running=bool(row["running"]),
            )
            return False
        await asyncio.sleep(_EARLY_INVESTIGATION_POLL_SECONDS)


async def _authorize_attempt(
    database: Database,
    *,
    claimed: ClaimedJob,
    signal_row: dict[str, object],
    policy: ImplementationPolicy,
    outcome: GateOutcome,
) -> None:
    """Create the attempt this gate decision authorized and dispatch the implementation Job."""
    product_signal_id = UUID(str(claimed.input["product_signal_id"]))
    now = datetime.now(tz=UTC)
    budgets = outcome.approved_scope.budgets if outcome.approved_scope is not None else policy.budgets
    job = JobIntent(
        job_id=uuid7(),
        job_type=RUN_IMPLEMENTATION_ATTEMPT,
        input={"product_signal_id": str(product_signal_id)},
        deadline_at=now + timedelta(seconds=budgets.elapsed_seconds + _ATTEMPT_TIMEOUT_MARGIN_SECONDS),
        timeout_seconds=budgets.elapsed_seconds + _ATTEMPT_TIMEOUT_MARGIN_SECONDS,
        max_runs=1,
        resource_budget={"kind": RUN_IMPLEMENTATION_ATTEMPT},
        retryable_categories=[],
        backoff_base_seconds=30,
        backoff_max_seconds=300,
        concurrency_key=f"implementation:{product_signal_id}",
        cancellation_scope_id=UUID(str(signal_row["cancellation_scope_id"])),
        not_before=now,
    )
    creation = await create_implementation_attempt(
        database,
        AttemptRequest(
            client_id=claimed.client_id,
            product_signal_id=product_signal_id,
            gate_decision_id=outcome.gate_decision_id,
            repository_ref=policy.repository_ref,
            base_revision=policy.base_revision,
            expected_classification_revision=int(str(signal_row["classification_revision"])),
            expected_configuration_release_id=UUID(
                str(signal_row["pinned_configuration_release_id"])
            ),
            implementation_limit=policy.implementation_limit,
            consumed_budgets=ConsumedBudgets.empty().as_column(),
            job=job,
            actor=_ACTOR,
        ),
    )
    _log.info(
        "implementation_attempt_dispatched",
        product_signal_id=str(product_signal_id),
        implementation_attempt_id=str(creation.attempt_id),
        replayed=creation.replayed,
    )
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select id from public.interview_sessions where product_signal_id = %s",
                (product_signal_id,),
            )
            sessions = await cur.fetchall()
        for session in sessions:
            await advance_phase_on_connection(
                conn,
                interview_session_id=as_uuid(session, "id"),
                phase=InterviewPhase.IMPLEMENTATION,
            )


async def _has_ready_version(database: Database, *, product_signal_id: UUID) -> bool:
    """Whether the case already has a ready version, so the gate decided a requested change."""
    async with database.connection() as conn:
        return await _has_ready_version_on_connection(conn, product_signal_id=product_signal_id)


async def _has_ready_version_on_connection(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID
) -> bool:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select exists (
              select 1 from public.preview_versions
              where product_signal_id = %s and state = 'ready'
                and superseded_by_preview_version_id is null
            ) as ready
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    return row is not None and bool(row["ready"])


async def _decline_change(
    database: Database, *, client_id: UUID, product_signal_id: UUID, outcome: GateOutcome
) -> None:
    """Keep the version the person has after the gate refused the change they asked for.

    The case goes back to validating its ready version against the criteria that
    version was approved for; the refused decision stays on record, and the session
    returns to validation, where the agent hears why.
    """
    reason = (
        f"{outcome.blocker.value}: {outcome.blocker_detail}"
        if outcome.blocker is not None
        else "the gate chose no implementation"
    )
    async with database.transaction() as conn:
        await _decline_change_on_connection(
            conn,
            client_id=client_id,
            product_signal_id=product_signal_id,
            gate_decision_id=outcome.gate_decision_id,
            reason=reason,
            reason_code=None if outcome.blocker is None else outcome.blocker.value,
        )
    _log.info(
        "gate_declined_change",
        client_id=str(client_id),
        product_signal_id=str(product_signal_id),
        gate_decision_id=str(outcome.gate_decision_id),
        reason=reason,
    )


async def _decline_change_on_connection(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    product_signal_id: UUID,
    gate_decision_id: UUID | None,
    reason: str,
    reason_code: str | None,
) -> None:
    """Return the case to validating its ready version, inside the caller's transaction.

    The version is validated against the criteria it was approved for, and the
    live session returns to validation.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select (approved_scope ->> 'acceptance_criteria_revision')::int as revision
            from public.gate_decisions
            where product_signal_id = %s and decision = %s
            order by decided_at desc
            limit 1
            """,
            (product_signal_id, GateDecisionKind.APPROVED_LIVE.value),
        )
        approved = await cur.fetchone()
        if approved is None:
            raise AggregateNotFoundError(table="gate_decisions", row_id=product_signal_id)
        await cur.execute(
            "update public.product_signals set acceptance_criteria_revision = %s where id = %s",
            (as_int(approved, "revision"), product_signal_id),
        )
        await cur.execute(
            "select id from public.interview_sessions where product_signal_id = %s and state = 'live'",
            (product_signal_id,),
        )
        sessions = await cur.fetchall()
    await advance_product_signal_on_connection(
        conn,
        client_id=client_id,
        product_signal_id=product_signal_id,
        trigger="revision_declined",
        facts=ProductSignalFacts(participation_occurred=True, case_file_published=False),
        actor=_ACTOR,
        payload={
            "gate_decision_id": None if gate_decision_id is None else str(gate_decision_id),
            "reason": reason,
            "acceptance_criteria_revision": as_int(approved, "revision"),
        },
        jobs=(),
        reason_code=reason_code,
    )
    for session in sessions:
        await return_to_validation_on_connection(conn, interview_session_id=as_uuid(session, "id"))


async def _close_without_implementation(
    database: Database, *, client_id: UUID, product_signal_id: UUID, outcome: GateOutcome
) -> None:
    """Commit the explanatory Case File and the entitlement a completed interview earns."""
    now = datetime.now(tz=UTC)
    reason = (
        f"{outcome.blocker.value}: {outcome.blocker_detail}"
        if outcome.blocker is not None
        else "the gate chose no implementation"
    )
    stream = StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=product_signal_id)
    async with database.transaction() as conn:
        await append_fact(
            conn,
            client_id=client_id,
            event=EventAppend(
                message_id=uuid7(),
                stream=stream,
                event_type=GATE_NO_IMPLEMENTATION_EVENT,
                payload={"gate_decision_id": str(outcome.gate_decision_id), "reason": reason},
                actor=_ACTOR,
                causation_id=outcome.gate_decision_id,
                correlation_id=product_signal_id,
                occurred_at=now,
            ),
            audit=AuditIntent(
                audit_id=uuid7(),
                subject=stream,
                action="gate_no_implementation",
                outcome="case_file_obliged",
                reason_code=None if outcome.blocker is None else outcome.blocker.value,
                actor=_ACTOR,
                pinned_configuration_release_id=None,
                detail={"gate_decision_id": str(outcome.gate_decision_id)},
            ),
            jobs=(
                case_file_assembly_job(
                    product_signal_id=product_signal_id,
                    validation_attempt_id=None,
                    variant=CaseFileVariant.NO_IMPLEMENTATION,
                    blocker_reason=reason,
                    closure_trigger="no_implementation",
                    closure_reason=reason,
                    now=now,
                    deadline_seconds=CASE_FILE_DEADLINE_SECONDS,
                ),
                entitlement_job(
                    product_signal_id=product_signal_id,
                    stop_reason=CaseStopReason.GATE_NO_IMPLEMENTATION.value,
                    now=now,
                    deadline_seconds=CASE_FILE_DEADLINE_SECONDS,
                ),
            ),
        )
        await wake_case_sidebands(conn, product_signal_id=product_signal_id)


async def settle_failed_gate(
    conn: AsyncConnection[DictRow], claimed: ClaimedJob, failure: Failure
) -> None:
    """Take the technical-blocker path for a gate Job that has no legal run left.

    Runs in the transaction that records the Job's terminal failure, so a failed
    gate can never leave its case waiting in `gate_pending`. The change cannot
    start: a case with a ready version keeps it, the change the person asked for
    declined, and its validation goes on; any other case closes with its blocker
    Case File, and the Outreach Offer is evaluated as a technical blocker. The
    committed fact names which, and the case's open sessions are woken so the
    sideband tells the agent at once.
    """
    product_signal_id = UUID(str(claimed.input["product_signal_id"]))
    async with conn.cursor() as cur:
        await cur.execute(
            "select state::text as state from public.product_signals where id = %s for update",
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="product_signals", row_id=product_signal_id)
    state = as_str(row, "state")
    if state != ProductSignalState.GATE_PENDING.value:
        _log.info(
            "gate_failure_needs_no_settlement",
            client_id=str(claimed.client_id),
            product_signal_id=str(product_signal_id),
            job_id=str(claimed.job_id),
            state=state,
        )
        return
    now = datetime.now(tz=UTC)
    blocker = f"the Implementation Gate could not complete ({failure.category}: {failure.code})"
    jobs: tuple[JobIntent, ...] = ()
    if await _has_ready_version_on_connection(conn, product_signal_id=product_signal_id):
        outcome = GateFailureOutcome.CHANGE_DECLINED
        await _decline_change_on_connection(
            conn,
            client_id=claimed.client_id,
            product_signal_id=product_signal_id,
            gate_decision_id=None,
            reason=blocker,
            reason_code=_GATE_FAILED,
        )
    else:
        outcome = GateFailureOutcome.CASE_BLOCKED
        jobs = (
            case_file_assembly_job(
                product_signal_id=product_signal_id,
                validation_attempt_id=None,
                variant=CaseFileVariant.BLOCKED,
                blocker_reason=blocker,
                closure_trigger="close_blocked",
                closure_reason=_GATE_FAILED,
                now=now,
                deadline_seconds=CASE_FILE_DEADLINE_SECONDS,
            ),
            entitlement_job(
                product_signal_id=product_signal_id,
                stop_reason=CaseStopReason.TECHNICAL_BLOCKER.value,
                now=now,
                deadline_seconds=CASE_FILE_DEADLINE_SECONDS,
            ),
        )
    stream = StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=product_signal_id)
    detail: dict[str, JsonValue] = {
        "outcome": outcome.value,
        "job_id": str(claimed.job_id),
        "failure_category": str(failure.category),
        "failure_code": failure.code,
        "failure_message": failure.message,
    }
    await append_fact(
        conn,
        client_id=claimed.client_id,
        event=EventAppend(
            message_id=uuid7(),
            stream=stream,
            event_type=GATE_FAILED_EVENT,
            payload=detail,
            actor=_ACTOR,
            causation_id=claimed.job_id,
            correlation_id=product_signal_id,
            occurred_at=now,
        ),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=stream,
            action="settle_failed_gate",
            outcome=outcome.value,
            reason_code=_GATE_FAILED,
            actor=_ACTOR,
            pinned_configuration_release_id=None,
            detail=detail,
        ),
        jobs=jobs,
    )
    await wake_case_sidebands(conn, product_signal_id=product_signal_id)
    _log.warning(
        "gate_failed_terminally",
        client_id=str(claimed.client_id),
        product_signal_id=str(product_signal_id),
        job_id=str(claimed.job_id),
        outcome=outcome.value,
        failure_category=str(failure.category),
        failure_code=failure.code,
        failure_message=failure.message,
    )


async def _signal(database: Database, *, product_signal_id: UUID) -> dict[str, object]:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select state::text as state, pinned_configuration_release_id,
                   classification_revision, cancellation_scope_id
            from public.product_signals where id = %s
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="product_signals", row_id=product_signal_id)
    return dict(row)


async def _agreed_concern(database: Database, *, product_signal_id: UUID) -> str:
    """Render the agreed outcome and its criteria as the investigation's concern."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select agreed_scope, acceptance_criteria_revision
            from public.acceptance_criteria_agreements
            where product_signal_id = %s
            order by acceptance_criteria_revision desc limit 1
            """,
            (product_signal_id,),
        )
        agreement = await cur.fetchone()
        if agreement is None:
            raise AggregateNotFoundError(
                table="acceptance_criteria_agreements", row_id=product_signal_id
            )
        await cur.execute(
            """
            select statement from public.validation_criteria
            where product_signal_id = %s and criteria_revision = %s order by ordinal
            """,
            (product_signal_id, as_int(agreement, "acceptance_criteria_revision")),
        )
        criteria = await cur.fetchall()
    scope = agreement["agreed_scope"]
    outcome = scope.get("intended_outcome") if isinstance(scope, dict) else None
    lines = [f"Agreed outcome: {outcome}"] + [
        f"- {as_str(row, 'statement')}" for row in criteria
    ]
    return "\n".join(lines)


async def _intake_concern(database: Database, *, product_signal_id: UUID) -> str:
    """Render the latest recorded concern as an investigation's, before any agreement."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select pain_point, validation_criterion, user_wording
            from public.product_signal_concerns
            where product_signal_id = %s
            order by concern_revision desc limit 1
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="product_signal_concerns", row_id=product_signal_id)
    wording = row["user_wording"]
    messages = wording.get("messages") if isinstance(wording, dict) else None
    quoted = (
        [f"> {message}" for message in messages if isinstance(message, str)]
        if isinstance(messages, list)
        else []
    )
    return "\n".join(
        [
            "The customer's request, before the interview has agreed a scope:",
            *quoted,
            f"Pain point: {as_str(row, 'pain_point')}",
            f"Outcome to reach: {as_str(row, 'validation_criterion')}",
        ]
    )
