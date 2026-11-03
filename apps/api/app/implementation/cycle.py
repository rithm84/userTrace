"""The workspace steps one authored change goes through, in an attempt or a prepared draft.

After the coding agent writes a change, the repository's formatter runs over the
files it added or changed, the change is staged, and its diff and final files are
read back. An Implementation Attempt runs these for every cycle; a prepared draft
(`app.implementation.drafts`) runs the same steps before the gate decides, so an
attempt that adopts the draft finds the workspace exactly as its own cycle would
have left it.
"""

from __future__ import annotations

import asyncio
import shlex

import structlog

from app.connectors.failures import (
    ConnectorFailure,
    EffectState,
    FailureDetail,
    RetryAdvice,
    TechnicalCategory,
)
from app.implementation.tools import ReadFile, RunProcess, ToolExecutor, ToolName

__all__ = [
    "GIT_TIMEOUT_SECONDS",
    "format_changed_files",
    "inspect_workspace",
    "reset_to_base",
    "stage_and_diff",
    "staged_files",
]

_logger = structlog.get_logger(__name__)

GIT_TIMEOUT_SECONDS = 300
FORMAT_TIMEOUT_SECONDS = 300
FORMAT_OUTPUT_TAIL_CHARACTERS = 2000


async def format_changed_files(executor: ToolExecutor, *, command: str | None, subject: str) -> None:
    """Run the repository's formatter over the files the change added or changed.

    The agent cannot run the repository's tools, so its layout seldom matches the
    formatter's, and a check that enforces formatting would reject a working change
    over whitespace. Only changed files are formatted, so the diff stays the
    agent's own. A formatter that fails, such as on code that does not parse, leaves
    those files as they were and the required checks report why.
    """
    if command is None:
        return
    result = await executor.execute(
        RunProcess(
            tool=ToolName.RUN_PROCESS,
            command=(
                "git add --all && git --no-pager diff --cached --name-only -z --diff-filter=d"
                f" | xargs -0 --no-run-if-empty {command}"
            ),
            working_directory=".",
            timeout_seconds=FORMAT_TIMEOUT_SECONDS,
        )
    )
    if result.exit_code != 0:
        _logger.warning(
            "implementation_format_failed",
            subject=subject,
            command=command,
            exit_code=result.exit_code,
            output_tail=result.output[-FORMAT_OUTPUT_TAIL_CHARACTERS:],
        )


async def stage_and_diff(executor: ToolExecutor) -> str:
    """Stage every change in the checkout and return the staged diff, in one command."""
    result = await executor.execute(
        RunProcess(
            tool=ToolName.RUN_PROCESS,
            command="git add --all && git --no-pager diff --cached",
            working_directory=".",
            timeout_seconds=GIT_TIMEOUT_SECONDS,
        )
    )
    return result.output


async def staged_files(executor: ToolExecutor) -> dict[str, str]:
    """Read the final content of every staged path the change adds or changes.

    A candidate runtime installs these verbatim over the base it was created
    from; a deleted path has no content to install and is not listed.
    """
    listed = await inspect_workspace(
        executor,
        operation="list_candidate_files",
        command="git --no-pager diff --cached --name-only --diff-filter=d",
    )
    paths = [path for path in listed.splitlines() if path]
    results = await asyncio.gather(
        *(executor.execute(ReadFile(tool=ToolName.READ_FILE, path=path)) for path in paths)
    )
    return {path: result.output for path, result in zip(paths, results, strict=True)}


async def reset_to_base(executor: ToolExecutor, *, tree: str | None = None) -> None:
    """Put the checkout back at its starting files, keeping ignored files.

    The starting files are the base revision, or the git `tree` a workspace prepared
    from a published version's files records. Installed dependencies and the type
    checker's incremental cache are ignored files, so they survive; only what a
    discarded change wrote is removed.
    """
    restore = (
        "git reset --quiet --hard HEAD"
        if tree is None
        else f"git read-tree -u --reset {shlex.quote(tree)}"
    )
    result = await executor.execute(
        RunProcess(
            tool=ToolName.RUN_PROCESS,
            command=f"{restore} && git clean --quiet -fd",
            working_directory=".",
            timeout_seconds=GIT_TIMEOUT_SECONDS,
        )
    )
    if result.exit_code != 0:
        raise ConnectorFailure(
            FailureDetail(
                provider="vercel",
                operation="reset_to_base",
                category=TechnicalCategory.PROVIDER_UNAVAILABLE,
                retry_advice=RetryAdvice.AFTER_DELAY,
                effect_state=EffectState.PARTIAL,
                message=f"resetting the checkout exited {result.exit_code}: {result.output[-2000:]}",
            )
        )


async def inspect_workspace(executor: ToolExecutor, *, operation: str, command: str) -> str:
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
