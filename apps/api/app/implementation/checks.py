"""Required checks: a clean-base baseline, the candidate, and new-failure detection.

The workspace runs the gate-frozen commands on the clean base to establish a
comparable baseline, then again against the candidate. The checks of one run start
together, except that a check waits for the earlier checks its `after` names: a
client declares the pairs that must not overlap, such as a build that writes files
another check reads. A pre-existing failure is
reported as integration health, not as the attempt's fault; the candidate must
simply introduce no new failure. A failed or ambiguous check can never be
relabelled as success, so the comparison is a pure function over exit codes.

Each run's combined output is captured to a workspace file before it is read
back, so the log survives as an extractable artifact rather than living only in
a response body.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.gate.scope import RequiredCheck
from app.implementation.tools import ReadFile, RunProcess, ToolName, WorkspaceShell

__all__ = [
    "CheckComparison",
    "CheckPhase",
    "CheckRun",
    "compare_checks",
    "run_required_checks",
]

LOG_DIRECTORY = ".utrace-checks"
"""Where check output is captured inside the checkout, before extraction."""


class CheckPhase(StrEnum):
    """Which side of the comparison a run belongs to."""

    BASELINE = "baseline"
    CANDIDATE = "candidate"


class CheckModel(BaseModel):
    """Base for the check records: immutable and closed to extra fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class CheckRun(CheckModel):
    """One required check, run once."""

    phase: CheckPhase
    name: str
    command: str
    exit_code: int
    duration_seconds: float
    log_path: str
    """Repository-relative path of the captured combined output."""
    output_tail: str
    """The last lines of the run's output, for the refusal message and the Case File."""

    @property
    def passed(self) -> bool:
        """A check passes only on exit code zero."""
        return self.exit_code == 0


class CheckComparison(CheckModel):
    """The baseline-versus-candidate verdict for one attempt cycle."""

    new_failures: tuple[str, ...]
    """Checks that pass on the clean base and fail on the candidate."""
    pre_existing_failures: tuple[str, ...]
    """Checks that already fail on the clean base: integration health, not this attempt."""
    fixed_failures: tuple[str, ...]
    """Checks that fail on the base and pass on the candidate."""

    @property
    def acceptable(self) -> bool:
        """The candidate is acceptable when it introduces no new failure."""
        return not self.new_failures


_TAIL_CHARACTERS = 4000


async def run_required_checks(
    executor: WorkspaceShell,
    *,
    checks: Sequence[RequiredCheck],
    phase: CheckPhase,
    cycle_index: int,
) -> tuple[CheckRun, ...]:
    """Run every required check once and capture each one's combined output.

    A check that fails still releases the checks waiting for it: every check runs,
    as it would one after another, and only the overlap changes.
    """
    await executor.execute(
        RunProcess(
            tool=ToolName.RUN_PROCESS,
            command=f"mkdir -p {LOG_DIRECTORY}",
            working_directory=".",
            timeout_seconds=60,
        )
    )
    started: dict[str, asyncio.Task[CheckRun]] = {}
    for check in checks:
        started[check.name] = asyncio.create_task(
            _run_check(
                executor,
                check=check,
                phase=phase,
                cycle_index=cycle_index,
                prerequisites=tuple(started[name] for name in check.after),
            )
        )
    try:
        return tuple(await asyncio.gather(*started.values()))
    finally:
        for task in started.values():
            task.cancel()


async def _run_check(
    executor: WorkspaceShell,
    *,
    check: RequiredCheck,
    phase: CheckPhase,
    cycle_index: int,
    prerequisites: tuple[asyncio.Task[CheckRun], ...],
) -> CheckRun:
    """Run one check once its prerequisites have finished, whatever their verdicts."""
    if prerequisites:
        await asyncio.wait(prerequisites)
    log_path = f"{LOG_DIRECTORY}/{phase.value}-{cycle_index}-{check.name}.log"
    result = await executor.execute(
        RunProcess(
            tool=ToolName.RUN_PROCESS,
            command=f"{check.command} > {log_path} 2>&1",
            working_directory=check.working_directory,
            timeout_seconds=check.timeout_seconds,
        )
    )
    captured = await _read_log(executor, log_path)
    return CheckRun(
        phase=phase,
        name=check.name,
        command=check.command,
        exit_code=result.exit_code if result.exit_code is not None else -1,
        duration_seconds=result.duration_seconds,
        log_path=log_path,
        output_tail=captured[-_TAIL_CHARACTERS:],
    )


async def _read_log(executor: WorkspaceShell, log_path: str) -> str:
    """Read one captured log back out of the workspace."""
    result = await executor.execute(ReadFile(tool=ToolName.READ_FILE, path=log_path))
    return result.output


def compare_checks(
    *, baseline: Sequence[CheckRun], candidate: Sequence[CheckRun]
) -> CheckComparison:
    """Compare the two phases and name every check whose verdict changed."""
    base_by_name = {run.name: run for run in baseline}
    new_failures: list[str] = []
    pre_existing: list[str] = []
    fixed: list[str] = []
    for run in candidate:
        base = base_by_name.get(run.name)
        if base is None:
            if not run.passed:
                new_failures.append(run.name)
            continue
        if not base.passed and not run.passed:
            pre_existing.append(run.name)
        elif base.passed and not run.passed:
            new_failures.append(run.name)
        elif not base.passed and run.passed:
            fixed.append(run.name)
    return CheckComparison(
        new_failures=tuple(new_failures),
        pre_existing_failures=tuple(pre_existing),
        fixed_failures=tuple(fixed),
    )
