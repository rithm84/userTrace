"""The typed Case File core.

The [PRD's Case File definition](../../../../docs/PRD.md#case-file) owns the
sections; this module is their exact shape. Every material claim carries its
citations: a ``CitedStatement`` with no citation cannot be constructed, so
"missing proof" surfaces as explicit uncertainty or a blocker rather than as an
uncited assertion.

Direct personal identifiers never appear. The originating user and their
customer account are opaque client-scoped references the authorized dashboard
resolves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from utrace_contracts.domain.lifecycle import (
    CaseFileVariant,
    CriterionObservability,
    CriterionResult,
    EvidenceClass,
    FlowType,
    GateDecisionKind,
    ValidationOutcome,
)

__all__ = [
    "CaseFileCore",
    "CaseFileMetadata",
    "CheckResult",
    "Citation",
    "CitedStatement",
    "CriterionTrace",
    "CustomerEvidenceSection",
    "GateDecisionSection",
    "ImplementationSection",
    "NextActionSection",
    "ProductTraceSection",
    "RiskSection",
    "SummarySection",
    "WorkflowRun",
    "opaque_ref",
]

CORE_SCHEMA_REVISION = 1
"""Bumped when the sealed shape changes; every core records the one it used."""


def opaque_ref(*, kind: str, identifier: UUID) -> str:
    """Build the client-scoped opaque reference the dashboard resolves.

    An identifier, not a person: knowing it grants nothing, and the published
    Case File carries no display name, email, or Discord handle.
    """
    return f"{kind}:{identifier}"


class _Section(BaseModel):
    """Frozen, closed section shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Citation(_Section):
    """One authorized evidence slice a claim rests on."""

    evidence_record_id: UUID
    evidence_class: EvidenceClass
    source_ref: str


class CitedStatement(_Section):
    """One material claim and the evidence it cites. At least one citation."""

    statement: str = Field(min_length=1)
    citations: tuple[Citation, ...] = Field(min_length=1)


class CaseFileMetadata(_Section):
    """What this Case File is about and which exact records it was built from."""

    schema_revision: int
    context_revision: int
    client_id: UUID
    product_signal_id: UUID
    originating_user_ref: str
    customer_account_ref: str
    evidence_window_id: UUID
    gate_decision_id: UUID | None
    implementation_attempt_ids: tuple[UUID, ...]
    preview_version_id: UUID | None
    validation_attempt_id: UUID | None
    narrative_source: Literal["model_drafted", "evidence_derived"]


class SummarySection(_Section):
    """Product Signal, user, account, and validation result."""

    title: str = Field(min_length=1)
    flow_type: FlowType
    concern: CitedStatement
    originating_user_ref: str
    customer_account_ref: str
    validation_outcome: ValidationOutcome | None


class CustomerEvidenceSection(_Section):
    """What the customer reported and said, and what the product did to them."""

    source_message: CitedStatement
    interview_excerpts: tuple[CitedStatement, ...]
    expected_behavior: CitedStatement
    actual_behavior: CitedStatement
    impact: CitedStatement
    workaround: CitedStatement | None


class CriterionTrace(_Section):
    """One acceptance criterion with its criterion-level evidence and limits."""

    criterion_id: UUID
    ordinal: int
    statement: str
    observability: CriterionObservability
    result: CriterionResult
    user_confirmed: bool
    observed_evidence: tuple[Citation, ...]
    contradicting_evidence: tuple[Citation, ...]
    coverage_limits: tuple[str, ...]
    reason: str


class WorkflowRun(_Section):
    """The observed milestones of the agreed workflow on one Preview Version."""

    preview_version_id: UUID | None
    milestones: tuple[Citation, ...]


class ProductTraceSection(_Section):
    """Criterion-level observed evidence, the workflow comparison, and limits."""

    workflow_ref: str
    criteria: tuple[CriterionTrace, ...]
    before: WorkflowRun
    after: WorkflowRun
    coverage_limits: tuple[str, ...]


class GateDecisionSection(_Section):
    """The Implementation Gate's frozen decision and scope."""

    decision: GateDecisionKind
    decided_at: datetime
    reason: str
    approved_scope: tuple[str, ...]
    excluded_scope: tuple[str, ...]
    required_checks: tuple[str, ...]


class CheckResult(_Section):
    """One pinned check and how it ended."""

    name: str
    outcome: Literal["passed", "failed", "skipped"]


class ImplementationSection(_Section):
    """The exact identities a reviewer needs to trust the delivered change."""

    repository_ref: str
    branch_ref: str | None
    pull_request_ref: str | None
    pull_request_number: int | None
    implementation_attempt_id: UUID
    preview_version_id: UUID
    source_commit: str
    source_digest: str
    build_digest: str
    base_revision: str
    dataset_manifest_digest: str
    files_touched: tuple[str, ...]
    checks: tuple[CheckResult, ...]
    cleanup_state: str


class RiskSection(_Section):
    """Blast radius, assumptions, and the follow-up checks a human should run."""

    blast_radius: CitedStatement
    assumptions: tuple[str, ...]
    follow_up_checks: tuple[str, ...]


class NextActionSection(_Section):
    """The one action the client product team should take."""

    action: Literal["review_pull_request", "assess_blocker", "no_action"]
    detail: str = Field(min_length=1)


class CaseFileCore(_Section):
    """The immutable, digest-sealed record the client product team reads."""

    metadata: CaseFileMetadata
    variant: CaseFileVariant
    summary: SummarySection
    customer_evidence: CustomerEvidenceSection
    product_trace: ProductTraceSection | None
    gate_decision: GateDecisionSection | None
    implementation: ImplementationSection | None
    risk: RiskSection
    next_action: NextActionSection
    blocker_reason: str | None
