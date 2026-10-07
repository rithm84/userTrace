"""The typed attempt specification the control plane dispatches.

One specification describes one Implementation Attempt completely: which
Product Signal and attempt it belongs to, which gate decision and async
authorization authorize it, the repository and the pinned base revision, the
frozen approved scope with its required checks and budgets, the pinned policy
and gate revisions, the context-bundle references, the code findings the gate
cited, and the current fencing token. A worker that cannot build this object does not start.

Nothing here is a credential. The repository clone token is injected at runtime
by the worker that holds it and never enters this record, a log, or an artifact.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.gate.scope import ApprovedScope

__all__ = ["AttemptSpecification", "CodeFindingHint", "LeaseBinding", "RepositoryCheckout"]


class SpecificationModel(BaseModel):
    """Base for the specification parts: immutable and closed to extra fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class LeaseBinding(SpecificationModel):
    """The attempt-scoped Environment Lease every mutating operation carries."""

    lease_id: UUID
    fencing_token: Annotated[int, Field(ge=1)]


class RepositoryCheckout(SpecificationModel):
    """Where the authorized repository comes from and which commit is pinned."""

    repository_ref: str
    """`owner/name` of the one authorized repository."""
    clone_url: str
    """HTTPS clone URL. Credentials are injected at clone time, never stored here."""
    base_revision: str
    """The exact commit the gate approved against and the workspace checks out."""
    workspace_root: str
    """Absolute path inside the workspace that holds the checkout."""
    application_port: Annotated[int, Field(ge=1, le=65535)]
    """The one port a signed preview URL for this repository may ever expose."""


class CodeFindingHint(SpecificationModel):
    """One exact-revision finding the gate cited: where the pre-gate investigation
    found code that bears on the change. A hint for the coding agent, never a fact
    the control plane relies on: the agent reads the exact lines before using it.
    """

    path: str
    first_line: Annotated[int, Field(ge=1)]
    last_line: Annotated[int, Field(ge=1)]
    note: str


class AttemptSpecification(SpecificationModel):
    """Everything one Implementation Attempt run needs, all of it durable."""

    client_id: UUID
    product_signal_id: UUID
    implementation_attempt_id: UUID
    gate_decision_id: UUID
    interview_session_id: UUID | None
    """The live session whose progress projection this attempt updates, if any."""
    pinned_configuration_release_id: UUID
    checkout: RepositoryCheckout
    approved_scope: ApprovedScope
    lease: LeaseBinding
    workspace_snapshot_id: str
    context_bundle_refs: tuple[str, ...]
    code_findings: tuple[CodeFindingHint, ...]
    """The findings the gate decision cited, in path order."""
    callback_hosts: tuple[str, ...]
    """uTrace endpoints the workspace network wall permits."""

    def scope_digest(self) -> str:
        """The frozen scope digest every scope check and artifact records."""
        return self.approved_scope.digest()
