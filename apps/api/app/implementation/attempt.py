"""The Implementation Attempt runner.

One run takes an authorized attempt from a fenced, empty workspace to an
accepted Preview Version, or to one of the typed outcomes that is not a
candidate. It claims the workspace prepared for the case while the interview ran
(`app.implementation.prepared`), already checked out at the approved base revision
with its dependencies installed and the baseline checks run, and starts the first
cycle at once. With none to claim, it creates the workspace from the pinned
snapshot, checks the repository out, and exports the clean base beside the
checkout; the first cycle is then authored while the dependencies install and the
baseline checks run. Either way it repeats: author a cycle, measure the diff
against the frozen approved scope, run the required checks again, and compare
against the baseline. While a cycle's checks run, the same staged files are
written into the session's spare candidate runtime, which builds them for
production, and pass its render check there, so an accepted candidate only has to
be proven ready and routed. That build is the run of every required check the
policy has the candidate runtime run, and a failed build or render check is a new
failure like any check's. A correction cycle stays inside
the attempt and consumes no revision, only budget.

A prepared draft the approved agreement recorded is the first cycle's change. Its
record holds everything publication needs: the patch, the files and their tree,
the dependency identity, the fast and render checks, the artifacts already
stored, and a spare proven ready for the version identifier it minted. So the
attempt publishes it from the record while it attaches the claimed sandbox, and
the participant's preview switches before the workspace is open; the full checks
then run in the workspace once its tree is shown to be the published one. Anything
in the record that does not hold leaves the first cycle to check the draft in the
workspace as it would its own change.

An attempt whose version passed its full checks hands its workspace over as the
case's next prepared workspace while the case may still use another attempt, so a
change the participant asks for while trying the version is drafted in it ahead.

Artifacts are extracted before the workspace is destroyed, and extraction
failure blocks cleanup: the sandbox is never the sole recovery record. The runner
therefore destroys a workspace only after it holds a complete manifest, or after
it has recorded that it could not build one.

A workspace the attempt creates is named for the attempt and its lease's fencing
token, and a claimed one is bound to both in the same row update, so the sandbox a
superseded lease took is never the one the current lease drives. A workspace is
checked out behind an egress policy that adds the repository token at the
firewall; once the checkout exists, the policy is replaced by one that no longer
reaches GitHub.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

import structlog
from utrace_contracts.domain.lifecycle import (
    ImplementationAttemptState,
    ImplementationProgress,
)

from app.connectors.failures import (
    ConnectorFailure,
    EffectState,
    FailureDetail,
    RetryAdvice,
    TechnicalCategory,
)
from app.connectors.vercel_sandboxes import SandboxHandle, VercelSandboxes
from app.db.pool import Database
from app.errors import BudgetExhaustedError, GuardRejectedError, LeaseLostError
from app.implementation.agent_author import ChangeAuthor, CycleFeedback
from app.implementation.artifact_store import ArtifactStore
from app.implementation.artifacts import (
    ArtifactManifest,
    AttemptEvidence,
    CycleRecord,
    extract_attempt_artifacts,
    record_cycle_evidence,
    scope_summary,
)
from app.implementation.budgets import BudgetLedger, ConsumedBudgets
from app.implementation.checks import (
    LOG_DIRECTORY,
    CheckComparison,
    CheckPhase,
    CheckRun,
    candidate_runtime_runs,
    compare_checks,
    run_required_checks,
    workspace_fast_checks,
)
from app.implementation.cycle import (
    GIT_TIMEOUT_SECONDS,
    format_changed_files,
    inspect_workspace,
    reset_to_base,
    stage_and_diff,
    staged_files,
)
from app.implementation.drafts import (
    PreparedDraft,
    adopt_draft,
    adoptable_draft,
    files_digest,
    wait_until_settled,
)
from app.implementation.environment import (
    PLACEHOLDER_API_KEY,
    WORKSPACE_VCPUS,
    checkout_egress,
    working_egress,
)
from app.implementation.network import UNSAFE_VIOLATION_THRESHOLD, scan_commands_for_egress
from app.implementation.ports import (
    BuildRecipe,
    CandidateHandoff,
    DependencyLockIdentity,
    PreviewRuntimePort,
    StagedCandidate,
)
from app.implementation.prepared import (
    WORKSPACE_WINDOW_SECONDS,
    ClaimedWorkspace,
    claim_prepared_workspace,
    hand_over_workspace,
    pins_for_attempt,
    release_claimed_workspace,
)
from app.implementation.preview_version import (
    PublicationRefusedError,
    previous_version,
    publish_preview_version,
    record_full_checks,
    replace_validated_version,
)
from app.implementation.progress import record_progress
from app.implementation.scope_check import (
    added_manifest_lines,
    check_diff_against_scope,
)
from app.implementation.specification import AttemptSpecification
from app.implementation.state import AttemptCheckpoint, current_attempt, write_checkpoint
from app.implementation.tools import (
    ReadFile,
    RunProcess,
    ToolExecutor,
    ToolJournalEntry,
    ToolName,
    WriteFile,
)
from app.implementation.workspace import checkout_repository, create_workspace
from app.implementation.workspace_setup import (
    exclude_check_logs,
    export_clean_base,
    install_dependencies,
    run_baseline,
)
from app.preview.artifacts import load_artifact

__all__ = ["AttemptRunResult", "AttemptRunner", "WorkspaceDependencies"]

_logger = structlog.get_logger(__name__)

RENDER_CHECK = "render"
"""The check name a failed render check is reported under, beside the required checks."""

_STOP_STATES = frozenset(
    {
        ImplementationAttemptState.PAUSED,
        ImplementationAttemptState.CANCELED,
    }
)


@dataclass(frozen=True, slots=True)
class WorkspaceDependencies:
    """The external systems one run touches. Everything else is durable state."""

    sandboxes: VercelSandboxes
    artifacts: ArtifactStore
    preview_runtime: PreviewRuntimePort
    change_author: ChangeAuthor
    candidate_ready: Callable[[CandidateHandoff], Awaitable[None]]
    """Commits `candidate_ready` once the first version serves the participant."""


@dataclass(frozen=True, slots=True)
class AttemptRunResult:
    """One run's typed outcome and everything it produced."""

    outcome: ImplementationAttemptState
    reason: str | None
    handoff: CandidateHandoff | None
    manifest: ArtifactManifest | None
    consumed: ConsumedBudgets
    checkpoint: AttemptCheckpoint
    workspace_destroyed: bool
    """Whether the attempt no longer holds its workspace: destroyed, or handed over."""
    full_checks_passed: bool
    """Whether the newest published version passed every required check.

    A `candidate_ready` attempt committed that outcome when its first version was
    published; one whose version still fails its full checks cannot be delivered."""


@dataclass(frozen=True, slots=True)
class _VersionSource:
    """The files one version publishes, and the identities that bind them."""

    files: Mapping[str, str]
    source_digest: str
    dependencies: DependencyLockIdentity
    dependency_manifest: str
    preview_version_id: UUID | None
    """The identifier a prepared draft's spare was proven ready for, when it was."""


class AttemptRunner:
    """Runs one Implementation Attempt to a typed outcome."""

    def __init__(
        self,
        *,
        database: Database,
        dependencies: WorkspaceDependencies,
        specification: AttemptSpecification,
        build_recipe: BuildRecipe,
        repository_access_token: str | Callable[[], Awaitable[str]],
        openai_api_key: str,
        ledger: BudgetLedger,
        implementation_limit: int = 1,
    ) -> None:
        """`repository_access_token` may be a callable, which a run calls only when it
        has to check the repository out itself. `implementation_limit` is how many
        attempts the case's pinned policy allows, which decides whether a finished
        workspace is handed over to the next."""
        self._database = database
        self._dependencies = dependencies
        self._specification = specification
        self._build_recipe = build_recipe
        self._access_token = repository_access_token
        self._openai_api_key = openai_api_key
        self._ledger = ledger
        self._implementation_limit = implementation_limit
        self._journal: list[ToolJournalEntry] = []
        self._previous: UUID | None = None
        """The case's ready version from an earlier attempt, which this attempt changes."""
        self._published_draft = False
        """Whether the adopted draft was published from its record, before the cycles."""
        self._cycles: list[CycleRecord] = []
        self._baseline: tuple[CheckRun, ...] = ()
        self._check_logs: dict[str, str] = {}
        self._inspected: list[dict[str, object]] = []
        self._prepared_workspace_id: UUID | None = None
        self._staging: asyncio.Task[StagedCandidate | None] | None = None
        self._staged: StagedCandidate | None = None
        self._draft: PreparedDraft | None = None
        self._handoff: CandidateHandoff | None = None
        self._manifest: ArtifactManifest | None = None
        self._recorded_cycles = 0
        """How many cycles' evidence is durable."""
        self._baseline_recorded = False

    async def run(self) -> AttemptRunResult:
        specification = self._specification
        ledger = self._ledger
        _, prepared, self._previous = await asyncio.gather(
            self._progress(ImplementationProgress.REPRODUCTION_CAPTURED),
            claim_prepared_workspace(
                self._database,
                specification=specification,
                pins=pins_for_attempt(specification, build_recipe=self._build_recipe),
            ),
            previous_version(
                self._database,
                product_signal_id=specification.product_signal_id,
                implementation_attempt_id=specification.implementation_attempt_id,
            ),
        )
        if prepared is None:
            handle = await create_workspace(
                self._dependencies.sandboxes,
                name=(
                    f"utrace-attempt-{specification.implementation_attempt_id.hex}-"
                    f"{specification.lease.fencing_token}"
                ),
                snapshot_id=specification.workspace_snapshot_id,
                egress=checkout_egress(
                    callback_hosts=specification.callback_hosts,
                    openai_api_key=self._openai_api_key,
                    repository_token=await self._repository_token(),
                ),
                env={"CODEX_API_KEY": PLACEHOLDER_API_KEY, "CI": "1"},
                execution_time_limit_seconds=specification.approved_scope.budgets.elapsed_seconds,
                vcpus=WORKSPACE_VCPUS,
            )
        else:
            self._prepared_workspace_id = prepared.prepared_workspace_id
            handle = await self._open_claimed(prepared, ledger=ledger)
        executor = ToolExecutor(
            database=self._database,
            sandboxes=self._dependencies.sandboxes,
            handle=handle,
            specification=specification,
            fencing_token=specification.lease.fencing_token,
            budgets=ledger,
            workspace_root=specification.checkout.workspace_root,
            journal=self._journal,
        )
        try:
            return await self._execute(
                handle=handle, executor=executor, ledger=ledger, prepared=prepared
            )
        finally:
            if self._staging is not None:
                await _settle_background(self._staging)
            _logger.info(
                "implementation.attempt.finished",
                attempt_id=str(specification.implementation_attempt_id),
                sandbox=handle.name,
            )

    async def _execute(
        self,
        *,
        handle: SandboxHandle,
        executor: ToolExecutor,
        ledger: BudgetLedger,
        prepared: ClaimedWorkspace | None,
    ) -> AttemptRunResult:
        """Drive the cycles, publish, and clean up, mapping every failure to an outcome."""
        try:
            if prepared is None:
                install, baseline = await self._prepare_here(handle=handle, executor=executor)
            else:
                install, baseline = await self._adopt(prepared, handle=handle)
                if self._draft is None:
                    await self._clear_workspace(executor, prepared=prepared)
            if prepared is None or prepared.preview_version_id != self._previous:
                await self._carry_forward(executor)
            try:
                outcome, reason = await self._run_cycles(
                    executor=executor, ledger=ledger, install=install, baseline=baseline
                )
                await self._baseline_runs(baseline)
            finally:
                await _settle_background(baseline)
                await _settle_background(install)
        except BudgetExhaustedError as error:
            outcome, reason = ImplementationAttemptState.BUDGET_EXHAUSTED, str(error)
            _logger.warning(
                "implementation.attempt.budget_exhausted",
                attempt_id=str(self._specification.implementation_attempt_id),
                reason=reason,
            )
        except LeaseLostError:
            # A stale run publishes nothing and destroys nothing: the current lease
            # holder owns the workspace and its artifacts.
            raise
        except ConnectorFailure as failure:
            if failure.detail.retry_advice is RetryAdvice.NEVER:
                raise
            outcome, reason = ImplementationAttemptState.INFRASTRUCTURE_BLOCKED, failure.detail.message
            _logger.warning(
                "implementation.attempt.infrastructure_blocked",
                attempt_id=str(self._specification.implementation_attempt_id),
                operation=failure.detail.operation,
                reason=reason[:500],
            )

        return await self._settle(
            handle=handle, executor=executor, ledger=ledger, outcome=outcome, reason=reason
        )

    async def _prepare_here(
        self, *, handle: SandboxHandle, executor: ToolExecutor
    ) -> tuple[asyncio.Task[None], asyncio.Task[tuple[CheckRun, ...]]]:
        """Check out a workspace the attempt created, and start its install and baseline.

        Authoring starts as soon as the checkout exists; the install and then the
        baseline run beside it.
        """
        specification = self._specification
        await checkout_repository(
            self._dependencies.sandboxes,
            handle,
            clone_url=specification.checkout.clone_url,
            base_revision=specification.checkout.base_revision,
            workspace_root=specification.checkout.workspace_root,
        )
        await self._dependencies.sandboxes.update_egress(
            handle,
            egress=working_egress(
                callback_hosts=specification.callback_hosts, openai_api_key=self._openai_api_key
            ),
        )
        await exclude_check_logs(executor)
        await export_clean_base(executor)
        await self._progress(ImplementationProgress.MAKING_CHANGES)
        install = asyncio.create_task(
            install_dependencies(executor, install_command=self._build_recipe.install_command)
        )
        return install, asyncio.create_task(self._baseline_after(executor, install=install))

    async def _baseline_after(
        self, executor: ToolExecutor, *, install: asyncio.Task[None]
    ) -> tuple[CheckRun, ...]:
        """Run the required checks on the clean base once the dependencies exist."""
        await install
        return await run_baseline(
            executor, checks=self._specification.approved_scope.required_checks
        )

    async def _adopt(
        self, prepared: ClaimedWorkspace, *, handle: SandboxHandle
    ) -> tuple[asyncio.Task[None], asyncio.Task[tuple[CheckRun, ...]]]:
        """Open a claimed workspace to the agent and give it the whole attempt budget.

        Its dependencies and its baseline already exist, so both are settled.
        """
        specification = self._specification
        sandboxes = self._dependencies.sandboxes
        # A workspace a draft wrote in already reaches the model.
        if prepared.prepared_draft_id is None:
            await sandboxes.update_egress(
                handle,
                egress=working_egress(
                    callback_hosts=specification.callback_hosts,
                    openai_api_key=self._openai_api_key,
                ),
            )
        steps = [
            sandboxes.extend_execution_time(
                handle, seconds=specification.approved_scope.budgets.elapsed_seconds
            )
        ]
        # A draft already published is past making changes.
        if not self._published_draft:
            steps.append(self._progress(ImplementationProgress.MAKING_CHANGES))
        await asyncio.gather(*steps)
        return asyncio.create_task(_settled(None)), asyncio.create_task(
            _settled(prepared.baseline)
        )

    async def _open_claimed(self, prepared: ClaimedWorkspace, *, ledger: BudgetLedger) -> SandboxHandle:
        """Attach the claimed sandbox while the draft it holds is adopted and published.

        Publishing an adopted draft needs only its record, so the participant's
        preview switches while the sandbox is still being reached.
        """
        attaching = asyncio.create_task(
            self._dependencies.sandboxes.attach(name=prepared.sandbox_name)
        )
        started = time.monotonic()
        try:
            if await self._take_draft(prepared):
                self._published_draft = await self._publish_draft(prepared, ledger=ledger)
                _logger.info(
                    "implementation.attempt.draft_publication",
                    attempt_id=str(self._specification.implementation_attempt_id),
                    published=self._published_draft,
                    seconds=round(time.monotonic() - started, 3),
                )
        except BaseException:
            await _settle_background(attaching)
            raise
        return await attaching

    async def _take_draft(self, prepared: ClaimedWorkspace) -> bool:
        """Adopt the prepared draft the approved agreement recorded, when it fits.

        The draft is the first cycle's authored change, and what it spent is the
        attempt's. It fits only when it started from the files this attempt starts
        from: the clean base for the case's first attempt, or the version this
        attempt changes, for a workspace that version's attempt handed over.
        """
        if prepared.prepared_draft_id is None or prepared.preview_version_id != self._previous:
            return False
        specification = self._specification
        draft = await adoptable_draft(
            self._database,
            specification=specification,
            prepared_workspace_id=prepared.prepared_workspace_id,
        )
        if draft is None or not await adopt_draft(
            self._database,
            prepared_draft_id=draft.prepared_draft_id,
            implementation_attempt_id=specification.implementation_attempt_id,
        ):
            return False
        self._draft = draft
        self._ledger.charge_prepared_draft(draft.consumed)
        adopted_at = datetime.now(tz=UTC)
        self._journal.extend(
            ToolJournalEntry(
                sequence=sequence,
                occurred_at=adopted_at,
                tool=tool,
                subject=subject,
                exit_code=None,
                output_bytes=0,
            )
            for sequence, (tool, subject) in enumerate(draft.journal, start=1)
        )
        _logger.info(
            "implementation.attempt.adopted_draft",
            attempt_id=str(specification.implementation_attempt_id),
            prepared_draft_id=str(draft.prepared_draft_id),
        )
        return True

    async def _publish_draft(self, prepared: ClaimedWorkspace, *, ledger: BudgetLedger) -> bool:
        """Publish the adopted draft from its record, as the first cycle's version.

        The draft's patch passes the scope and egress checks a cycle's would, against
        the frozen approved scope, and its recorded fast and render checks must be
        exactly this scope's fast checks on exactly its files. A record that does
        not hold, a refusal, or a deployment that fails leaves the first cycle to
        check the draft in the workspace instead.
        """
        draft = self._draft
        scope = self._specification.approved_scope
        if (
            draft is None
            or draft.staged is None
            or draft.source_digest is None
            or draft.dependencies is None
            or draft.dependency_manifest is None
            or draft.preview_version_id is None
            or draft.files_digest != files_digest(draft.files)
            or {(run.name, run.command) for run in draft.fast_checks}
            != {(check.name, check.command) for check in scope.required_checks if check.fast}
        ):
            return False
        scope_check = check_diff_against_scope(
            diff=draft.patch,
            scope=scope,
            added_dependency_lines=added_manifest_lines(draft.patch, scope),
        )
        if not scope_check.passed or self._unsafe_egress() is not None:
            return False
        candidate = (*draft.fast_checks, _render_run(draft.staged, cycle_index=1))
        if not compare_checks(baseline=prepared.baseline, candidate=candidate).acceptable:
            return False
        self._baseline = prepared.baseline
        self._capture_logs((*self._baseline, *candidate))
        self._staged = draft.staged
        self._cycles.append(
            CycleRecord(cycle_index=1, patch=draft.patch, scope_check=scope_check, check_runs=candidate)
        )
        self._inspected.extend(
            {
                "path": path,
                "base_revision": self._specification.checkout.base_revision,
                "digest": digest,
            }
            for path, digest in (
                ("package.json", draft.dependencies.manifest_digest),
                (draft.dependencies.lockfile_path, draft.dependencies.lockfile_digest),
            )
        )
        source = _VersionSource(
            files=draft.files,
            source_digest=draft.source_digest,
            dependencies=draft.dependencies,
            dependency_manifest=draft.dependency_manifest,
            preview_version_id=draft.preview_version_id,
        )
        try:
            refusal = await self._publish(source=source, ledger=ledger)
        except (ConnectorFailure, GuardRejectedError) as error:
            never = isinstance(error, ConnectorFailure) and (
                error.detail.retry_advice is RetryAdvice.NEVER
            )
            # Once the version serves the participant, a failure after it is the run's.
            if never or self._handoff is not None:
                raise
            refusal = str(error)
        if refusal is None:
            return True
        _logger.warning(
            "implementation.attempt.draft_not_published",
            attempt_id=str(self._specification.implementation_attempt_id),
            prepared_draft_id=str(draft.prepared_draft_id),
            reason=refusal,
        )
        self._cycles.clear()
        self._baseline = ()
        self._staged = None
        self._check_logs.clear()
        self._inspected.clear()
        return False

    async def _clear_workspace(self, executor: ToolExecutor, *, prepared: ClaimedWorkspace) -> None:
        """Put a claimed workspace whose draft was not adopted back at its starting files.

        A draft that wrote in it is waited for until it stopped writing. A workspace
        that started from other files than this attempt's goes back to the clean
        base, and the version this attempt changes is then carried forward into it.
        """
        if prepared.prepared_draft_id is not None:
            await wait_until_settled(
                self._database, prepared_workspace_id=prepared.prepared_workspace_id
            )
        if prepared.preview_version_id != self._previous:
            if prepared.prepared_draft_id is None and prepared.starting_tree is None:
                return
            await reset_to_base(executor)
        elif prepared.prepared_draft_id is not None:
            await reset_to_base(executor, tree=prepared.starting_tree)
        else:
            return
        _logger.info(
            "implementation.attempt.discarded_draft",
            attempt_id=str(self._specification.implementation_attempt_id),
            prepared_draft_id=(
                None if prepared.prepared_draft_id is None else str(prepared.prepared_draft_id)
            ),
        )

    async def _repository_token(self) -> str:
        if isinstance(self._access_token, str):
            return self._access_token
        self._access_token = await self._access_token()
        return self._access_token

    async def _baseline_runs(
        self, baseline: asyncio.Task[tuple[CheckRun, ...]]
    ) -> tuple[CheckRun, ...]:
        """The clean-base check runs, waiting for the background run on first use."""
        if not self._baseline:
            self._baseline = await baseline
            self._capture_logs(self._baseline)
        return self._baseline

    async def _run_cycles(
        self,
        *,
        executor: ToolExecutor,
        ledger: BudgetLedger,
        install: asyncio.Task[None],
        baseline: asyncio.Task[tuple[CheckRun, ...]],
    ) -> tuple[ImplementationAttemptState, str | None]:
        """Author, scope-check, and check cycles until one is acceptable or budget ends."""
        specification = self._specification
        scope = specification.approved_scope
        feedback: CycleFeedback | None = None
        for cycle_index in range(1, scope.budgets.correction_cycles + 1):
            stopped = await self._stop_requested()
            if stopped is not None:
                return (stopped, "the session withdrew authority for new live steps")

            if cycle_index == 1 and self._published_draft:
                # The adopted draft already serves the participant; its full checks
                # run on the same files now.
                comparison, candidate = await self._full_checks(executor, cycle_index=cycle_index)
                if comparison.acceptable:
                    return (ImplementationAttemptState.CANDIDATE_READY, None)
                feedback = CycleFeedback(
                    cycle_index=cycle_index,
                    scope_violations=(),
                    new_check_failures=tuple(
                        run for run in candidate if run.name in comparison.new_failures
                    ),
                )
                ledger.consume_correction_cycle()
                continue

            # An adopted draft is the first cycle's change, already formatted.
            drafted = self._draft if cycle_index == 1 else None
            if drafted is None:
                authored = await self._dependencies.change_author.author(
                    executor=executor, specification=specification, feedback=feedback
                )
                if authored.needs_scope_change:
                    return (
                        ImplementationAttemptState.SCOPE_CHANGE_REQUIRED,
                        authored.scope_change_reason or authored.summary,
                    )
                await self._format_changes(executor, install=install)
            diff = await stage_and_diff(executor)
            scope_check = check_diff_against_scope(
                diff=diff,
                scope=scope,
                added_dependency_lines=added_manifest_lines(diff, scope),
            )
            unsafe = self._unsafe_egress()
            if unsafe is not None:
                self._cycles.append(
                    CycleRecord(
                        cycle_index=cycle_index, patch=diff, scope_check=scope_check, check_runs=()
                    )
                )
                return (ImplementationAttemptState.UNSAFE, unsafe)

            if not scope_check.passed:
                self._cycles.append(
                    CycleRecord(
                        cycle_index=cycle_index, patch=diff, scope_check=scope_check, check_runs=()
                    )
                )
                feedback = CycleFeedback(
                    cycle_index=cycle_index,
                    scope_violations=scope_check.violations,
                    new_check_failures=(),
                )
                ledger.consume_correction_cycle()
                continue

            files = await staged_files(executor)
            carried = _carried_checks(drafted, files=files)
            if drafted is not None and carried is not None:
                self._staged = drafted.staged
            else:
                self._staging = asyncio.create_task(self._stage_candidate(files))
            await install
            candidate = (
                *(carried or ()),
                *await run_required_checks(
                    executor,
                    checks=workspace_fast_checks(scope.required_checks) if carried is None else (),
                    phase=CheckPhase.CANDIDATE,
                    cycle_index=cycle_index,
                ),
            )
            if self._staging is not None:
                self._staged = await self._take_staged()
                if self._staged is None:
                    # The spare's build is a fast check's run, so staging that failed
                    # beside the checks is tried once more rather than reported as a
                    # failure of the change.
                    self._staged = await self._stage_candidate(files)
                candidate = (
                    *candidate,
                    *candidate_runtime_runs(
                        scope.required_checks,
                        build=self._staged.build,
                        cycle_index=cycle_index,
                    ),
                )
            if self._staged is not None:
                candidate = (*candidate, _render_run(self._staged, cycle_index=cycle_index))
            self._capture_logs(candidate)
            comparison = compare_checks(
                baseline=await self._baseline_runs(baseline), candidate=candidate
            )
            self._cycles.append(
                CycleRecord(
                    cycle_index=cycle_index,
                    patch=diff,
                    scope_check=scope_check,
                    check_runs=candidate,
                )
            )
            if comparison.acceptable:
                # The fast checks passed: the participant sees this version now, and
                # its full checks run on the same files while they try it.
                refusal = await self._publish(
                    source=await self._workspace_source(executor, files=files), ledger=ledger
                )
                if refusal is not None:
                    return (ImplementationAttemptState.CHECKS_FAILED, refusal)
                comparison, candidate = await self._full_checks(executor, cycle_index=cycle_index)
                if comparison.acceptable:
                    return (ImplementationAttemptState.CANDIDATE_READY, None)
            feedback = CycleFeedback(
                cycle_index=cycle_index,
                scope_violations=(),
                new_check_failures=tuple(
                    run for run in candidate if run.name in comparison.new_failures
                ),
            )
            ledger.consume_correction_cycle()

        if self._handoff is not None:
            return (
                ImplementationAttemptState.CANDIDATE_READY,
                "the correction-cycle budget ended with the version still failing its full checks",
            )
        last = self._cycles[-1] if self._cycles else None
        reason = (
            "the correction-cycle budget ended with the candidate still failing checks"
            if last is not None and last.check_runs
            else "the correction-cycle budget ended with the diff outside the approved scope"
        )
        state = (
            ImplementationAttemptState.CHECKS_FAILED
            if last is not None and last.check_runs
            else ImplementationAttemptState.SCOPE_CHANGE_REQUIRED
        )
        return (state, reason)

    async def _publish(self, *, source: _VersionSource, ledger: BudgetLedger) -> str | None:
        """Publish the cycle's files as a Preview Version and swap the participant to it.

        Returns the refusal when the control plane will not publish them. The first
        version makes the attempt `candidate_ready`; a later one, after a full check
        failed, replaces the version the participant was trying, whose open
        Validation Attempt is abandoned so the corrected version is validated afresh.
        """
        specification = self._specification
        cycle = self._cycles[-1]
        _, manifest = await asyncio.gather(
            self._progress(ImplementationProgress.PREPARING_PREVIEW),
            self._extract(source=source, ledger=ledger),
        )
        try:
            handoff, _ = await publish_preview_version(
                self._database,
                specification=specification,
                manifest=manifest,
                scope_check=cycle.scope_check,
                comparison=compare_checks(baseline=self._baseline, candidate=cycle.check_runs),
                candidate_checks=cycle.check_runs,
                final_diff=cycle.patch,
                source_digest=source.source_digest,
                build=self._build_recipe,
                dependencies=source.dependencies,
                runtime=self._dependencies.preview_runtime,
                staged=self._staged,
                published_at=datetime.now(tz=UTC),
                preview_version_id=source.preview_version_id,
            )
        except PublicationRefusedError as refusal:
            return str(refusal)
        corrected = self._handoff is not None
        self._handoff = handoff
        self._manifest = manifest
        await self._progress(ImplementationProgress.READY_FOR_VALIDATION)
        if corrected:
            await replace_validated_version(
                self._database,
                product_signal_id=specification.product_signal_id,
                preview_version_id=handoff.preview_version_id,
            )
        else:
            await self._dependencies.candidate_ready(handoff)
        return None

    async def _full_checks(
        self, executor: ToolExecutor, *, cycle_index: int
    ) -> tuple[CheckComparison, tuple[CheckRun, ...]]:
        """Run the published version's remaining required checks, on its own files.

        The workspace's tree is read first and must be the version's, so the checks
        are recorded against the files they ran on. The cycles' evidence is made
        durable before the result, so delivery, which the result may unblock, always
        finds it.
        """
        handoff = self._handoff
        if handoff is None:
            raise RuntimeError("full checks run only on a published version")
        cycle = self._cycles[-1]
        await executor.execute(
            RunProcess(
                tool=ToolName.RUN_PROCESS,
                command="git add --all",
                working_directory=".",
                timeout_seconds=GIT_TIMEOUT_SECONDS,
            )
        )
        tree = await self._source_digest(executor)
        if tree != handoff.source.source_digest:
            raise ConnectorFailure(
                FailureDetail(
                    provider="vercel",
                    operation="verify_published_files",
                    category=TechnicalCategory.PROVIDER_UNAVAILABLE,
                    retry_advice=RetryAdvice.AFTER_DELAY,
                    effect_state=EffectState.ABSENT,
                    message=(
                        f"the workspace holds tree {tree}, not the published version's "
                        f"{handoff.source.source_digest}"
                    ),
                )
            )
        candidate = (
            *cycle.check_runs,
            *await run_required_checks(
                executor,
                checks=tuple(
                    check
                    for check in self._specification.approved_scope.required_checks
                    if not check.fast
                ),
                phase=CheckPhase.CANDIDATE,
                cycle_index=cycle_index,
            ),
        )
        self._capture_logs(candidate)
        comparison = compare_checks(baseline=self._baseline, candidate=candidate)
        self._cycles[-1] = CycleRecord(
            cycle_index=cycle_index,
            patch=cycle.patch,
            scope_check=cycle.scope_check,
            check_runs=candidate,
        )
        await self._record_evidence()
        await record_full_checks(
            self._database,
            preview_version_id=handoff.preview_version_id,
            runs=candidate,
            passed=comparison.acceptable,
            source_digest=tree,
        )
        return comparison, candidate

    async def _record_evidence(self) -> None:
        """Make the cycles not yet recorded durable, with the baseline the first time."""
        cycles = self._cycles[self._recorded_cycles :]
        baseline = () if self._baseline_recorded else self._baseline
        if not cycles and not baseline:
            return
        await record_cycle_evidence(
            self._database, specification=self._specification, cycles=cycles, baseline=baseline
        )
        self._recorded_cycles = len(self._cycles)
        self._baseline_recorded = self._baseline_recorded or bool(baseline)

    async def _settle(
        self,
        *,
        handle: SandboxHandle,
        executor: ToolExecutor,
        ledger: BudgetLedger,
        outcome: ImplementationAttemptState,
        reason: str | None,
    ) -> AttemptRunResult:
        """Extract the final artifacts and evidence, then clean up the workspace."""
        specification = self._specification
        manifest = await self._extract(source=await self._workspace_source(executor), ledger=ledger)
        await self._record_evidence()
        final = outcome
        if final is ImplementationAttemptState.CANDIDATE_READY and self._handoff is None:
            final = ImplementationAttemptState.CHECKS_FAILED
        elif self._handoff is not None and final is not ImplementationAttemptState.CANDIDATE_READY:
            # A version already serves the participant and the attempt is
            # `candidate_ready`; what stopped it leaves that version undeliverable.
            reason = f"the version's full checks did not finish: {reason or final.value}"
            final = ImplementationAttemptState.CANDIDATE_READY
        full_checks_passed = final is ImplementationAttemptState.CANDIDATE_READY and reason is None
        checkpoint = AttemptCheckpoint(
            cycle_index=len(self._cycles),
            consumed=ledger.consumed(),
            workspace_ref=handle.name,
            last_scope_digest=specification.scope_digest(),
        )
        destroyed = await self._hand_over(
            handle, full_checks_passed=full_checks_passed
        ) or await self._destroy(handle, resumable=_is_resumable(final))
        if destroyed:
            checkpoint = AttemptCheckpoint(
                cycle_index=checkpoint.cycle_index,
                consumed=checkpoint.consumed,
                workspace_ref=None,
                last_scope_digest=checkpoint.last_scope_digest,
            )
        await write_checkpoint(
            self._database,
            attempt_id=specification.implementation_attempt_id,
            checkpoint=checkpoint,
        )
        return AttemptRunResult(
            outcome=final,
            reason=reason,
            handoff=self._handoff,
            manifest=manifest,
            consumed=checkpoint.consumed,
            checkpoint=checkpoint,
            workspace_destroyed=destroyed,
            full_checks_passed=full_checks_passed,
        )

    async def _extract(self, *, source: _VersionSource, ledger: BudgetLedger) -> ArtifactManifest:
        """Extract every required artifact. A failure here raises and blocks cleanup."""
        specification = self._specification
        final_diff = self._cycles[-1].patch if self._cycles else ""
        evidence = AttemptEvidence(
            final_diff=final_diff,
            cycles=tuple(self._cycles),
            baseline_checks=self._baseline,
            tool_journal=tuple(self._journal),
            check_logs=dict(self._check_logs),
            inspection_evidence=tuple(self._inspected),
            dependency_manifest=source.dependency_manifest,
            dependency_lock_digest=source.dependencies.lockfile_digest,
            source_provenance={
                **scope_summary(specification.approved_scope),
                "clone_url": specification.checkout.clone_url,
                "workspace_snapshot_id": specification.workspace_snapshot_id,
                "prepared_workspace_id": (
                    None
                    if self._prepared_workspace_id is None
                    else str(self._prepared_workspace_id)
                ),
            },
            build_provenance={
                "build_recipe": self._build_recipe.model_dump(mode="json"),
                "consumed_budgets": ledger.consumed().as_column(),
                "cycle_count": len(self._cycles),
            },
            candidate_files=source.files,
        )
        return await extract_attempt_artifacts(
            self._dependencies.artifacts,
            specification=specification,
            evidence=evidence,
            extracted_at=datetime.now(tz=UTC),
        )

    async def _stage_candidate(self, files: Mapping[str, str]) -> StagedCandidate:
        """Stage the cycle's files in the session's spare and render them, while the checks run."""
        return await self._dependencies.preview_runtime.stage_candidate(
            client_id=self._specification.client_id, files=files
        )

    async def _take_staged(self) -> StagedCandidate | None:
        """Wait for the staging that ran beside the checks.

        Staging that failed leaves the deployment to stage the candidate itself,
        which reports any failure that is the candidate's own.
        """
        task, self._staging = self._staging, None
        if task is None:
            return None
        try:
            return await task
        except (ConnectorFailure, GuardRejectedError) as error:
            _logger.warning(
                "implementation.candidate_staging_failed",
                attempt_id=str(self._specification.implementation_attempt_id),
                error=str(error),
            )
            return None

    async def _destroy(self, handle: SandboxHandle, *, resumable: bool) -> bool:
        """Destroy the workspace unless the outcome is resumable from it."""
        if resumable:
            return False
        await self._dependencies.sandboxes.delete(handle)
        if self._prepared_workspace_id is not None:
            await release_claimed_workspace(
                self._database, prepared_workspace_id=self._prepared_workspace_id
            )
        return True

    async def _hand_over(self, handle: SandboxHandle, *, full_checks_passed: bool) -> bool:
        """Hand the workspace of a version that passed its full checks to the next change.

        Only while the case may still use another attempt; the sandbox then lives as
        long as a prepared workspace waits for its attempt.
        """
        specification = self._specification
        handoff = self._handoff
        if handoff is None or not full_checks_passed:
            return False
        handed = await hand_over_workspace(
            self._database,
            client_id=specification.client_id,
            product_signal_id=specification.product_signal_id,
            sandbox_name=handle.name,
            pins=pins_for_attempt(specification, build_recipe=self._build_recipe),
            baseline=self._baseline,
            starting_tree=handoff.source.source_digest,
            preview_version_id=handoff.preview_version_id,
            implementation_limit=self._implementation_limit,
        )
        if handed is None:
            return False
        try:
            await self._dependencies.sandboxes.extend_execution_time(
                handle, seconds=WORKSPACE_WINDOW_SECONDS
            )
        except ConnectorFailure as failure:
            # A draft that finds the sandbox gone fails, and the attempt that follows
            # prepares its own workspace.
            _logger.warning(
                "implementation.attempt.hand_over_not_extended",
                attempt_id=str(specification.implementation_attempt_id),
                message=failure.detail.message,
            )
        return True

    async def _stop_requested(self) -> ImplementationAttemptState | None:
        """Return the state that withdrew authority, or `None` to keep going."""
        state = await current_attempt(
            self._database, attempt_id=self._specification.implementation_attempt_id
        )
        return state.state if state.state in _STOP_STATES else None

    async def _progress(self, progress: ImplementationProgress) -> None:
        await record_progress(
            self._database,
            client_id=self._specification.client_id,
            implementation_attempt_id=self._specification.implementation_attempt_id,
            interview_session_id=self._specification.interview_session_id,
            progress=progress,
            occurred_at=datetime.now(tz=UTC),
        )

    async def _carry_forward(self, executor: ToolExecutor) -> None:
        """Start a change the person asked for from the version they asked to change.

        A later attempt builds on the case's newest ready version: its frozen files
        are written into the workspace before the change is authored, so the new
        candidate holds the earlier change and this one, and a first attempt starts
        from the clean base.
        """
        previous = self._previous
        if previous is None:
            return
        artifact = await load_artifact(self._database, preview_version_id=previous)
        for candidate in artifact.files:
            await executor.execute(
                WriteFile(tool=ToolName.WRITE_FILE, path=candidate.path, content=candidate.content)
            )
        _logger.info(
            "implementation.attempt.carried_forward",
            attempt_id=str(self._specification.implementation_attempt_id),
            preview_version_id=str(previous),
            files=len(artifact.files),
        )

    async def _format_changes(self, executor: ToolExecutor, *, install: asyncio.Task[None]) -> None:
        """Format the cycle's changed files once the formatter is installed."""
        if self._build_recipe.format_command is None:
            return
        # The formatter is one of the repository's installed dependencies.
        await install
        await format_changed_files(
            executor,
            command=self._build_recipe.format_command,
            subject=str(self._specification.implementation_attempt_id),
        )

    async def _source_digest(self, executor: ToolExecutor) -> str:
        """The candidate tree's own git object identity, computed in the workspace."""
        tree = await inspect_workspace(executor, operation="source_digest", command="git write-tree")
        return tree.strip()

    async def _workspace_source(
        self, executor: ToolExecutor, *, files: Mapping[str, str] | None = None
    ) -> _VersionSource:
        """What the workspace holds: its staged files, their tree, and the dependencies."""
        staged, manifest, lockfile, tree = await asyncio.gather(
            _settled(files) if files is not None else staged_files(executor),
            self._read_optional(executor, "package.json"),
            self._read_optional(executor, "package-lock.json"),
            self._source_digest(executor),
        )
        return _VersionSource(
            files=staged,
            source_digest=tree,
            dependencies=DependencyLockIdentity(
                lockfile_path="package-lock.json",
                lockfile_digest=_digest(lockfile),
                manifest_digest=_digest(manifest),
            ),
            dependency_manifest=manifest,
            preview_version_id=None,
        )

    def _unsafe_egress(self) -> str | None:
        """Why the run's commands reached too far for the network policy, if they did."""
        specification = self._specification
        egress = scan_commands_for_egress(
            commands=[entry.subject for entry in self._journal if entry.tool is ToolName.RUN_PROCESS],
            permitted_domains=working_egress(
                callback_hosts=specification.callback_hosts,
                openai_api_key=self._openai_api_key,
            ).allowed_domains,
        )
        if len(egress) >= UNSAFE_VIOLATION_THRESHOLD or any(
            item.exfiltration_shaped for item in egress
        ):
            return f"the run reached {len(egress)} hosts the network policy does not permit"
        return None

    async def _read_optional(self, executor: ToolExecutor, path: str) -> str:
        """Read a file the repository may not have, recording what was inspected."""
        result = await executor.execute(ReadFile(tool=ToolName.READ_FILE, path=path))
        self._inspected.append(
            {
                "path": path,
                "base_revision": self._specification.checkout.base_revision,
                "digest": _digest(result.output),
            }
        )
        return result.output

    def _capture_logs(self, runs: Sequence[CheckRun]) -> None:
        """Keep each check run's captured output for the durable artifact."""
        for run in runs:
            self._check_logs[run.log_path] = run.output_tail


async def _settled[ResultT](value: ResultT) -> ResultT:
    """A step that already happened, as the task the cycles await."""
    return value


async def _settle_background[ResultT](task: asyncio.Task[ResultT]) -> None:
    """Stop background work still running, and report a failure nothing awaited.

    A failure that decided the outcome was already raised where it was awaited; one
    that did not, because the cycles ended first, is logged rather than lost.
    """
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return
    except ConnectorFailure as failure:
        _logger.warning(
            "implementation.background_step_failed",
            operation=failure.detail.operation,
            message=failure.detail.message,
        )


def _carried_checks(
    draft: PreparedDraft | None, *, files: Mapping[str, str]
) -> tuple[CheckRun, ...] | None:
    """The fast checks an adopted draft already ran, when its files are this cycle's.

    The scope check may not change files, but the cycle stages the checkout again,
    so the draft's results count only for the same bytes.
    """
    if draft is None or draft.staged is None or draft.files_digest != files_digest(files):
        return None
    return draft.fast_checks


def _render_run(staged: StagedCandidate, *, cycle_index: int) -> CheckRun:
    """The spare's render check, reported as one more candidate check run."""
    seconds = staged.render.get("seconds")
    return CheckRun(
        phase=CheckPhase.CANDIDATE,
        name=RENDER_CHECK,
        command="render the change's pages in the spare candidate runtime",
        exit_code=0 if staged.render_passed else 1,
        duration_seconds=float(seconds) if isinstance(seconds, int | float) else 0.0,
        log_path=f"{LOG_DIRECTORY}/candidate-{cycle_index}-{RENDER_CHECK}.log",
        output_tail=json.dumps(staged.render, sort_keys=True)[-2000:],
    )


def _is_resumable(outcome: ImplementationAttemptState) -> bool:
    """Whether the workspace must survive so the attempt can continue from it."""
    return outcome in (
        ImplementationAttemptState.PAUSED,
        ImplementationAttemptState.INFRASTRUCTURE_BLOCKED,
    )


def _digest(body: str) -> str:
    """The digest a provenance record carries for one file's exact bytes."""
    return f"sha256:{sha256(body.encode('utf-8')).hexdigest()}"
