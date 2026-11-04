"""Prepared drafts: a change written before the participant agrees to it.

Contract: docs/arch-components/implementation.md#prepared-draft

Two kinds of draft share one record and one Job:

- An intake draft starts once the case's prepared workspace is ready, from the
  original request (`record_intake_draft`), so code exists before the call does.
- A proposal draft starts when the voice agent proposes a change
  (`record_change_proposal`). It revises an intake draft the workspace already
  holds into exactly the proposed change, or starts from the workspace's own
  starting files when there is none.

The Job runs one Codex cycle on a draft task, then formats, stages, and diffs the
change as an attempt's cycle does, and records the draft's files, patch, source
digest, dependency identity, cost, and fast-check results. Either kind is staged in
the session's spare candidate runtime, which builds it for production, so a
proposal that revises the intake draft rebuilds only the files it changes. A
proposal draft also prepares the gate's evaluation beside its Codex run
(`app.gate.prepared`), and finishes the candidate before the answer: it stores the
draft's large artifacts and proves the spare ready for the Preview Version
identifier it mints, so the attempt that adopts the draft publishes and swaps
without staging, building, or probing again.

A draft holds no lease and grants nothing. Its tool calls are fenced by its own
record (`_require_current`): once a newer proposal, a different agreement, or the
case's end discards it, its next call is refused. An Implementation Attempt adopts
a draft only when the agreement its gate decision approved records that draft
(`adoptable_draft`). A draft that is discarded while its Codex run is still
writing keeps writing until that run ends, so whoever takes the workspace next
waits until the draft has settled (`settled_at`). Every draft records what it
spent, adopted or not, so a case's drafts cost what its rows add up to.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

import structlog
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.canonical import payload_digest
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    CriterionObservability,
    ProductSignalState,
)
from utrace_contracts.envelope.artifacts import ArtifactReference

from app.connectors.failures import ConnectorFailure
from app.connectors.openai_roles import OpenAIRole, openai_api_key, openai_configuration_for
from app.connectors.vercel_sandboxes import VercelSandboxes, sandbox_grant_from_environment
from app.context.bundles import AssembledBundle
from app.db.kernel import append_fact
from app.db.pool import Database
from app.db.rows import as_int, as_optional_str, as_optional_uuid, as_str, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, JobIntent, JsonValue, StreamRef
from app.errors import (
    AggregateNotFoundError,
    BudgetExhaustedError,
    GuardRejectedError,
    LeaseLostError,
)
from app.execution.jobs import ClaimedJob
from app.gate.policy import ImplementationPolicy, load_implementation_policy
from app.gate.prepared import prepare_gate_proposal
from app.gate.scope import AcceptanceCriterion, ApprovedScope
from app.ids import uuid7
from app.implementation.agent_author import AgentAuthor, ChangeAuthor, CycleFeedback
from app.implementation.artifact_store import ArtifactStore
from app.implementation.artifacts import prestore_draft_artifacts
from app.implementation.budgets import BudgetLedger, ConsumedBudgets
from app.implementation.checks import (
    CheckPhase,
    CheckRun,
    candidate_runtime_runs,
    run_required_checks,
    workspace_fast_checks,
)
from app.implementation.cycle import (
    format_changed_files,
    inspect_workspace,
    reset_to_base,
    stage_and_diff,
    staged_files,
)
from app.implementation.draft_records import (
    OPEN_DRAFT_STATES,
    DraftKind,
    ProposedChange,
    ProposedCriterion,
    change_from_row,
    discard_open_drafts,
)
from app.implementation.environment import WORKSPACE_ROOT, working_egress
from app.implementation.job_types import INVESTIGATE_CODE
from app.implementation.ports import DependencyLockIdentity, PreviewRuntimePort, StagedCandidate
from app.implementation.specification import (
    AttemptSpecification,
    CodeFindingHint,
    LeaseBinding,
    RepositoryCheckout,
)
from app.implementation.tools import ReadFile, ToolExecutor, ToolJournalEntry, ToolName
from app.preview.candidate_intake import PreviewCandidateDeployer
from app.preview.jobs import preview_session
from app.preview.spare import request_spare_candidate, request_standby_spare
from app.settings import get_settings

__all__ = [
    "PREPARE_DRAFT",
    "PreparedDraft",
    "adopt_draft",
    "adoptable_draft",
    "draft_change",
    "files_digest",
    "prepare_draft",
    "record_change_proposal",
    "record_intake_draft",
    "wait_until_settled",
]

_logger = structlog.get_logger(__name__)

PREPARE_DRAFT: Final[str] = "implementation.prepare_draft"
_ACTOR: Final[Actor] = Actor(kind=ActorKind.SERVICE, ref="implementation.drafts")
_PROPOSABLE_STATES: Final[frozenset[str]] = frozenset(
    {
        ProductSignalState.INTERVIEWING.value,
        ProductSignalState.AWAITING_VALIDATION.value,
        ProductSignalState.VALIDATING.value,
    }
)
_INTAKE_STATES: Final[frozenset[str]] = frozenset(
    {ProductSignalState.AWAITING_INTERVIEW.value, ProductSignalState.INTERVIEWING.value}
)
_WORKSPACE_WAIT_SECONDS: Final[float] = 120.0
"""How long a draft waits for the case's workspace to finish preparing."""
_SETTLE_WAIT_SECONDS: Final[float] = 120.0
"""How long the next holder of a workspace waits for a draft still writing in it."""
_INVESTIGATION_WAIT_SECONDS: Final[float] = 60.0
"""How long a draft waits for the early code investigation's findings."""
_POLL_SECONDS: Final[float] = 0.5
_DRAFT_JOB_TIMEOUT_SECONDS: Final[int] = 900
_INTAKE_WORKFLOW: Final[str] = "requested_feature"
_ADJUSTMENT: Final[str] = (
    "The workspace already holds a draft written from the customer's original "
    "request, before the interview agreed on a change. Its whole change against the "
    "base revision is the diff below, so the files it touched need no reading first. "
    "Revise it so it makes exactly the change above and meets exactly those "
    "criteria: keep what fits, change what differs, and remove what the agreed change "
    "does not need. When it already does, change nothing.\n<draft-diff>\n{patch}\n</draft-diff>"
)


@dataclass(frozen=True, slots=True)
class PreparedDraft:
    """One recorded draft, as an adopting attempt reads it."""

    prepared_draft_id: UUID
    state: str
    kind: str
    prepared_workspace_id: UUID | None
    change: ProposedChange
    files: Mapping[str, str]
    files_digest: str | None
    patch: str
    source_digest: str | None
    dependencies: DependencyLockIdentity | None
    dependency_manifest: str | None
    """The dependency manifest's content, which the version's artifact holds."""
    preview_version_id: UUID | None
    consumed: ConsumedBudgets
    fast_checks: tuple[CheckRun, ...]
    staged: StagedCandidate | None
    journal: tuple[tuple[ToolName, str], ...]
    """What the draft ran and touched, for the adopting attempt's own journal."""


@dataclass(frozen=True, slots=True)
class _TakenWorkspace:
    prepared_workspace_id: UUID
    sandbox_name: str
    earlier_draft_id: UUID | None
    starting_tree: str | None


async def record_change_proposal(
    database: Database, *, bundle: AssembledBundle, change: ProposedChange
) -> UUID:
    """Record the change the voice agent is proposing and queue its draft.

    A newer proposal discards any open draft for the case, except an intake draft
    still being written, which it revises once that finishes. Nothing about the case's
    lifecycle moves: a proposal is not an agreement.
    """
    interview_session_id = bundle.interview_session_id
    if interview_session_id is None:
        raise GuardRejectedError(
            machine="product_signal",
            trigger="propose_change",
            reason="a change is proposed only in a live Interview Session",
        )
    async with database.transaction() as conn:
        state = await _lock_signal_state(conn, product_signal_id=bundle.product_signal_id)
        if state not in _PROPOSABLE_STATES:
            raise GuardRejectedError(
                machine="product_signal",
                trigger="propose_change",
                reason=f"a change cannot be proposed while the case is {state}",
            )
        await discard_open_drafts(
            conn,
            product_signal_id=bundle.product_signal_id,
            keep=None,
            reason="a newer proposal replaced it",
            spare_running_intake=True,
        )
        draft_id = await _insert_draft(
            conn,
            client_id=bundle.client_id,
            product_signal_id=bundle.product_signal_id,
            interview_session_id=interview_session_id,
            change=change,
            kind=DraftKind.PROPOSAL,
            request=None,
            pinned_configuration_release_id=bundle.dependencies.pinned_configuration_release_id,
        )
        await request_spare_candidate(
            conn,
            client_id=bundle.client_id,
            product_signal_id=bundle.product_signal_id,
            interview_session_id=interview_session_id,
        )
    return draft_id


async def record_intake_draft(
    database: Database, *, client_id: UUID, product_signal_id: UUID
) -> UUID | None:
    """Queue a draft of the requested feature from the case's original request.

    It is queued once, before any proposal, for a case whose interview has not agreed
    a change and that has a session to stage it for later; nobody hears of it.
    """
    async with database.transaction() as conn:
        state = await _lock_signal_state(conn, product_signal_id=product_signal_id)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select signals.pinned_configuration_release_id,
                       (select sessions.id from public.interview_sessions as sessions
                         where sessions.product_signal_id = signals.id
                         order by sessions.created_at desc limit 1) as interview_session_id,
                       exists (select 1 from public.prepared_drafts as drafts
                               where drafts.product_signal_id = signals.id) as drafted,
                       concerns.pain_point, concerns.validation_criterion, concerns.user_wording
                from public.product_signals as signals
                left join lateral (
                  select pain_point, validation_criterion, user_wording
                  from public.product_signal_concerns
                  where product_signal_id = signals.id
                  order by concern_revision desc limit 1
                ) as concerns on true
                where signals.id = %s
                """,
                (product_signal_id,),
            )
            row = await cur.fetchone()
        session = None if row is None else as_optional_uuid(row, "interview_session_id")
        criterion = None if row is None else as_optional_str(row, "validation_criterion")
        if (
            row is None
            or state not in _INTAKE_STATES
            or session is None
            or criterion is None
            or row["drafted"]
        ):
            return None
        wording = row["user_wording"]
        messages = wording.get("messages") if isinstance(wording, dict) else None
        return await _insert_draft(
            conn,
            client_id=client_id,
            product_signal_id=product_signal_id,
            interview_session_id=session,
            change=ProposedChange(
                intended_outcome=criterion,
                workflow_ref=_INTAKE_WORKFLOW,
                criteria=(
                    ProposedCriterion(
                        statement=criterion, observability=CriterionObservability.OBSERVED
                    ),
                ),
            ),
            kind=DraftKind.INTAKE,
            request={
                "pain_point": as_optional_str(row, "pain_point"),
                "messages": [str(item) for item in messages] if isinstance(messages, list) else [],
            },
            pinned_configuration_release_id=as_uuid(row, "pinned_configuration_release_id"),
        )


async def _insert_draft(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
    change: ProposedChange,
    kind: str,
    request: Mapping[str, JsonValue] | None,
    pinned_configuration_release_id: UUID,
) -> UUID:
    """Insert one draft with the fact that records it and the Job that writes it."""
    draft_id = uuid7()
    now = datetime.now(tz=UTC)
    scope: dict[str, JsonValue] = change.scope_column()
    if request is not None:
        scope["request"] = dict(request)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.prepared_drafts (
                id, client_id, product_signal_id, interview_session_id, state, kind,
                proposed_scope, criteria, proposal_digest
            )
            values (%s, %s, %s, %s, 'proposed', %s, %s, %s, %s)
            """,
            (
                draft_id,
                client_id,
                product_signal_id,
                interview_session_id,
                kind,
                Jsonb(scope),
                Jsonb([criterion.model_dump(mode="json") for criterion in change.criteria]),
                change.digest(),
            ),
        )
    stream = StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=product_signal_id)
    await append_fact(
        conn,
        client_id=client_id,
        event=EventAppend(
            message_id=uuid7(),
            stream=stream,
            event_type="change_proposed" if kind == DraftKind.PROPOSAL else "request_drafting_queued",
            payload={"prepared_draft_id": str(draft_id), "proposal_digest": change.digest()},
            actor=_ACTOR,
            causation_id=None,
            correlation_id=product_signal_id,
            occurred_at=now,
        ),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=stream,
            action="record_change_proposal" if kind == DraftKind.PROPOSAL else "record_intake_draft",
            outcome="drafting_queued",
            reason_code=None,
            actor=_ACTOR,
            pinned_configuration_release_id=pinned_configuration_release_id,
            detail={"criterion_count": len(change.criteria), "kind": kind},
        ),
        jobs=(
            JobIntent(
                job_id=uuid7(),
                job_type=PREPARE_DRAFT,
                input={
                    "product_signal_id": str(product_signal_id),
                    "prepared_draft_id": str(draft_id),
                },
                deadline_at=now + timedelta(seconds=_DRAFT_JOB_TIMEOUT_SECONDS),
                timeout_seconds=_DRAFT_JOB_TIMEOUT_SECONDS,
                max_runs=1,
                resource_budget={"kind": PREPARE_DRAFT},
                retryable_categories=[],
                backoff_base_seconds=5,
                backoff_max_seconds=30,
                concurrency_key=f"prepared_draft:{draft_id}",
                cancellation_scope_id=None,
                not_before=now,
            ),
        ),
    )
    _logger.info(
        "implementation.draft.queued",
        product_signal_id=str(product_signal_id),
        prepared_draft_id=str(draft_id),
        kind=kind,
    )
    return draft_id


async def prepare_draft(database: Database, claimed: ClaimedJob) -> None:
    """Write one draft in the case's prepared workspace, then fast-check it."""
    draft_id = UUID(str(claimed.input["prepared_draft_id"]))
    settings = get_settings()
    async with (
        VercelSandboxes(grant=sandbox_grant_from_environment()) as sandboxes,
        preview_session(database) as preview,
    ):
        draft = await _draft_row(database, draft_id=draft_id)
        artifacts = ArtifactStore(
            database=database,
            supabase_url=settings.supabase_url,
            service_key=settings.supabase_secret_key,
        )
        await draft_change(
            database,
            sandboxes,
            prepared_draft_id=draft_id,
            preview_runtime=PreviewCandidateDeployer(
                database=database,
                artifacts=artifacts,
                session=preview,
                interview_session_id=as_uuid(draft, "interview_session_id"),
            ),
            artifacts=artifacts,
            change_author=AgentAuthor,
            openai_key=openai_api_key(),
            prepare_gate=True,
        )


async def draft_change(
    database: Database,
    sandboxes: VercelSandboxes,
    *,
    prepared_draft_id: UUID,
    preview_runtime: PreviewRuntimePort,
    artifacts: ArtifactStore | None,
    change_author: Callable[..., ChangeAuthor],
    openai_key: str,
    prepare_gate: bool,
) -> None:
    """Write one draft in the case's prepared workspace and fast-check it.

    `change_author` builds the author from a model configuration and the draft's
    ledger, as `AgentAuthor` does. A proposal draft with `prepare_gate` prepares the
    gate's evaluation beside its Codex run; with `artifacts`, it also stores its
    artifacts and proves its spare for the Preview Version identifier it mints.
    """
    draft_id = prepared_draft_id
    draft = await _draft_row(database, draft_id=draft_id)
    if as_str(draft, "state") != "proposed":
        _logger.info(
            "implementation.draft.skipped",
            prepared_draft_id=str(draft_id),
            state=as_str(draft, "state"),
        )
        await _settle(database, draft_id=draft_id, consumed=None)
        return
    started = time.monotonic()
    kind = as_str(draft, "kind")
    product_signal_id = as_uuid(draft, "product_signal_id")
    release_id = await _pinned_release(database, draft=draft)
    policy = await load_implementation_policy(database, configuration_release_id=release_id)
    gate: asyncio.Task[None] | None = None
    if prepare_gate and kind == DraftKind.PROPOSAL:
        change = change_from_row(draft)
        gate = asyncio.create_task(
            prepare_gate_proposal(
                database,
                client_id=as_uuid(draft, "client_id"),
                product_signal_id=product_signal_id,
                prepared_draft_id=draft_id,
                criteria=tuple(
                    (criterion.statement, criterion.observability.value)
                    for criterion in change.criteria
                ),
            )
        )
    taken = await _take_workspace(database, draft_id=draft_id, draft=draft)
    if taken is None:
        await _settle_gate(gate, draft_id=draft_id)
        await _settle(database, draft_id=draft_id, consumed=None)
        return
    ledger = BudgetLedger(
        budgets=policy.budgets,
        already_consumed=ConsumedBudgets.empty(),
        started_at=datetime.now(tz=UTC),
    )
    adjust = False
    try:
        handle = await sandboxes.attach(name=taken.sandbox_name)
        await sandboxes.update_egress(
            handle,
            egress=working_egress(callback_hosts=policy.callback_hosts, openai_api_key=openai_key),
        )
        await _await_investigation(database, product_signal_id=product_signal_id)
        specification = _specification(
            draft=draft,
            policy=policy,
            release_id=release_id,
            findings=await _findings(database, product_signal_id=product_signal_id),
        )

        async def authority() -> None:
            await _require_current(
                database, draft_id=draft_id, workspace_id=taken.prepared_workspace_id
            )

        executor = ToolExecutor(
            database=database,
            sandboxes=sandboxes,
            handle=handle,
            specification=specification,
            fencing_token=specification.lease.fencing_token,
            budgets=ledger,
            workspace_root=WORKSPACE_ROOT,
            journal=[],
            authority=authority,
        )
        earlier_patch = await _intake_patch(database, earlier=taken.earlier_draft_id)
        adjust = earlier_patch is not None
        if taken.earlier_draft_id is not None and not adjust:
            await reset_to_base(executor, tree=taken.starting_tree)
        authored = await change_author(
            configuration=openai_configuration_for(OpenAIRole.IMPLEMENTATION), ledger=ledger
        ).author(
            executor=executor,
            specification=specification,
            feedback=(
                CycleFeedback(
                    cycle_index=0,
                    scope_violations=(),
                    new_check_failures=(),
                    adjustment=_ADJUSTMENT.format(patch=earlier_patch),
                )
                if earlier_patch is not None
                else None
            ),
        )
        if authored.needs_scope_change:
            await _fail(
                database,
                draft_id=draft_id,
                detail=f"the draft needs a different scope: {authored.scope_change_reason}",
            )
            return
        await format_changed_files(
            executor,
            command=policy.build_recipe.format_command,
            subject=f"prepared_draft:{draft_id}",
        )
        patch = await stage_and_diff(executor)
        files = await staged_files(executor)
        proposal = kind == DraftKind.PROPOSAL
        preview_version_id = uuid7() if proposal else None
        if adjust and taken.earlier_draft_id is not None:
            await _log_revision(
                database, draft_id=draft_id, earlier=taken.earlier_draft_id, files=files
            )
        stored, staged, runs, source_digest, dependencies = await asyncio.gather(
            _prestore(
                artifacts,
                draft=draft,
                base_revision=policy.base_revision,
                files=files,
                patch=patch,
                enabled=proposal,
            ),
            # An intake draft is built in the spare too, so a proposal that changes
            # little rebuilds little before the answer.
            _stage(
                preview_runtime,
                client_id=as_uuid(draft, "client_id"),
                files=files,
                preview_version_id=preview_version_id,
            ),
            run_required_checks(
                executor,
                checks=workspace_fast_checks(policy.required_checks),
                phase=CheckPhase.CANDIDATE,
                cycle_index=1,
            ),
            _tree_digest(executor),
            _dependency_identity(executor),
        )
        await _record_drafted(
            database,
            draft_id=draft_id,
            files=files,
            patch=patch,
            consumed=ledger.consumed(),
            runs=(
                *runs,
                *candidate_runtime_runs(
                    policy.required_checks,
                    build=None if staged is None else staged.build,
                    cycle_index=1,
                ),
            ),
            staged=staged,
            journal=executor.journal,
            source_digest=source_digest,
            dependencies=dependencies,
            artifacts=stored,
            preview_version_id=preview_version_id if staged is not None else None,
        )
        if staged is not None and staged.build.get("exit_code") == 0:
            # The session's standby spare builds these files too, so it already
            # serves the version this draft becomes once it is swapped in.
            async with database.transaction() as conn:
                await request_standby_spare(
                    conn,
                    client_id=as_uuid(draft, "client_id"),
                    product_signal_id=product_signal_id,
                    interview_session_id=as_uuid(draft, "interview_session_id"),
                )
    except LeaseLostError:
        _logger.info("implementation.draft.superseded", prepared_draft_id=str(draft_id))
    except (ConnectorFailure, BudgetExhaustedError, GuardRejectedError) as error:
        await _fail(database, draft_id=draft_id, detail=str(error))
    finally:
        await _settle_gate(gate, draft_id=draft_id)
        await _settle(database, draft_id=draft_id, consumed=ledger.consumed())
    _logger.info(
        "implementation.draft.finished",
        prepared_draft_id=str(draft_id),
        kind=kind,
        revised_intake_draft=adjust,
        seconds=round(time.monotonic() - started, 1),
    )


async def wait_until_settled(database: Database, *, prepared_workspace_id: UUID) -> UUID | None:
    """Wait until the workspace's latest draft no longer writes in it, and return its id.

    The wait is bounded: a draft Job that died never settles, and its sandbox is
    then taken as it stands.
    """
    deadline = time.monotonic() + _SETTLE_WAIT_SECONDS
    while True:
        async with database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select drafts.id, drafts.settled_at is not null as settled
                from public.prepared_workspaces as workspaces
                join public.prepared_drafts as drafts on drafts.id = workspaces.prepared_draft_id
                where workspaces.id = %s
                """,
                (prepared_workspace_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        if row["settled"] or time.monotonic() >= deadline:
            return as_uuid(row, "id")
        await asyncio.sleep(_POLL_SECONDS)


async def adoptable_draft(
    database: Database, *, specification: AttemptSpecification, prepared_workspace_id: UUID
) -> PreparedDraft | None:
    """The draft this attempt's approved agreement recorded, once it has settled.

    The draft must have been written in the workspace this attempt claimed; any
    other draft, or one that failed, leaves the attempt to author anew.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select prepared_draft_id from public.acceptance_criteria_agreements
            where product_signal_id = %s and acceptance_criteria_revision = %s
            """,
            (
                specification.product_signal_id,
                specification.approved_scope.acceptance_criteria_revision,
            ),
        )
        agreement = await cur.fetchone()
    agreed = None if agreement is None else as_optional_uuid(agreement, "prepared_draft_id")
    settled = await wait_until_settled(database, prepared_workspace_id=prepared_workspace_id)
    if agreed is None or settled != agreed:
        return None
    row = await _draft_row(database, draft_id=agreed)
    if (
        as_str(row, "state") != "drafted"
        or as_optional_uuid(row, "prepared_workspace_id") != prepared_workspace_id
    ):
        return None
    return _prepared(row)


async def adopt_draft(
    database: Database, *, prepared_draft_id: UUID, implementation_attempt_id: UUID
) -> bool:
    """Record that the attempt adopted the draft; refused when it is no longer drafted."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.prepared_drafts
            set state = 'adopted', implementation_attempt_id = %s, updated_at = now()
            where id = %s and state = 'drafted'
            """,
            (implementation_attempt_id, prepared_draft_id),
        )
        return cur.rowcount == 1


async def _take_workspace(
    database: Database, *, draft_id: UUID, draft: DictRow
) -> _TakenWorkspace | None:
    """Bind the case's newest prepared workspace to this draft, waiting until it is open.

    The earlier draft that wrote in it, if any, has settled before this one takes it.
    A proposal that takes over the intake draft retires it: its files stay on record
    for the proposal to revise. `None` fails the draft.
    """
    product_signal_id = as_uuid(draft, "product_signal_id")
    deadline = time.monotonic() + _WORKSPACE_WAIT_SECONDS
    while True:
        async with database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select id, state, prepared_draft_id, starting_tree
                from public.prepared_workspaces
                where product_signal_id = %s
                order by generation desc
                limit 1
                """,
                (product_signal_id,),
            )
            row = await cur.fetchone()
        state = None if row is None else as_str(row, "state")
        if row is None or state not in ("preparing", "ready", "drafting", "claimed"):
            await _fail(
                database,
                draft_id=draft_id,
                detail="no prepared workspace is free" if row is None else f"the workspace is {state}",
            )
            return None
        # An attempt that still holds the newest workspace hands it over, holding its
        # version's files, once that version's full checks pass.
        if state not in ("preparing", "claimed"):
            break
        if time.monotonic() >= deadline:
            await _fail(database, draft_id=draft_id, detail=f"the workspace was still {state}")
            return None
        await asyncio.sleep(_POLL_SECONDS)
    workspace_id = as_uuid(row, "id")
    earlier = as_optional_uuid(row, "prepared_draft_id")
    if earlier is not None:
        await wait_until_settled(database, prepared_workspace_id=workspace_id)
    # The draft is bound before the workspace, so one discarded while it waited
    # leaves the workspace with the draft it holds, for the next proposal to revise.
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select sandbox_name from public.prepared_workspaces
            where id = %s and state in ('ready', 'drafting')
            for update
            """,
            (workspace_id,),
        )
        taken = await cur.fetchone()
        if taken is not None:
            await cur.execute(
                """
                update public.prepared_drafts
                set state = 'drafting', prepared_workspace_id = %s, updated_at = now()
                where id = %s and state = 'proposed'
                """,
                (workspace_id, draft_id),
            )
            if cur.rowcount != 1:
                taken = None
        if taken is not None:
            await cur.execute(
                """
                update public.prepared_workspaces
                set state = 'drafting', prepared_draft_id = %s, updated_at = now()
                where id = %s
                """,
                (draft_id, workspace_id),
            )
            if earlier is not None and as_str(draft, "kind") == DraftKind.PROPOSAL:
                await cur.execute(
                    """
                    update public.prepared_drafts
                    set state = 'discarded', failure_detail = %s, updated_at = now()
                    where id = %s and kind = %s and state = any(%s)
                    """,
                    ("a proposal revised it", earlier, DraftKind.INTAKE, list(OPEN_DRAFT_STATES)),
                )
    if taken is None:
        await _fail(database, draft_id=draft_id, detail="the workspace was claimed meanwhile")
        return None
    return _TakenWorkspace(
        prepared_workspace_id=workspace_id,
        sandbox_name=as_str(taken, "sandbox_name"),
        earlier_draft_id=earlier,
        starting_tree=as_optional_str(row, "starting_tree"),
    )


async def _log_revision(
    database: Database, *, draft_id: UUID, earlier: UUID, files: Mapping[str, str]
) -> None:
    """Log whether revising the intake draft into the proposed change changed its files.

    Unchanged files mean the spare already serves a build of them, so the proposal
    is proven without building again.
    """
    row = await _draft_row(database, draft_id=earlier)
    before = {str(path): str(content) for path, content in _mapping(row["files"]).items()}
    changed = sorted(
        path for path in set(before) | set(files) if before.get(path) != files.get(path)
    )
    _logger.info(
        "implementation.draft.revised_intake_draft",
        prepared_draft_id=str(draft_id),
        intake_draft_id=str(earlier),
        files_changed=bool(changed),
        changed_paths=changed,
    )


async def _intake_patch(database: Database, *, earlier: UUID | None) -> str | None:
    """The patch of a finished intake draft the workspace holds, which this draft revises."""
    if earlier is None:
        return None
    row = await _draft_row(database, draft_id=earlier)
    if as_str(row, "kind") != DraftKind.INTAKE or row["files_digest"] is None:
        return None
    return as_optional_str(row, "patch")


async def _await_investigation(database: Database, *, product_signal_id: UUID) -> None:
    """Wait, within a bound, for an early code investigation still running for the case.

    Its findings name the exact files the change bears on, which the draft's task then
    carries in full instead of the agent reading them itself.
    """
    deadline = time.monotonic() + _INVESTIGATION_WAIT_SECONDS
    while time.monotonic() < deadline:
        async with database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select exists (
                  select 1 from public.code_investigation_findings where product_signal_id = %s
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
        if row is None or row["found"] or not row["running"]:
            return
        await asyncio.sleep(_POLL_SECONDS)


async def _require_current(database: Database, *, draft_id: UUID, workspace_id: UUID) -> None:
    """Refuse a draft's next call once it is discarded or the workspace moved on."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select drafts.state as draft_state, workspaces.state as workspace_state,
                   workspaces.prepared_draft_id
            from public.prepared_drafts as drafts
            join public.prepared_workspaces as workspaces on workspaces.id = %s
            where drafts.id = %s
            """,
            (workspace_id, draft_id),
        )
        row = await cur.fetchone()
    if (
        row is None
        or as_str(row, "draft_state") != "drafting"
        or as_optional_uuid(row, "prepared_draft_id") != draft_id
        or as_str(row, "workspace_state") not in ("drafting", "claimed")
    ):
        raise LeaseLostError(lease_kind="prepared_draft", lease_id=draft_id, fencing_token=1)


async def _prestore(
    artifacts: ArtifactStore | None,
    *,
    draft: DictRow,
    base_revision: str,
    files: Mapping[str, str],
    patch: str,
    enabled: bool,
) -> tuple[ArtifactReference, ...]:
    if artifacts is None or not enabled:
        return ()
    return await prestore_draft_artifacts(
        artifacts,
        client_id=as_uuid(draft, "client_id"),
        product_signal_id=as_uuid(draft, "product_signal_id"),
        base_revision=base_revision,
        files=files,
        patch=patch,
    )


async def _stage(
    preview_runtime: PreviewRuntimePort,
    *,
    client_id: UUID,
    files: Mapping[str, str],
    preview_version_id: UUID | None,
) -> StagedCandidate | None:
    """Stage a draft in the session's spare, build it there, and prove a proposal's.

    A staging that fails leaves the draft without a staged candidate; the attempt
    that adopts it then stages, builds, and proves the files itself.
    """
    try:
        return await preview_runtime.stage_candidate(
            client_id=client_id, files=files, preview_version_id=preview_version_id
        )
    except (ConnectorFailure, GuardRejectedError) as error:
        _logger.warning(
            "implementation.draft.not_staged",
            preview_version_id=None if preview_version_id is None else str(preview_version_id),
            error=str(error)[:500],
        )
        return None


async def _tree_digest(executor: ToolExecutor) -> str:
    """The staged tree's own git object identity, which the Preview Version records."""
    return (
        await inspect_workspace(executor, operation="draft_source_digest", command="git write-tree")
    ).strip()


async def _dependency_identity(executor: ToolExecutor) -> tuple[DependencyLockIdentity, str]:
    """The lock identity the version records, and the manifest its artifact holds."""
    manifest, lockfile = await asyncio.gather(
        executor.execute(ReadFile(tool=ToolName.READ_FILE, path="package.json")),
        executor.execute(ReadFile(tool=ToolName.READ_FILE, path="package-lock.json")),
    )
    identity = DependencyLockIdentity(
        lockfile_path="package-lock.json",
        lockfile_digest=_body_digest(lockfile.output),
        manifest_digest=_body_digest(manifest.output),
    )
    return identity, manifest.output


async def _record_drafted(
    database: Database,
    *,
    draft_id: UUID,
    files: Mapping[str, str],
    patch: str,
    consumed: ConsumedBudgets,
    runs: Sequence[CheckRun],
    staged: StagedCandidate | None,
    journal: Sequence[ToolJournalEntry],
    source_digest: str,
    dependencies: tuple[DependencyLockIdentity, str],
    artifacts: Sequence[ArtifactReference],
    preview_version_id: UUID | None,
) -> None:
    fast_checks: dict[str, JsonValue] = {
        "journal": [{"tool": entry.tool.value, "subject": entry.subject} for entry in journal],
        "runs": [run.model_dump(mode="json") for run in runs],
    }
    if staged is not None:
        fast_checks.update(
            {
                "render_passed": staged.render_passed,
                "render": json.loads(json.dumps(dict(staged.render))),
                "build": json.loads(json.dumps(dict(staged.build))),
                "file_digests": dict(staged.file_digests),
            }
        )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.prepared_drafts
            set state = 'drafted', files = %s, files_digest = %s, patch = %s,
                consumed_budgets = %s, agent_cost_cents = %s, fast_checks = %s,
                staged_runtime_ref = %s, source_digest = %s, dependencies = %s,
                artifacts = %s, preview_version_id = %s, updated_at = now()
            where id = %s and state = 'drafting'
            """,
            (
                Jsonb(dict(files)),
                files_digest(files),
                patch,
                Jsonb(consumed.as_column()),
                consumed.agent_cost_cents,
                Jsonb(fast_checks),
                None if staged is None else staged.runtime_ref,
                source_digest,
                Jsonb({"identity": dependencies[0].model_dump(mode="json"), "manifest": dependencies[1]}),
                Jsonb([reference.model_dump(mode="json") for reference in artifacts]),
                preview_version_id,
                draft_id,
            ),
        )
        recorded = cur.rowcount == 1
    _logger.info(
        "implementation.draft.drafted",
        prepared_draft_id=str(draft_id),
        recorded=recorded,
        files=len(files),
        fast_checks=[f"{run.name}={run.exit_code}" for run in runs],
        render_passed=None if staged is None else staged.render_passed,
        build=None if staged is None else {
            key: staged.build.get(key) for key in ("exit_code", "seconds", "reused", "restart", "restart_seconds")
        },
        proven_for=None if preview_version_id is None else str(preview_version_id),
    )


def files_digest(files: Mapping[str, str]) -> str:
    """One digest over a set of files: each path with the digest of its exact bytes."""
    return "sha256:" + payload_digest(
        {
            path: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for path, content in sorted(files.items())
        }
    )


async def _settle_gate(gate: asyncio.Task[None] | None, *, draft_id: UUID) -> None:
    """Wait for the gate's preparation; a failed one only means the gate asks afresh."""
    if gate is None:
        return
    try:
        await gate
    except Exception as error:  # noqa: BLE001 - any failure leaves the ordinary gate path
        _logger.warning(
            "implementation.draft.gate_not_prepared",
            prepared_draft_id=str(draft_id),
            error=f"{type(error).__name__}: {error}",
        )


async def _fail(database: Database, *, draft_id: UUID, detail: str) -> None:
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.prepared_drafts
            set state = 'failed', failure_detail = %s, updated_at = now()
            where id = %s and state in ('proposed', 'drafting')
            """,
            (detail[:2000], draft_id),
        )
    _logger.info("implementation.draft.failed", prepared_draft_id=str(draft_id), detail=detail[:500])


async def _settle(database: Database, *, draft_id: UUID, consumed: ConsumedBudgets | None) -> None:
    """Record that the draft stopped writing, and what it spent, whatever became of it."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.prepared_drafts
            set settled_at = coalesce(settled_at, now()),
                consumed_budgets = coalesce(%s, consumed_budgets),
                agent_cost_cents = greatest(agent_cost_cents, %s)
            where id = %s
            """,
            (
                None if consumed is None else Jsonb(consumed.as_column()),
                0 if consumed is None else consumed.agent_cost_cents,
                draft_id,
            ),
        )


async def _lock_signal_state(conn: AsyncConnection[DictRow], *, product_signal_id: UUID) -> str:
    async with conn.cursor() as cur:
        await cur.execute(
            "select state::text as state from public.product_signals where id = %s for update",
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="product_signals", row_id=product_signal_id)
    return as_str(row, "state")


async def _draft_row(database: Database, *, draft_id: UUID) -> DictRow:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute("select * from public.prepared_drafts where id = %s", (draft_id,))
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="prepared_drafts", row_id=draft_id)
    return row


async def _pinned_release(database: Database, *, draft: DictRow) -> UUID:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select pinned_configuration_release_id from public.product_signals where id = %s",
            (as_uuid(draft, "product_signal_id"),),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="product_signals", row_id=as_uuid(draft, "product_signal_id")
        )
    return as_uuid(row, "pinned_configuration_release_id")


async def _findings(database: Database, *, product_signal_id: UUID) -> tuple[CodeFindingHint, ...]:
    """The findings the investigation that ran during the interview recorded."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select path, first_line, last_line, note from public.code_investigation_findings
            where product_signal_id = %s order by path, first_line
            """,
            (product_signal_id,),
        )
        rows = await cur.fetchall()
    return tuple(
        CodeFindingHint(
            path=as_str(row, "path"),
            first_line=as_int(row, "first_line"),
            last_line=as_int(row, "last_line"),
            note=as_str(row, "note"),
        )
        for row in rows
    )


def _specification(
    *,
    draft: DictRow,
    policy: ImplementationPolicy,
    release_id: UUID,
    findings: tuple[CodeFindingHint, ...],
) -> AttemptSpecification:
    """The provisional specification a draft is authored under.

    It carries the proposal, or the original request, where an attempt carries the
    gate's frozen scope, and the policy's own paths, checks, and budgets. Its
    identities are the draft's: no attempt, gate decision, or lease exists yet, and
    nothing it names grants anything.
    """
    change = change_from_row(draft)
    draft_id = as_uuid(draft, "id")
    scope = draft["proposed_scope"]
    request = scope.get("request") if isinstance(scope, dict) else None
    in_scope = [change.intended_outcome]
    if isinstance(request, dict):
        if isinstance(request.get("pain_point"), str):
            in_scope.append(f"Relieve this: {request['pain_point']}")
        messages = request.get("messages")
        if isinstance(messages, list):
            in_scope.extend(f"The customer asked: {message}" for message in messages)
    approved = ApprovedScope(
        intended_outcome=change.intended_outcome,
        acceptance_criteria=tuple(
            AcceptanceCriterion(
                criterion_id=f"proposed-{index + 1}",
                ordinal=index,
                statement=criterion.statement,
                observability=criterion.observability.value,
            )
            for index, criterion in enumerate(change.criteria)
        ),
        acceptance_criteria_revision=0,
        classification_revision=0,
        evidence_revision=0,
        repository_ref=policy.repository_ref,
        base_revision=policy.base_revision,
        in_scope_changes=tuple(in_scope),
        excluded_changes=(),
        permitted_paths=policy.permitted_paths,
        blocked_paths=policy.blocked_paths,
        required_checks=policy.required_checks,
        dependency_policy=policy.dependency_policy,
        permitted_dependencies=policy.permitted_dependencies,
        dependency_manifest_paths=policy.dependency_manifest_paths,
        budgets=policy.budgets,
    )
    return AttemptSpecification(
        client_id=as_uuid(draft, "client_id"),
        product_signal_id=as_uuid(draft, "product_signal_id"),
        implementation_attempt_id=draft_id,
        gate_decision_id=draft_id,
        interview_session_id=as_uuid(draft, "interview_session_id"),
        pinned_configuration_release_id=release_id,
        checkout=RepositoryCheckout(
            repository_ref=policy.repository_ref,
            clone_url=policy.clone_url,
            base_revision=policy.base_revision,
            workspace_root=WORKSPACE_ROOT,
            application_port=policy.build_recipe.application_port,
        ),
        approved_scope=approved,
        lease=LeaseBinding(lease_id=draft_id, fencing_token=1),
        workspace_snapshot_id=policy.workspace_snapshot_id,
        context_bundle_refs=(f"prepared_draft:{draft_id}",),
        code_findings=findings,
        callback_hosts=policy.callback_hosts,
    )


def _prepared(row: DictRow) -> PreparedDraft:
    files = _mapping(row["files"])
    fast = _mapping(row["fast_checks"])
    runs = fast.get("runs")
    journal = fast.get("journal")
    staged_ref = row["staged_runtime_ref"]
    consumed = row["consumed_budgets"]
    dependencies = row["dependencies"]
    return PreparedDraft(
        prepared_draft_id=as_uuid(row, "id"),
        state=as_str(row, "state"),
        kind=as_str(row, "kind"),
        prepared_workspace_id=as_optional_uuid(row, "prepared_workspace_id"),
        change=change_from_row(row),
        files={str(path): str(content) for path, content in files.items()},
        files_digest=as_optional_str(row, "files_digest"),
        patch=as_optional_str(row, "patch") or "",
        source_digest=as_optional_str(row, "source_digest"),
        dependencies=(
            DependencyLockIdentity.model_validate(dependencies["identity"])
            if isinstance(dependencies, dict) and isinstance(dependencies.get("identity"), dict)
            else None
        ),
        dependency_manifest=(
            str(dependencies["manifest"])
            if isinstance(dependencies, dict) and isinstance(dependencies.get("manifest"), str)
            else None
        ),
        preview_version_id=as_optional_uuid(row, "preview_version_id"),
        consumed=(
            ConsumedBudgets.from_column(consumed)
            if isinstance(consumed, dict)
            else ConsumedBudgets.empty()
        ),
        fast_checks=tuple(
            CheckRun.model_validate(item) for item in (runs if isinstance(runs, list) else [])
        ),
        staged=None
        if not isinstance(staged_ref, str)
        else StagedCandidate(
            runtime_ref=staged_ref,
            file_digests={
                str(path): str(digest)
                for path, digest in _mapping(fast.get("file_digests")).items()
            },
            render_passed=bool(fast.get("render_passed")),
            render=_mapping(fast.get("render")),
            build=_mapping(fast.get("build")),
        ),
        journal=tuple(
            (ToolName(str(entry["tool"])), str(entry["subject"]))
            for entry in (journal if isinstance(journal, list) else [])
            if isinstance(entry, dict)
        ),
    )


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def _body_digest(body: str) -> str:
    return f"sha256:{hashlib.sha256(body.encode('utf-8')).hexdigest()}"
