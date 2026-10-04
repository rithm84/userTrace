"""The typed tool executor every control-plane operation in a workspace runs through.

Three tools exist and no others: read an exact file, write a file, run a process.
The attempt runner's own steps — installing, checking, diffing — and the harness
author's staging and launch of Codex go through this executor, so the fencing,
the budgets, and the recorded journal are the same for all of them. What the agent
itself does inside the sandbox is recorded from its event stream with
`record_agent_activity`.

Every mutating call first asks the control plane whether this run still holds the
attempt's active Environment Lease at this exact token. A stale worker may still
read — it may emit evidence — but it can write nothing and start nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol

import structlog
from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict, Field

from app.connectors.ports import WorkspaceExecution
from app.connectors.vercel_sandboxes import SandboxHandle, VercelSandboxes
from app.db.pool import Database
from app.db.rows import as_int
from app.errors import LeaseLostError
from app.gate.paths import PathEscapesRepositoryError, normalize
from app.implementation.budgets import BudgetLedger
from app.implementation.specification import AttemptSpecification
from app.implementation.workspace import WorkspaceFilesystem

__all__ = [
    "ReadFile",
    "RunProcess",
    "ToolCall",
    "ToolCallRefusedError",
    "ToolExecutor",
    "ToolJournalEntry",
    "ToolName",
    "ToolResult",
    "WorkspaceAccess",
    "WorkspaceShell",
    "WriteFile",
    "journal_payload",
]

_logger = structlog.get_logger(__name__)


class ToolName(StrEnum):
    """The closed set of operations a workspace run records."""

    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    RUN_PROCESS = "run_process"


class ToolCallRefusedError(Exception):
    """A tool call was malformed or named something outside the checkout."""

    def __init__(self, subject: str, reason: str) -> None:
        super().__init__(f"tool call refused for '{subject}': {reason}")
        self.subject = subject
        self.reason = reason


class ToolModel(BaseModel):
    """Base for the tool payloads: immutable and closed to extra fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ReadFile(ToolModel):
    """Read one exact file at the pinned revision or as the attempt has left it."""

    tool: Literal[ToolName.READ_FILE]
    path: str


class WriteFile(ToolModel):
    """Replace one file's complete contents."""

    tool: Literal[ToolName.WRITE_FILE]
    path: str
    content: str


class RunProcess(ToolModel):
    """Run one command inside the checkout."""

    tool: Literal[ToolName.RUN_PROCESS]
    command: str
    working_directory: str
    """Repository-relative directory, or `.` for the checkout root."""
    timeout_seconds: Annotated[int, Field(ge=1, le=3600)]


ToolCall = Annotated[ReadFile | WriteFile | RunProcess, Field(discriminator="tool")]


class ToolResult(ToolModel):
    """What one tool call produced. A non-zero exit code is a result, not a failure."""

    tool: ToolName
    subject: str
    """The path or command the call named."""
    output: str
    exit_code: int | None
    """Set for `run_process` only; `None` for the other tools."""
    duration_seconds: float


class ToolJournalEntry(ToolModel):
    """One journal line. The journal is extracted as a durable attempt artifact."""

    sequence: int
    occurred_at: datetime
    tool: ToolName
    subject: str
    exit_code: int | None
    output_bytes: int


@dataclass(frozen=True, slots=True)
class WorkspaceAccess:
    """The sandbox one run drives, for work that is not a checkout-confined tool call."""

    sandboxes: VercelSandboxes
    handle: SandboxHandle


class WorkspaceShell(Protocol):
    """Runs commands and reads files under one workspace root.

    `ToolExecutor` is the fenced, budgeted shell an attempt runs under;
    `app.implementation.workspace_setup.SandboxShell` runs the same setup steps
    before any attempt or lease exists.
    """

    async def execute(self, call: RunProcess | ReadFile) -> ToolResult:
        """Run one command or read one file."""
        ...

    def at_root(self, workspace_root: str) -> WorkspaceShell:
        """The same shell over another directory of the same sandbox."""
        ...


class ToolExecutor:
    """Executes typed tool calls for one attempt run under one fencing token."""

    def __init__(
        self,
        *,
        database: Database,
        sandboxes: VercelSandboxes,
        handle: SandboxHandle,
        specification: AttemptSpecification,
        fencing_token: int,
        budgets: BudgetLedger,
        workspace_root: str,
        journal: list[ToolJournalEntry],
    ) -> None:
        self._database = database
        self._sandboxes = sandboxes
        self._handle = handle
        self._root = workspace_root
        self._filesystem = WorkspaceFilesystem(sandboxes, handle, root=workspace_root)
        self._specification = specification
        self._fencing_token = fencing_token
        self._budgets = budgets
        self._journal = journal

    def at_root(self, workspace_root: str) -> ToolExecutor:
        """An executor for another directory of the same sandbox.

        It runs under this run's lease, charges the same budgets, and appends to
        the same journal, so work done there is accounted exactly like work in
        the checkout.
        """
        return ToolExecutor(
            database=self._database,
            sandboxes=self._sandboxes,
            handle=self._handle,
            specification=self._specification,
            fencing_token=self._fencing_token,
            budgets=self._budgets,
            workspace_root=workspace_root,
            journal=self._journal,
        )

    @property
    def workspace(self) -> WorkspaceAccess:
        """The sandbox this executor drives."""
        return WorkspaceAccess(sandboxes=self._sandboxes, handle=self._handle)

    async def require_current_lease(self) -> None:
        """Refuse to continue unless this run still holds the attempt's lease."""
        await self._require_current_lease(ToolName.RUN_PROCESS)

    @property
    def journal(self) -> tuple[ToolJournalEntry, ...]:
        """Every call this run made, in order, for the durable attempt artifact."""
        return tuple(self._journal)

    async def execute(self, call: ToolCall) -> ToolResult:
        """Execute one typed call, fencing and budgeting every mutating one."""
        started = datetime.now(tz=UTC)
        match call:
            case ReadFile():
                result = await self._read(call)
            case WriteFile():
                await self._require_current_lease(ToolName.WRITE_FILE)
                result = await self._write(call)
            case RunProcess():
                await self._require_current_lease(ToolName.RUN_PROCESS)
                result = await self._run(call)
        elapsed = (datetime.now(tz=UTC) - started).total_seconds()
        stamped = result.model_copy(update={"duration_seconds": elapsed})
        self._record(stamped, occurred_at=started)
        return stamped

    async def inspect(self, *, command: str, timeout_seconds: int) -> WorkspaceExecution:
        """Run one read-only controller command in the checkout, outside the budgets.

        Artifact extraction reads the workspace after the run's work has ended,
        including after a budget refusal, so it is neither charged nor journaled
        as attempt work.
        """
        return await self._sandboxes.run(
            self._handle,
            script=command,
            cwd=self._absolute(".", allow_root=True),
            timeout_seconds=timeout_seconds,
            sudo=False,
        )

    async def _read(self, call: ReadFile) -> ToolResult:
        body = await self._filesystem.read_text(path=self._relative_path(call.path))
        return ToolResult(
            tool=ToolName.READ_FILE,
            subject=call.path,
            output=body,
            exit_code=None,
            duration_seconds=0.0,
        )

    async def _write(self, call: WriteFile) -> ToolResult:
        path = self._absolute(call.path, allow_root=False)
        body = call.content.encode("utf-8")
        self._budgets.consume_artifact_bytes(len(body))
        await self._sandboxes.write_file(self._handle, path=path, content=body)
        return ToolResult(
            tool=ToolName.WRITE_FILE,
            subject=call.path,
            output=f"wrote {len(body)} bytes",
            exit_code=None,
            duration_seconds=0.0,
        )

    async def _run(self, call: RunProcess) -> ToolResult:
        self._budgets.consume_process_execution()
        execution = await self._sandboxes.run(
            self._handle,
            script=call.command,
            cwd=self._absolute(call.working_directory, allow_root=True),
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

    async def _require_current_lease(self, tool: ToolName) -> None:
        """Refuse a mutating call whose run no longer holds the attempt's lease."""
        lease = self._specification.lease
        async with self._database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select fencing_token from public.environment_leases
                where id = %s and implementation_attempt_id = %s and state = 'active'
                """,
                (lease.lease_id, self._specification.implementation_attempt_id),
            )
            row: DictRow | None = await cur.fetchone()
        if row is None or as_int(row, "fencing_token") != self._fencing_token:
            _logger.warning(
                "implementation.tool.stale_lease",
                tool=tool.value,
                attempt_id=str(self._specification.implementation_attempt_id),
                lease_id=str(lease.lease_id),
                fencing_token=self._fencing_token,
            )
            raise LeaseLostError(
                lease_kind="environment",
                lease_id=lease.lease_id,
                fencing_token=self._fencing_token,
            )

    def _absolute(self, path: str, *, allow_root: bool) -> str:
        """Resolve a repository-relative path inside the checkout, or refuse it."""
        root = self._root
        if allow_root and path in (".", ""):
            return root
        try:
            relative = normalize(path)
        except PathEscapesRepositoryError as error:
            raise ToolCallRefusedError(path, str(error)) from None
        return f"{root}/{relative}"

    def _relative_path(self, path: str) -> str:
        """Normalize a repository-relative path, or refuse one that leaves the checkout."""
        try:
            return normalize(path)
        except PathEscapesRepositoryError as error:
            raise ToolCallRefusedError(path, str(error)) from None

    def record_agent_activity(self, *, tool: ToolName, subject: str, occurred_at: datetime) -> None:
        """Journal one operation the agent performed inside the sandbox by itself.

        The harness transcript is the source; the output is not carried, only the
        subject, so the journal stays a record of what was touched and run.
        """
        self._journal.append(
            ToolJournalEntry(
                sequence=len(self._journal) + 1,
                occurred_at=occurred_at,
                tool=tool,
                subject=subject,
                exit_code=None,
                output_bytes=0,
            )
        )

    def _record(self, result: ToolResult, *, occurred_at: datetime) -> None:
        self._journal.append(
            ToolJournalEntry(
                sequence=len(self._journal) + 1,
                occurred_at=occurred_at,
                tool=result.tool,
                subject=result.subject,
                exit_code=result.exit_code,
                output_bytes=len(result.output.encode("utf-8")),
            )
        )


def journal_payload(entries: Sequence[ToolJournalEntry]) -> list[Mapping[str, object]]:
    """Render the journal for the durable artifact."""
    return [entry.model_dump(mode="json") for entry in entries]
