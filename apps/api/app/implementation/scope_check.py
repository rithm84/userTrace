"""The diff-versus-approved-scope check, run after every edit cycle.

Scope never grows inside the workspace. After each cycle the controller takes
the working diff and measures it against the frozen approved scope: permitted
code surfaces, blocked surfaces, file and line bounds, and the dependency
policy. A mechanical violation returns for correction while budget remains; a
persistent one, or a discovery that the solution needs a different outcome,
stops mutation and returns the evidence to the Implementation Gate.

Blocked paths stay readable — the attempt often needs their interfaces — but
they can never appear in the accepted diff. This module is the one place that
distinction is enforced, so there is no second, softer rule anywhere else.

Everything here is pure: a unified diff and a scope in, a typed result out.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.gate.paths import PathEscapesRepositoryError, within_any_prefix
from app.gate.scope import ApprovedScope, DependencyPolicy

__all__ = [
    "ChangedFile",
    "ScopeCheckResult",
    "ScopeViolation",
    "ViolationKind",
    "added_manifest_lines",
    "check_diff_against_scope",
    "parse_unified_diff",
]

_DIFF_HEADER = re.compile(r"^diff --git a/(?P<old>.+?) b/(?P<new>.+)$")
_HUNK = re.compile(r"^@@ ")


class ViolationKind(StrEnum):
    """The closed set of scope violations a cycle can produce."""

    OUTSIDE_PERMITTED_PATHS = "outside_permitted_paths"
    BLOCKED_CODE_SURFACE = "blocked_code_surface"
    PATH_ESCAPES_REPOSITORY = "path_escapes_repository"
    DEPENDENCY_POLICY = "dependency_policy"
    FILE_BOUND_EXCEEDED = "file_bound_exceeded"


class ScopeModelBase(BaseModel):
    """Base for the scope-check records: immutable and closed to extra fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ChangedFile(ScopeModelBase):
    """One file the working diff touches, with its added and removed line counts."""

    path: str
    added_lines: int
    removed_lines: int


class ScopeViolation(ScopeModelBase):
    """One refusal, naming the exact subject that broke the frozen scope."""

    kind: ViolationKind
    subject: str
    detail: str


class ScopeCheckResult(ScopeModelBase):
    """The result of measuring one working diff against the approved scope."""

    passed: bool
    scope_digest: str
    changed_files: tuple[ChangedFile, ...]
    violations: tuple[ScopeViolation, ...]

    @property
    def changed_paths(self) -> tuple[str, ...]:
        """Every path the diff touches, in diff order."""
        return tuple(item.path for item in self.changed_files)


def parse_unified_diff(diff: str) -> tuple[ChangedFile, ...]:
    """Parse `git diff` output into the files it touches and their line counts.

    A rename shows both sides; both are reported, because an accepted diff that
    moves a file out of a permitted surface is still a change to both surfaces.
    """
    files: list[ChangedFile] = []
    current: list[str] = []
    added = 0
    removed = 0
    in_hunk = False

    def flush() -> None:
        nonlocal added, removed, in_hunk
        for path in current:
            files.append(ChangedFile(path=path, added_lines=added, removed_lines=removed))
        current.clear()
        added = 0
        removed = 0
        in_hunk = False

    for line in diff.splitlines():
        header = _DIFF_HEADER.match(line)
        if header is not None:
            flush()
            old, new = header.group("old"), header.group("new")
            current.extend([new] if old == new else [old, new])
            continue
        if _HUNK.match(line):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    flush()
    return tuple(files)


def check_diff_against_scope(
    *, diff: str, scope: ApprovedScope, added_dependency_lines: Sequence[str]
) -> ScopeCheckResult:
    """Measure one working diff against the frozen approved scope.

    `added_dependency_lines` are the lines the diff adds to the scope's
    dependency manifests. The dependency policy is evaluated against them
    directly, so a manifest edit that adds nothing is not a policy event.
    """
    changed = parse_unified_diff(diff)
    violations: list[ScopeViolation] = []
    violations.extend(_surface_violations(changed, scope))
    violations.extend(_bound_violations(changed, scope))
    violations.extend(_dependency_violations(added_dependency_lines, scope))
    return ScopeCheckResult(
        passed=not violations,
        scope_digest=scope.digest(),
        changed_files=changed,
        violations=tuple(violations),
    )


def _surface_violations(
    changed: Sequence[ChangedFile], scope: ApprovedScope
) -> Sequence[ScopeViolation]:
    violations: list[ScopeViolation] = []
    for item in changed:
        try:
            if scope.blocked_paths and within_any_prefix(item.path, scope.blocked_paths):
                violations.append(
                    ScopeViolation(
                        kind=ViolationKind.BLOCKED_CODE_SURFACE,
                        subject=item.path,
                        detail=(
                            "a blocked code surface is readable but must never appear "
                            "in the accepted diff"
                        ),
                    )
                )
                continue
            if not within_any_prefix(item.path, scope.permitted_paths):
                violations.append(
                    ScopeViolation(
                        kind=ViolationKind.OUTSIDE_PERMITTED_PATHS,
                        subject=item.path,
                        detail="the path is outside every permitted code surface",
                    )
                )
        except PathEscapesRepositoryError as error:
            violations.append(
                ScopeViolation(
                    kind=ViolationKind.PATH_ESCAPES_REPOSITORY,
                    subject=item.path,
                    detail=str(error),
                )
            )
    return violations


def _bound_violations(
    changed: Sequence[ChangedFile], scope: ApprovedScope
) -> Sequence[ScopeViolation]:
    violations: list[ScopeViolation] = []
    if len(changed) > scope.budgets.changed_files:
        violations.append(
            ScopeViolation(
                kind=ViolationKind.FILE_BOUND_EXCEEDED,
                subject=str(len(changed)),
                detail=f"the approved scope bounds the diff at {scope.budgets.changed_files} files",
            )
        )
    return violations


def _dependency_violations(
    added_dependency_lines: Sequence[str], scope: ApprovedScope
) -> Sequence[ScopeViolation]:
    """Evaluate added manifest lines against the frozen `none|allowlist|open` policy."""
    if not added_dependency_lines:
        return ()
    if scope.dependency_policy is DependencyPolicy.OPEN:
        return ()
    if scope.dependency_policy is DependencyPolicy.NONE:
        return tuple(
            ScopeViolation(
                kind=ViolationKind.DEPENDENCY_POLICY,
                subject=line.strip(),
                detail="the approved dependency policy is 'none': no addition is permitted",
            )
            for line in added_dependency_lines
        )
    permitted = {
        f'"{item.package_name}": "{item.exact_version}"'
        for item in scope.permitted_dependencies
    }
    return tuple(
        ScopeViolation(
            kind=ViolationKind.DEPENDENCY_POLICY,
            subject=line.strip(),
            detail="the addition is not on the frozen allowlist at that exact version",
        )
        for line in added_dependency_lines
        if line.strip().rstrip(",") not in permitted
    )


def added_manifest_lines(diff: str, scope: ApprovedScope) -> tuple[str, ...]:
    """Return the lines the diff adds to the scope's dependency manifests."""
    lines: list[str] = []
    active = False
    for line in diff.splitlines():
        header = _DIFF_HEADER.match(line)
        if header is not None:
            active = header.group("new") in scope.dependency_manifest_paths
            continue
        if active and line.startswith("+") and not line.startswith("+++"):
            body = line[1:].strip()
            if body:
                lines.append(body)
    return tuple(lines)
