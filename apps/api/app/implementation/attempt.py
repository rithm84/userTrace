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
against the baseline. While a cycle's checks run, the preview runtime builds the
same staged files, so an accepted candidate only has to start; a cycle whose
checks fail discards that build. A correction cycle stays inside the attempt and
consumes no revision, only budget.

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
from collections.abc import Sequence
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
from app.implementation.checks import CheckPhase, CheckRun, compare_checks, run_required_checks
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
    PrebuiltCandidate,
    PreviewRuntimePort,
)
from app.implementation.prepared import (
    ClaimedWorkspace,
    claim_prepared_workspace,
    pins_for_attempt,
)
from app.implementation.preview_version import (
    PublicationRefusedError,
    previous_version,
    publish_preview_version,
)
from app.implementation.progress import record_progress
from app.implementation.scope_check import (
    added_manifest_lines,
    check_diff_against_scope,
)
from app.implementation.specification import AttemptSpecification
from app.implementation.state import AttemptCheckpoint, current_attempt, write_checkpoint
from app.implementation.tools import ReadFile, RunProcess, ToolExecutor, ToolName, WriteFile
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

GIT_TIMEOUT_SECONDS = 300

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


class AttemptRunner:
    """Runs one Implementation Attempt to a typed outcome."""

    def __init__(
        self,
        *,
        database: Database,
        dependencies: WorkspaceDependencies,
        specification: AttemptSpecification,
        build_recipe: BuildRecipe,
        repository_access_token: str,
        openai_api_key: str,
        ledger: BudgetLedger,
    ) -> None:
        self._database = database
        self._dependencies = dependencies
        self._specification = specification
        self._build_recipe = build_recipe
        self._access_token = repository_access_token
        self._openai_api_key = openai_api_key
        self._ledger = ledger
        self._cycles: list[CycleRecord] = []
        self._baseline: tuple[CheckRun, ...] = ()
        self._check_logs: dict[str, str] = {}
        self._inspected: list[dict[str, object]] = []
        self._prepared_workspace_id: UUID | None = None
        self._prebuild: asyncio.Task[PrebuiltCandidate] | None = None

    async def run(self) -> AttemptRunResult:
        """Execute the attempt and return its outcome."""
        specification = self._specification
        ledger = self._ledger
        await self._progress(ImplementationProgress.REPRODUCTION_CAPTURED)

        prepared = await claim_prepared_workspace(
            self._database,
            self._dependencies.sandboxes,
            specification=specification,
            pins=pins_for_attempt(specification, build_recipe=self._build_recipe),
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
                    repository_token=self._access_token,
                ),
                env={"CODEX_API_KEY": PLACEHOLDER_API_KEY, "CI": "1"},
                execution_time_limit_seconds=specification.approved_scope.budgets.elapsed_seconds,
                vcpus=WORKSPACE_VCPUS,
            )
        else:
            handle = prepared.handle
            self._prepared_workspace_id = prepared.prepared_workspace_id
        executor = ToolExecutor(
            database=self._database,
            sandboxes=self._dependencies.sandboxes,
            handle=handle,
            specification=specification,
            fencing_token=specification.lease.fencing_token,
            budgets=ledger,
            workspace_root=specification.checkout.workspace_root,
            journal=[],
        )
        try:
            return await self._execute(
                handle=handle, executor=executor, ledger=ledger, prepared=prepared
            )
        finally:
            await self._discard_prebuild()
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
                install, baseline = await self._adopt(prepared)
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
        except LeaseLostError:
            # A stale run publishes nothing and destroys nothing: the current lease
            # holder owns the workspace and its artifacts.
            raise
        except ConnectorFailure as failure:
            if failure.detail.retry_advice is RetryAdvice.NEVER:
                raise
            outcome, reason = ImplementationAttemptState.INFRASTRUCTURE_BLOCKED, failure.detail.message

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
        self, prepared: ClaimedWorkspace
    ) -> tuple[asyncio.Task[None], asyncio.Task[tuple[CheckRun, ...]]]:
        """Open a claimed workspace to the agent and give it the whole attempt budget.

        Its dependencies and its baseline already exist, so both are settled.
        """
        specification = self._specification
        sandboxes = self._dependencies.sandboxes
        await sandboxes.update_egress(
            prepared.handle,
            egress=working_egress(
                callback_hosts=specification.callback_hosts, openai_api_key=self._openai_api_key
            ),
        )
        await sandboxes.extend_execution_time(
            prepared.handle, seconds=specification.approved_scope.budgets.elapsed_seconds
        )
        await self._progress(ImplementationProgress.MAKING_CHANGES)
        return asyncio.create_task(_settled(None)), asyncio.create_task(
            _settled(prepared.baseline)
        )

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

            authored = await self._dependencies.change_author.author(
                executor=executor, specification=specification, feedback=feedback
            )
            if authored.needs_scope_change:
                return (
                    ImplementationAttemptState.SCOPE_CHANGE_REQUIRED,
                    authored.scope_change_reason or authored.summary,
                )

            diff = await self._stage_and_diff(executor)
            scope_check = check_diff_against_scope(
                diff=diff,
                scope=scope,
                added_dependency_lines=added_manifest_lines(diff, scope),
            )
            egress = scan_commands_for_egress(
                commands=[
                    entry.subject
                    for entry in executor.journal
                    if entry.tool is ToolName.RUN_PROCESS
                ],
                permitted_domains=working_egress(
                    callback_hosts=specification.callback_hosts,
                    openai_api_key=self._openai_api_key,
                ).allowed_domains,
            )
            if len(egress) >= UNSAFE_VIOLATION_THRESHOLD or any(
                item.exfiltration_shaped for item in egress
            ):
                self._cycles.append(
                    CycleRecord(
                        cycle_index=cycle_index, patch=diff, scope_check=scope_check, check_runs=()
                    )
                )
                return (
                    ImplementationAttemptState.UNSAFE,
                    f"the run reached {len(egress)} hosts the network policy does not permit",
                )

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

            await self._discard_prebuild()
            self._prebuild = asyncio.create_task(self._prebuild_candidate(executor))
            await install
            candidate = await run_required_checks(
                executor,
                checks=scope.required_checks,
                phase=CheckPhase.CANDIDATE,
                cycle_index=cycle_index,
            )
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
                return (ImplementationAttemptState.PUBLISHING_CANDIDATE, None)
            await self._discard_prebuild()
            feedback = CycleFeedback(
                cycle_index=cycle_index,
                scope_violations=(),
                new_check_failures=tuple(
                    run for run in candidate if run.name in comparison.new_failures
                ),
            )
            ledger.consume_correction_cycle()

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

    async def _settle(
        self,
        *,
        handle: SandboxHandle,
        executor: ToolExecutor,
        ledger: BudgetLedger,
        outcome: ImplementationAttemptState,
        reason: str | None,
    ) -> AttemptRunResult:
        """Extract artifacts, publish when the candidate is acceptable, then clean up."""
        specification = self._specification
        publishing = outcome is ImplementationAttemptState.PUBLISHING_CANDIDATE
        if publishing:
            await self._progress(ImplementationProgress.PREPARING_PREVIEW)
        else:
            await self._discard_prebuild()

        manifest = await self._extract(executor=executor, ledger=ledger)
        await record_cycle_evidence(
            self._database,
            specification=specification,
            cycles=self._cycles,
            baseline=self._baseline,
        )

        handoff: CandidateHandoff | None = None
        final = outcome
        if publishing:
            prebuilt = await self._take_prebuild()
            try:
                handoff, _ = await publish_preview_version(
                    self._database,
                    specification=specification,
                    manifest=manifest,
                    scope_check=self._cycles[-1].scope_check,
                    comparison=compare_checks(
                        baseline=self._baseline, candidate=self._cycles[-1].check_runs
                    ),
                    candidate_checks=self._cycles[-1].check_runs,
                    final_diff=self._cycles[-1].patch,
                    source_digest=await self._source_digest(executor),
                    build=self._build_recipe,
                    dependencies=await self._dependency_identity(executor),
                    runtime=self._dependencies.preview_runtime,
                    prebuilt=prebuilt,
                    published_at=datetime.now(tz=UTC),
                )
            except PublicationRefusedError as refusal:
                if prebuilt is not None:
                    await self._dependencies.preview_runtime.discard_prebuilt(prebuilt)
                final, reason = ImplementationAttemptState.CHECKS_FAILED, str(refusal)
            else:
                final = ImplementationAttemptState.CANDIDATE_READY
                await self._progress(ImplementationProgress.READY_FOR_VALIDATION)

        checkpoint = AttemptCheckpoint(
            cycle_index=len(self._cycles),
            consumed=ledger.consumed(),
            workspace_ref=handle.name,
            last_scope_digest=specification.scope_digest(),
        )
        destroyed = await self._destroy(handle, resumable=_is_resumable(final))
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
            handoff=handoff,
            manifest=manifest,
            consumed=checkpoint.consumed,
            checkpoint=checkpoint,
            workspace_destroyed=destroyed,
        )

    async def _extract(
        self, *, executor: ToolExecutor, ledger: BudgetLedger
    ) -> ArtifactManifest:
        """Extract every required artifact. A failure here raises and blocks cleanup."""
        specification = self._specification
        final_diff = self._cycles[-1].patch if self._cycles else ""
        manifest_body = await self._read_optional(executor, "package.json")
        lock_body = await self._read_optional(executor, "package-lock.json")
        evidence = AttemptEvidence(
            final_diff=final_diff,
            cycles=tuple(self._cycles),
            baseline_checks=self._baseline,
            tool_journal=executor.journal,
            check_logs=dict(self._check_logs),
            inspection_evidence=tuple(self._inspected),
            dependency_manifest=manifest_body,
            dependency_lock_digest=_digest(lock_body),
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
            candidate_files=await self._candidate_files(executor),
        )
        return await extract_attempt_artifacts(
            self._dependencies.artifacts,
            specification=specification,
            evidence=evidence,
            extracted_at=datetime.now(tz=UTC),
        )

    async def _prebuild_candidate(self, executor: ToolExecutor) -> PrebuiltCandidate:
        """Build the candidate preview from the staged files while the checks run."""
        return await self._dependencies.preview_runtime.prebuild_candidate(
            client_id=self._specification.client_id,
            files=await self._candidate_files(executor),
            build_command=self._build_recipe.build_command,
        )

    async def _take_prebuild(self) -> PrebuiltCandidate | None:
        """Wait for the build that ran beside the checks, and take it over.

        A build that failed leaves the deployment to build the candidate itself,
        which reports any failure that is the candidate's own.
        """
        task, self._prebuild = self._prebuild, None
        if task is None:
            return None
        try:
            return await task
        except (ConnectorFailure, GuardRejectedError) as error:
            _logger.warning(
                "implementation.candidate_prebuild_failed",
                attempt_id=str(self._specification.implementation_attempt_id),
                error=str(error),
            )
            return None

    async def _discard_prebuild(self) -> None:
        """Destroy a build that ran beside checks whose candidate will not be published."""
        prebuilt = await self._take_prebuild()
        if prebuilt is not None:
            await self._dependencies.preview_runtime.discard_prebuilt(prebuilt)

    async def _destroy(self, handle: SandboxHandle, *, resumable: bool) -> bool:
        """Destroy the workspace unless the outcome is resumable from it."""
        if resumable:
            return False
        await self._dependencies.sandboxes.delete(handle)
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
        previous = await previous_version(
            self._database,
            product_signal_id=self._specification.product_signal_id,
            implementation_attempt_id=self._specification.implementation_attempt_id,
        )
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

    async def _stage_and_diff(self, executor: ToolExecutor) -> str:
        """Stage everything the cycle wrote and return the complete staged diff."""
        await executor.execute(
            RunProcess(
                tool=ToolName.RUN_PROCESS,
                command="git add --all",
                working_directory=".",
                timeout_seconds=GIT_TIMEOUT_SECONDS,
            )
        )
        result = await executor.execute(
            RunProcess(
                tool=ToolName.RUN_PROCESS,
                command="git --no-pager diff --cached",
                working_directory=".",
                timeout_seconds=GIT_TIMEOUT_SECONDS,
            )
        )
        return result.output

    async def _candidate_files(self, executor: ToolExecutor) -> dict[str, str]:
        """Read the final content of every staged path the candidate adds or changes.

        A candidate runtime installs these verbatim over the base it was created
        from; a deleted path has no content to install and is not listed.
        """
        listed = await self._inspect(
            executor,
            operation="list_candidate_files",
            command="git --no-pager diff --cached --name-only --diff-filter=d",
        )
        paths = [path for path in listed.splitlines() if path]
        results = await asyncio.gather(
            *(executor.execute(ReadFile(tool=ToolName.READ_FILE, path=path)) for path in paths)
        )
        return {path: result.output for path, result in zip(paths, results, strict=True)}

    async def _source_digest(self, executor: ToolExecutor) -> str:
        """The candidate tree's own git object identity, computed in the workspace."""
        tree = await self._inspect(executor, operation="source_digest", command="git write-tree")
        return tree.strip()

    async def _inspect(self, executor: ToolExecutor, *, operation: str, command: str) -> str:
        """Read workspace state for extraction, raising when the read itself fails."""
        result = await executor.inspect(command=command, timeout_seconds=GIT_TIMEOUT_SECONDS)
        if result.exit_code != 0:
            raise ConnectorFailure(
                FailureDetail(
                    provider="vercel",
                    operation=operation,
                    category=TechnicalCategory.PROVIDER_UNAVAILABLE,
                    retry_advice=RetryAdvice.AFTER_DELAY,
                    effect_state=EffectState.ABSENT,
                    message=f"'{command}' exited {result.exit_code}: {result.stdout[-2000:]}",
                )
            )
        return result.stdout

    async def _dependency_identity(self, executor: ToolExecutor) -> DependencyLockIdentity:
        """Read the candidate's manifest and lockfile digests."""
        manifest = await self._read_optional(executor, "package.json")
        lockfile = await self._read_optional(executor, "package-lock.json")
        return DependencyLockIdentity(
            lockfile_path="package-lock.json",
            lockfile_digest=_digest(lockfile),
            manifest_digest=_digest(manifest),
        )

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


def _is_resumable(outcome: ImplementationAttemptState) -> bool:
    """Whether the workspace must survive so the attempt can continue from it."""
    return outcome in (
        ImplementationAttemptState.PAUSED,
        ImplementationAttemptState.INFRASTRUCTURE_BLOCKED,
    )


def _digest(body: str) -> str:
    """The digest a provenance record carries for one file's exact bytes."""
    return f"sha256:{sha256(body.encode('utf-8')).hexdigest()}"
