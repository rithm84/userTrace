"""Getting a checked-out workspace ready for authoring.

The checkout gets its clean base exported beside it for the baseline, its check
logs kept out of the diff, its dependencies installed from the client's pinned
recipe, and the required checks run on the clean base. The same steps run in two
places: the Job that prepares a case's workspace while its interview runs, before
any attempt or lease exists, and an attempt that found no prepared workspace, under
its lease. `WorkspaceShell` is what both run them through; `SandboxShell` is the
lease-free one.

The install writes its exit status to `DEPENDENCY_INSTALL_STATUS`, where the agent
waits for it before running a check.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from app.connectors.failures import (
    ConnectorFailure,
    EffectState,
    FailureDetail,
    RetryAdvice,
    TechnicalCategory,
)
from app.connectors.vercel_sandboxes import SandboxHandle, VercelSandboxes
from app.gate.scope import RequiredCheck
from app.implementation.checks import CheckPhase, CheckRun, run_required_checks
from app.implementation.environment import BASELINE_ROOT, DEPENDENCY_INSTALL_STATUS
from app.implementation.tools import ReadFile, RunProcess, ToolName, ToolResult, WorkspaceShell
from app.implementation.workspace import WorkspaceFilesystem

__all__ = [
    "SandboxShell",
    "exclude_check_logs",
    "export_clean_base",
    "install_dependencies",
    "run_baseline",
]

_INSTALL_TIMEOUT_SECONDS: Final[int] = 1800
_COPY_TIMEOUT_SECONDS: Final[int] = 300
_CHECK_LOG_EXCLUDE: Final[str] = ".utrace-checks/"


class SandboxShell:
    """Runs setup steps in a workspace that no attempt holds yet: no lease, no budget."""

    def __init__(self, sandboxes: VercelSandboxes, handle: SandboxHandle, *, root: str) -> None:
        self._sandboxes = sandboxes
        self._handle = handle
        self._root = root
        self._filesystem = WorkspaceFilesystem(sandboxes, handle, root=root)

    async def execute(self, call: RunProcess | ReadFile) -> ToolResult:
        """Run one command or read one file under this shell's root."""
        match call:
            case RunProcess():
                execution = await self._sandboxes.run(
                    self._handle,
                    script=call.command,
                    cwd=(
                        self._root
                        if call.working_directory in (".", "")
                        else f"{self._root}/{call.working_directory}"
                    ),
                    timeout_seconds=call.timeout_seconds,
                    sudo=False,
                )
                return ToolResult(
                    tool=ToolName.RUN_PROCESS,
                    subject=call.command,
                    output=execution.stdout,
                    exit_code=execution.exit_code,
                    duration_seconds=execution.duration_seconds,
                )
            case ReadFile():
                return ToolResult(
                    tool=ToolName.READ_FILE,
                    subject=call.path,
                    output=await self._filesystem.read_text(path=call.path),
                    exit_code=None,
                    duration_seconds=0.0,
                )

    def at_root(self, workspace_root: str) -> SandboxShell:
        """The same lease-free shell over another directory of the sandbox."""
        return SandboxShell(self._sandboxes, self._handle, root=workspace_root)


async def exclude_check_logs(shell: WorkspaceShell) -> None:
    """Keep captured check output out of the accepted diff.

    A failure here is a setup failure, not something to continue past: check output
    that reached the diff would be measured as a code change.
    """
    await _require_success(
        shell,
        operation="exclude_workspace_files",
        command=(
            f"mkdir -p .git/info && printf '%s\\n' '{_CHECK_LOG_EXCLUDE}' >> .git/info/exclude"
        ),
        timeout_seconds=60,
    )


async def export_clean_base(shell: WorkspaceShell) -> None:
    """Export the base revision's tree beside the checkout for the baseline checks.

    It is exported from git, not copied from the working tree, so an edit the agent
    makes meanwhile can never reach it; the installed dependencies are hard-linked in
    once they exist.
    """
    await _require_success(
        shell,
        operation="export_clean_base",
        command=f"mkdir -p {BASELINE_ROOT} && git archive HEAD | tar -xf - -C {BASELINE_ROOT}",
        timeout_seconds=_COPY_TIMEOUT_SECONDS,
    )


async def install_dependencies(shell: WorkspaceShell, *, install_command: str) -> None:
    """Install the repository's dependencies from its pinned build recipe.

    The recipe is the client's own; it is replayed and never substituted. Its exit
    status is written where the agent waits for it. A non-zero exit is an
    infrastructure blocker, not a candidate: the checks that follow would measure an
    empty toolchain rather than the code.
    """
    await _require_success(
        shell,
        operation="install_dependencies",
        command=(
            f"({install_command}); status=$?; "
            f"printf '%s' \"$status\" > {DEPENDENCY_INSTALL_STATUS}; exit $status"
        ),
        timeout_seconds=_INSTALL_TIMEOUT_SECONDS,
    )


async def run_baseline(
    shell: WorkspaceShell, *, checks: Sequence[RequiredCheck]
) -> tuple[CheckRun, ...]:
    """Link the installed dependencies into the clean base and run the checks there."""
    await _require_success(
        shell,
        operation="link_baseline_dependencies",
        command=f"cp -al node_modules {BASELINE_ROOT}/node_modules",
        timeout_seconds=_COPY_TIMEOUT_SECONDS,
    )
    return await run_required_checks(
        shell.at_root(BASELINE_ROOT), checks=checks, phase=CheckPhase.BASELINE, cycle_index=0
    )


async def _require_success(
    shell: WorkspaceShell, *, operation: str, command: str, timeout_seconds: int
) -> None:
    """Run one setup command that has to succeed, or raise a typed blocker."""
    result = await shell.execute(
        RunProcess(
            tool=ToolName.RUN_PROCESS,
            command=command,
            working_directory=".",
            timeout_seconds=timeout_seconds,
        )
    )
    if result.exit_code != 0:
        raise ConnectorFailure(
            FailureDetail(
                provider="vercel",
                operation=operation,
                category=TechnicalCategory.PROVIDER_UNAVAILABLE,
                retry_advice=RetryAdvice.AFTER_DELAY,
                effect_state=EffectState.PARTIAL,
                message=f"'{command}' exited {result.exit_code}: {result.output[-2000:]}",
            )
        )
