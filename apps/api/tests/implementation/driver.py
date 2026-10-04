"""The deterministic driver and the stand-in preview-runtime port.

`ScriptedChangeAuthor` implements the same `ChangeAuthor` protocol
`AgentAuthor` does, and applies its edits through the same typed tool
executor: it reads the exact files first, as the coding contract requires, then
writes complete file contents. Driving the attempt this way exercises the
workspace, the checkout, the fencing, the budgets, the scope check, the checks,
the artifacts, and the candidate pipeline end to end. The one thing it does not
exercise is the model's choice of what to write, which is the point: the patch
is fixed, so the run is reproducible.

`RecordingPreviewRuntime` stands in for the preview-runtime workstream's
adapter. It records the exact `CandidateHandoff` it was given and creates the
candidate Preview Runtime Allocation row, so a test can assert the input shape
this workstream produces. It deploys nothing, because deployment belongs to that
workstream.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.db.pool import Database
from app.ids import uuid7
from app.implementation.agent_author import AuthoredCycle, CycleFeedback
from app.implementation.ports import (
    CandidateHandoff,
    PrebuiltCandidate,
    PreviewRuntimeAcceptance,
)
from app.implementation.specification import AttemptSpecification
from app.implementation.tools import ReadFile, ToolExecutor, ToolName, WriteFile

__all__ = [
    "MODIFIED_PATHS",
    "SAVED_VIEWS_PATCH",
    "RecordingPreviewRuntime",
    "ScopeChangeAuthor",
    "ScriptedChangeAuthor",
    "Secret",
    "blocked_path_patch",
    "load_patch",
]


@dataclass(frozen=True, slots=True)
class Secret:
    """A live credential that a failing test's traceback prints as `Secret()`."""

    value: str = field(repr=False)

FIXTURES = Path(__file__).parent / "fixtures" / "saved_views"

SAVED_VIEWS_PATCH: Mapping[str, str] = {
    "src/lib/dashboard/saved-views.ts": "saved-views.ts",
    "src/components/dashboard/SavedViewsBar.tsx": "SavedViewsBar.tsx",
    "src/components/dashboard/ConversionDashboard.tsx": "ConversionDashboard.tsx",
    "tests/unit/saved-views.test.ts": "saved-views.test.ts",
}
"""Repository path to the fixture file that holds its complete new contents.

The four files are a minimal real Saved Conversion Views implementation: a pure
persistence and restoration module, the save control and the reopen selector,
the dashboard wiring, and the unit test that covers the round trip. They were
verified against the pinned base revision with the repository's own lint,
typecheck, test, and build commands before being frozen here.
"""

MODIFIED_PATHS: tuple[str, ...] = (
    "src/components/dashboard/ConversionDashboard.tsx",
)
"""The one file the patch modifies; the rest it creates."""


def load_patch(files: Mapping[str, str]) -> dict[str, str]:
    """Read the fixture bodies for one patch."""
    return {path: (FIXTURES / name).read_text(encoding="utf-8") for path, name in files.items()}


def blocked_path_patch() -> dict[str, str]:
    """A one-file patch that writes into a blocked code surface.

    `src/lib/utrace/chart-state.ts` is readable — the attempt often needs the
    chart-state interface — but the approved scope blocks it, so the diff check
    must catch it after the edit cycle rather than the executor refusing it.
    """
    return {
        "src/lib/utrace/chart-state.ts": (
            "// the attempt must never change a blocked code surface\n"
        )
    }


class ScriptedChangeAuthor:
    """Applies a fixed patch through the typed tool executor, reading before writing."""

    def __init__(self, *, files: Mapping[str, str], modified_paths: Sequence[str]) -> None:
        self._files = dict(files)
        self._modified = tuple(modified_paths)
        self.cycles = 0

    async def author(
        self,
        *,
        executor: ToolExecutor,
        specification: AttemptSpecification,
        feedback: CycleFeedback | None,
    ) -> AuthoredCycle:
        """Read the files the patch modifies, then write every file it carries."""
        del specification, feedback
        self.cycles += 1
        for path in self._modified:
            await executor.execute(ReadFile(tool=ToolName.READ_FILE, path=path))
        for path, body in self._files.items():
            await executor.execute(
                WriteFile(tool=ToolName.WRITE_FILE, path=path, content=body)
            )
        return AuthoredCycle(
            summary=f"applied the fixed patch to {len(self._files)} files",
            changed_paths=tuple(self._files),
            needs_scope_change=False,
            scope_change_reason=None,
        )


class ScopeChangeAuthor:
    """Reports that the approved scope does not cover the work, changing nothing."""

    def __init__(self, *, reason: str) -> None:
        self._reason = reason

    async def author(
        self,
        *,
        executor: ToolExecutor,
        specification: AttemptSpecification,
        feedback: CycleFeedback | None,
    ) -> AuthoredCycle:
        """Stop mutation and return the evidence to the Implementation Gate."""
        del executor, specification, feedback
        return AuthoredCycle(
            summary=self._reason,
            changed_paths=(),
            needs_scope_change=True,
            scope_change_reason=self._reason,
        )


class RecordingPreviewRuntime:
    """Records the prebuilds and the handoff, and creates the candidate allocation row."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self.handoffs: list[CandidateHandoff] = []
        self.prebuilt: list[PrebuiltCandidate] = []
        self.discarded: list[PrebuiltCandidate] = []
        self.adopted: list[PrebuiltCandidate | None] = []

    async def prebuild_candidate(
        self, *, client_id: UUID, files: Mapping[str, str], build_command: str
    ) -> PrebuiltCandidate:
        """Record the staged files a prebuild would build."""
        prebuilt = PrebuiltCandidate(
            runtime_ref=f"recorded-{uuid7().hex}",
            file_digests={
                path: f"sha256:{hashlib.sha256(body.encode('utf-8')).hexdigest()}"
                for path, body in files.items()
            },
        )
        self.prebuilt.append(prebuilt)
        return prebuilt

    async def discard_prebuilt(self, prebuilt: PrebuiltCandidate) -> None:
        """Record that a prebuild was destroyed."""
        self.discarded.append(prebuilt)

    async def deploy_candidate(
        self, *, handoff: CandidateHandoff, prebuilt: PrebuiltCandidate | None
    ) -> PreviewRuntimeAcceptance:
        """Record the exact handoff this workstream produced and accept it."""
        self.handoffs.append(handoff)
        self.adopted.append(prebuilt)
        allocation_id = uuid7()
        async with self._database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.preview_runtime_allocations (
                    id, client_id, product_signal_id, allocation_kind, pool_role, state,
                    preview_version_id, origin_host, fencing_token
                )
                values (%s, %s, %s, 'candidate', 'candidate', 'pending', %s, %s, utrace.next_fencing_token())
                """,
                (
                    allocation_id,
                    handoff.client_id,
                    handoff.product_signal_id,
                    handoff.preview_version_id,
                    f"{allocation_id.hex[:16]}.preview.utrace.dev",
                ),
            )
        return PreviewRuntimeAcceptance(
            preview_version_id=handoff.preview_version_id,
            preview_runtime_allocation_id=allocation_id,
            accepted_at=datetime.now(tz=UTC),
        )

    def only_handoff(self) -> CandidateHandoff:
        """The single handoff this runtime was given."""
        if len(self.handoffs) != 1:
            raise AssertionError(f"expected exactly one handoff, recorded {len(self.handoffs)}")
        return self.handoffs[0]
