"""Building and verifying the Case File's immutable core.

Verification is code, not judgement. It checks structure, scope, redaction,
citations, mandatory evidence, and outcome consistency before anything is frozen,
and refuses with the exact failing condition rather than publishing a Case File
that says less than it appears to.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from utrace_contracts import canonicalize
from utrace_contracts.domain.lifecycle import (
    CaseFileVariant,
    CriterionResult,
    GateDecisionKind,
    ValidationOutcome,
)

from app.errors import GuardRejectedError
from app.outputs.bundle import DraftingBundle
from app.outputs.delivery import DeliveryRow
from app.outputs.drafting import DraftedNarrative, cited_statement
from app.outputs.sections import (
    CORE_SCHEMA_REVISION,
    CaseFileCore,
    CaseFileMetadata,
    Citation,
    CitedStatement,
    CriterionTrace,
    CustomerEvidenceSection,
    GateDecisionSection,
    ImplementationSection,
    NextActionSection,
    ProductTraceSection,
    RiskSection,
    SummarySection,
    WorkflowRun,
)
from app.validation.evidence import BeforeAfterComparison, WorkflowObservation

__all__ = [
    "build_core",
    "canonical_core",
    "verify_core",
]

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_DISCORD_HANDLE = re.compile(r"(?<![\w/])@[A-Za-z0-9._]{2,32}\b")

def _refuse(reason: str) -> GuardRejectedError:
    return GuardRejectedError(machine="case_file", trigger="verify", reason=reason)


def _criterion_traces(
    bundle: DraftingBundle, comparison: BeforeAfterComparison
) -> tuple[CriterionTrace, ...]:
    """Build the criterion-level product trace from the recorded proofs."""
    limits = comparison.coverage_limits
    traces: list[CriterionTrace] = []
    for proof in bundle.proofs:
        traces.append(
            CriterionTrace(
                criterion_id=proof.criterion.id,
                ordinal=proof.criterion.ordinal,
                statement=proof.criterion.statement,
                observability=proof.criterion.observability,
                result=CriterionResult(proof.result),
                user_confirmed=proof.user_confirmed,
                observed_evidence=tuple(
                    bundle.by_id(item).citation()
                    for item in proof.supporting_evidence_record_ids
                ),
                contradicting_evidence=tuple(
                    bundle.by_id(item).citation()
                    for item in proof.contradicting_evidence_record_ids
                ),
                coverage_limits=limits,
                reason=proof.reason,
            )
        )
    return tuple(traces)


def _workflow_run(
    bundle: DraftingBundle,
    observations: Sequence[WorkflowObservation],
    preview_version_id: UUID | None,
) -> WorkflowRun:
    """Render one side of the before-and-after comparison as citations."""
    return WorkflowRun(
        preview_version_id=preview_version_id,
        milestones=tuple(
            bundle.by_id(observation.evidence_record_id).citation()
            for observation in observations
        ),
    )


def build_core(
    *,
    bundle: DraftingBundle,
    narrative: DraftedNarrative,
    comparison: BeforeAfterComparison | None,
    delivery: DeliveryRow | None,
    narrative_source: str,
) -> CaseFileCore:
    """Compose the typed Case File core from the frozen bundle and its narrative."""
    if narrative_source not in {"model_drafted", "evidence_derived"}:
        raise _refuse(f"unknown narrative source '{narrative_source}'")
    implementation = bundle.implementation
    product_trace: ProductTraceSection | None = None
    if comparison is not None and bundle.proofs:
        product_trace = ProductTraceSection(
            workflow_ref=bundle.workflow_ref,
            criteria=_criterion_traces(bundle, comparison),
            before=_workflow_run(
                bundle, comparison.base_observations, comparison.base_preview_version_id
            ),
            after=_workflow_run(
                bundle,
                comparison.candidate_observations,
                comparison.candidate_preview_version_id,
            ),
            coverage_limits=comparison.coverage_limits + tuple(narrative.coverage_limits),
        )

    gate_section: GateDecisionSection | None = None
    if bundle.gate is not None:
        gate_section = GateDecisionSection(
            decision=bundle.gate.decision,
            decided_at=bundle.gate.decided_at,
            reason=bundle.gate.blocker_reason or "the gate approved the frozen scope",
            approved_scope=bundle.gate.approved_scope,
            excluded_scope=bundle.gate.excluded_scope,
            required_checks=bundle.gate.required_checks,
        )

    implementation_section: ImplementationSection | None = None
    if implementation is not None:
        implementation_section = ImplementationSection(
            repository_ref=implementation.repository_ref,
            branch_ref=None if delivery is None else delivery.branch_ref,
            pull_request_ref=None if delivery is None else delivery.pull_request_ref,
            pull_request_number=(
                None if delivery is None else delivery.pull_request_number
            ),
            implementation_attempt_id=implementation.implementation_attempt_id,
            preview_version_id=implementation.preview_version_id,
            source_commit=implementation.source_commit,
            source_digest=implementation.source_digest,
            build_digest=implementation.build_digest,
            base_revision=implementation.base_revision,
            dataset_manifest_digest=implementation.dataset_manifest_digest,
            files_touched=implementation.files_touched,
            checks=implementation.checks,
            cleanup_state=implementation.cleanup_state,
        )

    return CaseFileCore(
        metadata=CaseFileMetadata(
            schema_revision=CORE_SCHEMA_REVISION,
            context_revision=bundle.context_revision,
            client_id=bundle.client_id,
            product_signal_id=bundle.product_signal_id,
            originating_user_ref=bundle.originating_user_ref,
            customer_account_ref=bundle.customer_account_ref,
            evidence_window_id=bundle.evidence_window_id,
            gate_decision_id=(
                None if bundle.gate is None else bundle.gate.gate_decision_id
            ),
            implementation_attempt_ids=bundle.implementation_attempt_ids,
            preview_version_id=(
                None if implementation is None else implementation.preview_version_id
            ),
            validation_attempt_id=bundle.validation_attempt_id,
            narrative_source=(
                "model_drafted" if narrative_source == "model_drafted" else "evidence_derived"
            ),
        ),
        variant=bundle.variant,
        summary=SummarySection(
            title=narrative.title,
            flow_type=bundle.flow_type,
            concern=cited_statement(narrative.concern, bundle),
            originating_user_ref=bundle.originating_user_ref,
            customer_account_ref=bundle.customer_account_ref,
            validation_outcome=bundle.validation_outcome,
        ),
        customer_evidence=CustomerEvidenceSection(
            source_message=CitedStatement(
                statement=bundle.source_message.summary,
                citations=(bundle.source_message.citation(),),
            ),
            interview_excerpts=tuple(
                cited_statement(claim, bundle) for claim in narrative.interview_excerpts
            ),
            expected_behavior=cited_statement(narrative.expected_behavior, bundle),
            actual_behavior=cited_statement(narrative.actual_behavior, bundle),
            impact=cited_statement(narrative.impact, bundle),
            workaround=(
                None
                if narrative.workaround is None
                else cited_statement(narrative.workaround, bundle)
            ),
        ),
        product_trace=product_trace,
        gate_decision=gate_section,
        implementation=implementation_section,
        risk=RiskSection(
            blast_radius=cited_statement(narrative.blast_radius, bundle),
            assumptions=tuple(narrative.assumptions),
            follow_up_checks=tuple(narrative.follow_up_checks),
        ),
        next_action=NextActionSection(
            action=_next_action(bundle.variant),
            detail=narrative.next_action_detail,
        ),
        blocker_reason=bundle.blocker_reason,
    )


def _next_action(
    variant: CaseFileVariant,
) -> Literal["review_pull_request", "assess_blocker", "no_action"]:
    """Return the one action this variant asks the client product team to take."""
    if variant is CaseFileVariant.VALIDATED_IMPLEMENTATION:
        return "review_pull_request"
    if variant is CaseFileVariant.BLOCKED:
        return "assess_blocker"
    return "no_action"


def canonical_core(core: CaseFileCore) -> bytes:
    """Return the RFC 8785 canonical bytes the immutable core digest covers."""
    return canonicalize(core.model_dump(mode="json"))


def verify_core(
    core: CaseFileCore, *, bundle: DraftingBundle, forbidden_identifiers: Sequence[str]
) -> None:
    """Refuse a core that fails scope, redaction, evidence, or consistency."""
    if core.metadata.client_id != bundle.client_id:
        raise _refuse("the Case File core names another client")
    if core.metadata.product_signal_id != bundle.product_signal_id:
        raise _refuse("the Case File core names another Product Signal")

    rendered = canonical_core(core).decode("utf-8")
    for identifier in forbidden_identifiers:
        if identifier and identifier in rendered:
            raise _refuse(
                "the Case File core carries a direct personal identifier; the "
                "dashboard resolves opaque client-scoped references instead"
            )
    if _EMAIL.search(rendered):
        raise _refuse("the Case File core carries an email address")
    if _DISCORD_HANDLE.search(rendered):
        raise _refuse("the Case File core carries a Customer Surface handle")

    index = bundle.citation_index()
    for citation in _every_citation(core):
        if citation.evidence_record_id not in index:
            raise _refuse(
                f"citation {citation.evidence_record_id} is outside the frozen bundle"
            )

    _verify_variant(core, bundle)


def _every_citation(core: CaseFileCore) -> tuple[Citation, ...]:
    """Collect every citation the core carries, in no particular order."""
    citations: list[Citation] = [*core.summary.concern.citations]
    evidence = core.customer_evidence
    for statement in (
        evidence.source_message,
        evidence.expected_behavior,
        evidence.actual_behavior,
        evidence.impact,
        *evidence.interview_excerpts,
    ):
        citations.extend(statement.citations)
    if evidence.workaround is not None:
        citations.extend(evidence.workaround.citations)
    citations.extend(core.risk.blast_radius.citations)
    if core.product_trace is not None:
        for criterion in core.product_trace.criteria:
            citations.extend(criterion.observed_evidence)
            citations.extend(criterion.contradicting_evidence)
        citations.extend(core.product_trace.before.milestones)
        citations.extend(core.product_trace.after.milestones)
    return tuple(citations)


def _verify_variant(core: CaseFileCore, bundle: DraftingBundle) -> None:
    """Check the mandatory evidence and outcome consistency of one variant."""
    if core.variant is CaseFileVariant.VALIDATED_IMPLEMENTATION:
        if bundle.validation_outcome is not ValidationOutcome.VALIDATED:
            raise _refuse(
                "a validated_implementation Case File requires a validated outcome; "
                f"the Validation Attempt recorded "
                f"{bundle.validation_outcome.value if bundle.validation_outcome else 'none'}"
            )
        if core.implementation is None:
            raise _refuse(
                "a validated_implementation Case File must record its implementation"
            )
        if core.product_trace is None or not core.product_trace.criteria:
            raise _refuse(
                "a validated_implementation Case File must record criterion-level "
                "validation evidence"
            )
        unmet = [
            trace.statement
            for trace in core.product_trace.criteria
            if trace.result is not CriterionResult.MET
        ]
        if unmet:
            raise _refuse(
                "a validated_implementation Case File cannot carry an unmet "
                f"criterion: {'; '.join(unmet)}"
            )
        if core.next_action.action != "review_pull_request":
            raise _refuse("a validated implementation's next action is to review the PR")
        return

    if core.variant is CaseFileVariant.NO_IMPLEMENTATION:
        if core.gate_decision is None:
            raise _refuse("a no_implementation Case File must record the gate decision")
        if core.gate_decision.decision is not GateDecisionKind.NO_IMPLEMENTATION:
            raise _refuse(
                "a no_implementation Case File must cite a no_implementation gate "
                f"decision, not {core.gate_decision.decision.value}"
            )
        if core.implementation is not None:
            raise _refuse("a no_implementation Case File contains no candidate or PR")
        return

    if core.blocker_reason is None:
        raise _refuse("a blocked Case File must record its exact blocker")
