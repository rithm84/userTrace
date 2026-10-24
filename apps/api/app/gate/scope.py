"""The approved-scope object an Implementation Gate approval freezes.

One approval freezes exactly one of these for exactly one Implementation
Attempt: the intended user-visible outcome, the acceptance criteria, the
authorized repository and its pinned base revision, the in-scope and excluded
changes, the permitted and blocked code surfaces, the required checks, the
dependency policy, the resource budgets, and the evidence revision the decision
cited. Nothing inside the workspace may widen it, and a later policy or gate
revision never rewrites it.

The object is stored across the three `gate_decisions` columns the schema
defines (`approved_scope`, `required_checks`, `budgets`). `to_columns` and
`from_columns` own that split so the object stays one source of truth in code.
Its digest is RFC 8785 canonical JSON over all three parts, which is what the
async authorization record binds to.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field
from utrace_contracts.canonical import payload_digest

__all__ = [
    "AcceptanceCriterion",
    "ApprovedScope",
    "DependencyPolicy",
    "PermittedDependency",
    "RequiredCheck",
    "ResourceBudgets",
    "ScopeColumns",
]


class DependencyPolicy(StrEnum):
    """What the pinned Autonomy Policy permits an attempt to add to the manifest."""

    NONE = "none"
    ALLOWLIST = "allowlist"
    OPEN = "open"


class ScopeModel(BaseModel):
    """Base for the frozen scope parts: immutable and closed to extra fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class AcceptanceCriterion(ScopeModel):
    """One frozen acceptance criterion, at the criteria revision the gate cited."""

    criterion_id: str
    ordinal: Annotated[int, Field(ge=0)]
    statement: str
    observability: str


class RequiredCheck(ScopeModel):
    """One command the workspace runs on the clean base and again on the candidate."""

    name: str
    command: str
    working_directory: str
    timeout_seconds: Annotated[int, Field(ge=1)]
    after: tuple[str, ...]
    """Earlier checks that must finish before this one starts; the rest run concurrently."""


class PermittedDependency(ScopeModel):
    """One dependency an `allowlist` policy permits, at an exact version."""

    manifest_path: str
    package_name: str
    exact_version: str
    license_class: str


class ResourceBudgets(ScopeModel):
    """The pinned execution budgets. Every one of them is enforced at runtime."""

    elapsed_seconds: Annotated[int, Field(ge=1)]
    agent_turns: Annotated[int, Field(ge=1)]
    """Agent commands and edited files across every authoring cycle."""
    agent_cost_cents: Annotated[int, Field(ge=1)]
    """Model spend across every authoring cycle, in US cents."""
    process_executions: Annotated[int, Field(ge=1)]
    artifact_bytes: Annotated[int, Field(ge=1)]
    correction_cycles: Annotated[int, Field(ge=1)]
    changed_files: Annotated[int, Field(ge=1)]


class ScopeColumns(ScopeModel):
    """The three `gate_decisions` jsonb columns one approved scope occupies."""

    approved_scope: Mapping[str, object]
    required_checks: Mapping[str, object]
    budgets: Mapping[str, object]


class ApprovedScope(ScopeModel):
    """Everything one approval freezes for one Implementation Attempt."""

    intended_outcome: str
    """The user-visible result, in the originating user's language."""
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    acceptance_criteria_revision: Annotated[int, Field(ge=0)]
    classification_revision: Annotated[int, Field(ge=0)]
    evidence_revision: Annotated[int, Field(ge=0)]
    repository_ref: str
    """`owner/name` of the one authorized repository."""
    base_revision: str
    """The exact commit the gate approved against and the workspace checks out."""
    in_scope_changes: tuple[str, ...]
    excluded_changes: tuple[str, ...]
    permitted_paths: tuple[str, ...]
    """Repository-relative path prefixes the accepted diff may touch."""
    blocked_paths: tuple[str, ...]
    """Prefixes that stay readable but must never appear in the accepted diff."""
    required_checks: tuple[RequiredCheck, ...]
    dependency_policy: DependencyPolicy
    permitted_dependencies: tuple[PermittedDependency, ...]
    dependency_manifest_paths: tuple[str, ...]
    """Manifest and lockfile paths whose changes the dependency policy governs."""
    budgets: ResourceBudgets

    def to_columns(self) -> ScopeColumns:
        """Split the object into the three columns the gate decision row stores."""
        body = self.model_dump(mode="json")
        checks = body.pop("required_checks")
        budgets = body.pop("budgets")
        return ScopeColumns(
            approved_scope=body,
            required_checks={"checks": checks},
            budgets=budgets,
        )

    @classmethod
    def from_columns(
        cls,
        *,
        approved_scope: Mapping[str, object],
        required_checks: Mapping[str, object],
        budgets: Mapping[str, object],
    ) -> Self:
        """Rebuild the object from the three stored columns."""
        return cls.model_validate(
            {
                **approved_scope,
                "required_checks": required_checks["checks"],
                "budgets": budgets,
            }
        )

    def digest(self) -> str:
        """Return the canonical digest the async authorization record binds to."""
        return f"sha256:{payload_digest(self.model_dump(mode='json'))}"
