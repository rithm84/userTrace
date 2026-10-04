"""The deterministic security screen and scope checks.

Every function here is pure: a bundle and a proposal in, an ordered tuple of
typed findings out. The controller runs them after the bundle exists and before
it commits anything, so a proposal that cites something the bundle does not
carry, names another repository, touches a blocked surface, adds a dependency
the pinned policy forbids, or reaches a forbidden security target is refused by
code rather than by a model's judgement.

A single finding is enough to deny. The screen never repairs a proposal.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from utrace_contracts.domain.lifecycle import FlowType

from app.gate.bundle import GateBundle
from app.gate.paths import PathEscapesRepositoryError, matches_any_pattern, within_any_prefix
from app.gate.proposal import GateBlocker, GateProposal

__all__ = ["ScreenFinding", "check_live_fit", "screen_proposal", "validate_citations"]


class ScreenFinding(BaseModel):
    """One refusal: its typed blocker and the exact subject that caused it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocker: GateBlocker
    subject: str
    detail: str


def validate_citations(bundle: GateBundle, proposal: GateProposal) -> tuple[ScreenFinding, ...]:
    """Check that every identifier the proposal cites exists in the bundle."""
    findings: list[ScreenFinding] = []
    admissible_evidence = bundle.evidence_ids()
    admissible_findings = bundle.finding_ids()

    for raw in proposal.cited_evidence_ids:
        identifier = _as_uuid(raw)
        if identifier is None or identifier not in admissible_evidence:
            findings.append(
                ScreenFinding(
                    blocker=GateBlocker.UNVERIFIABLE_CITATION,
                    subject=raw,
                    detail="the proposal cites an evidence record the bundle does not carry",
                )
            )
    for raw in proposal.cited_finding_ids:
        identifier = _as_uuid(raw)
        if identifier is None or identifier not in admissible_findings:
            findings.append(
                ScreenFinding(
                    blocker=GateBlocker.UNVERIFIABLE_CITATION,
                    subject=raw,
                    detail="the proposal cites a code finding the bundle does not carry",
                )
            )
    findings.extend(_change_citation_findings(bundle, proposal))
    return tuple(findings)


def _change_citation_findings(
    bundle: GateBundle, proposal: GateProposal
) -> tuple[ScreenFinding, ...]:
    """Check each proposed change against the exact-revision findings it cites."""
    findings: list[ScreenFinding] = []
    for change in proposal.proposed_changes:
        if not change.cited_finding_ids:
            findings.append(
                ScreenFinding(
                    blocker=GateBlocker.MISSING_CODE_FINDINGS,
                    subject=change.path,
                    detail="a proposed change cites no exact-revision code finding",
                )
            )
            continue
        for raw in change.cited_finding_ids:
            identifier = _as_uuid(raw)
            finding = None if identifier is None else bundle.finding_by_id(identifier)
            if finding is None:
                findings.append(
                    ScreenFinding(
                        blocker=GateBlocker.UNVERIFIABLE_CITATION,
                        subject=f"{change.path}:{raw}",
                        detail="a proposed change cites a finding the bundle does not carry",
                    )
                )
                continue
            if finding.base_revision != _base_revision(bundle):
                findings.append(
                    ScreenFinding(
                        blocker=GateBlocker.UNVERIFIABLE_CITATION,
                        subject=f"{change.path}:{raw}",
                        detail=(
                            f"the cited finding is at revision {finding.base_revision}, "
                            f"not the bundle's {_base_revision(bundle)}"
                        ),
                    )
                )
    return tuple(findings)


def screen_proposal(
    bundle: GateBundle, proposal: GateProposal, *, base_revision: str
) -> tuple[ScreenFinding, ...]:
    """Run the full deterministic screen over one proposal."""
    findings: list[ScreenFinding] = []
    findings.extend(_readiness_findings(bundle))
    findings.extend(_repository_findings(bundle, proposal, base_revision=base_revision))
    findings.extend(_surface_findings(bundle, proposal))
    findings.extend(_dependency_findings(bundle, proposal))
    if proposal.blocker is not None:
        findings.append(
            ScreenFinding(
                blocker=proposal.blocker,
                subject="proposal",
                detail=proposal.blocker_detail or "the proposal reported a blocker",
            )
        )
    return tuple(findings)


def _readiness_findings(bundle: GateBundle) -> Sequence[ScreenFinding]:
    findings: list[ScreenFinding] = []
    if bundle.established_flow_type is None:
        findings.append(
            ScreenFinding(
                blocker=GateBlocker.STALE_CLASSIFICATION,
                subject=str(bundle.product_signal_id),
                detail="the interview established no final flow classification",
            )
        )
    elif bundle.established_flow_type is not FlowType.FEATURE_REQUEST_FLOW:
        findings.append(
            ScreenFinding(
                blocker=GateBlocker.UNSUPPORTED_FLOW,
                subject=str(bundle.product_signal_id),
                detail=(
                    f"the interview established {bundle.established_flow_type.value}; "
                    "the demo implements Feature Request Flow only"
                ),
            )
        )
    if not bundle.acceptance_criteria:
        findings.append(
            ScreenFinding(
                blocker=GateBlocker.INSUFFICIENT_EVIDENCE,
                subject=str(bundle.product_signal_id),
                detail=(
                    "no acceptance criterion is frozen at criteria revision "
                    f"{bundle.acceptance_criteria_revision}"
                ),
            )
        )
    if not bundle.confirmed_evidence:
        findings.append(
            ScreenFinding(
                blocker=GateBlocker.INSUFFICIENT_EVIDENCE,
                subject=str(bundle.product_signal_id),
                detail="no confirmed user evidence is admissible for this decision",
            )
        )
    if not bundle.code_findings:
        findings.append(
            ScreenFinding(
                blocker=GateBlocker.MISSING_CODE_FINDINGS,
                subject=str(bundle.product_signal_id),
                detail="the read-only investigation recorded no exact-revision findings",
            )
        )
    if not bundle.readiness.prepared:
        findings.append(
            ScreenFinding(
                blocker=GateBlocker.ENVIRONMENT_NOT_READY,
                subject=str(bundle.product_signal_id),
                detail=(
                    "the base Preview Runtime Allocation is "
                    f"{bundle.readiness.base_allocation_state or 'absent'}"
                ),
            )
        )
    if bundle.implementation_count >= bundle.policy.implementation_limit:
        findings.append(
            ScreenFinding(
                blocker=GateBlocker.BUDGET_EXHAUSTED,
                subject=str(bundle.product_signal_id),
                detail=(
                    "the Product Signal already used all "
                    f"{bundle.policy.implementation_limit} of its Implementation Attempts"
                ),
            )
        )
    return findings


def _repository_findings(
    bundle: GateBundle, proposal: GateProposal, *, base_revision: str
) -> Sequence[ScreenFinding]:
    findings: list[ScreenFinding] = []
    for finding in bundle.code_findings:
        if finding.repository_ref != bundle.policy.repository_ref:
            findings.append(
                ScreenFinding(
                    blocker=GateBlocker.UNAUTHORIZED_REPOSITORY,
                    subject=finding.repository_ref,
                    detail=(
                        "a code finding names a repository other than the authorized "
                        f"{bundle.policy.repository_ref}"
                    ),
                )
            )
        if finding.base_revision != base_revision:
            findings.append(
                ScreenFinding(
                    blocker=GateBlocker.UNVERIFIABLE_CITATION,
                    subject=finding.path,
                    detail=(
                        f"a code finding is at revision {finding.base_revision}, not the "
                        f"pinned base revision {base_revision}"
                    ),
                )
            )
    if not proposal.proposed_changes and proposal.recommended_decision != "no_implementation":
        findings.append(
            ScreenFinding(
                blocker=GateBlocker.INSUFFICIENT_EVIDENCE,
                subject="proposal",
                detail="an approval proposal names no file to change",
            )
        )
    return findings


def _surface_findings(bundle: GateBundle, proposal: GateProposal) -> Sequence[ScreenFinding]:
    findings: list[ScreenFinding] = []
    policy = bundle.policy
    for change in proposal.proposed_changes:
        try:
            if not within_any_prefix(change.path, policy.permitted_paths):
                findings.append(
                    ScreenFinding(
                        blocker=GateBlocker.BLOCKED_CODE_SURFACE,
                        subject=change.path,
                        detail="the path is outside every permitted code surface",
                    )
                )
            if policy.blocked_paths and within_any_prefix(change.path, policy.blocked_paths):
                findings.append(
                    ScreenFinding(
                        blocker=GateBlocker.BLOCKED_CODE_SURFACE,
                        subject=change.path,
                        detail="the path is a blocked code surface: readable, never changed",
                    )
                )
            if matches_any_pattern(change.path, policy.forbidden_path_patterns):
                findings.append(
                    ScreenFinding(
                        blocker=GateBlocker.SECURITY_RISK,
                        subject=change.path,
                        detail="the path matches a forbidden security target",
                    )
                )
        except PathEscapesRepositoryError:
            findings.append(
                ScreenFinding(
                    blocker=GateBlocker.SECURITY_RISK,
                    subject=change.path,
                    detail="the path escapes the repository root",
                )
            )
    return findings


def _dependency_findings(bundle: GateBundle, proposal: GateProposal) -> Sequence[ScreenFinding]:
    policy = bundle.policy
    if not proposal.dependency_additions:
        return ()
    if policy.dependency_policy.value == "open":
        return ()
    if policy.dependency_policy.value == "none":
        return tuple(
            ScreenFinding(
                blocker=GateBlocker.DEPENDENCY_POLICY_VIOLATION,
                subject=addition.package_name,
                detail="the pinned dependency policy is 'none': no addition is permitted",
            )
            for addition in proposal.dependency_additions
        )
    permitted = {
        (item.manifest_path, item.package_name, item.exact_version)
        for item in policy.permitted_dependencies
    }
    return tuple(
        ScreenFinding(
            blocker=GateBlocker.DEPENDENCY_POLICY_VIOLATION,
            subject=f"{addition.package_name}@{addition.exact_version}",
            detail="the addition is not on the pinned allowlist at that exact version",
        )
        for addition in proposal.dependency_additions
        if (addition.manifest_path, addition.package_name, addition.exact_version) not in permitted
    )


def check_live_fit(bundle: GateBundle, proposal: GateProposal) -> ScreenFinding | None:
    """Refuse live work the pinned policy does not allow or that would not fit the session.

    The demo runs live implementation or none at all: work that does not fit the
    pinned live window is a `no_implementation` decision, never deferred work.
    """
    policy = bundle.policy
    if not policy.live_implementation_allowed:
        return ScreenFinding(
            blocker=GateBlocker.POLICY_DENIED,
            subject=str(bundle.pinned_configuration_release_id),
            detail="the pinned Autonomy Policy allows no live implementation",
        )
    if proposal.recommended_decision != "approved_live":
        return ScreenFinding(
            blocker=GateBlocker.POLICY_DENIED,
            subject="proposal",
            detail="the proposal did not recommend live implementation",
        )
    if not proposal.live_window_fits or (
        proposal.estimated_elapsed_seconds > policy.live_window_seconds
    ):
        return ScreenFinding(
            blocker=GateBlocker.EXCESSIVE_LIVE_SCOPE,
            subject="live",
            detail=(
                f"the proposal estimates {proposal.estimated_elapsed_seconds}s against a "
                f"{policy.live_window_seconds}s live window"
            ),
        )
    return None


def _base_revision(bundle: GateBundle) -> str:
    """The revision every finding in a coherent bundle shares."""
    return bundle.code_findings[0].base_revision if bundle.code_findings else ""


def _as_uuid(raw: str) -> UUID | None:
    """Parse a cited identifier, or return `None` when it is not a UUID at all."""
    try:
        return UUID(raw)
    except ValueError:
        return None
