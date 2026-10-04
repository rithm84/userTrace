"""The deterministic security screen and the citation checks.

These run everywhere: they are pure functions over a bundle and a proposal, and
they are what stops a model's recommendation from becoming an approval on its
own say-so.
"""

from __future__ import annotations

from datetime import UTC, datetime

from utrace_contracts.domain.lifecycle import FlowType, ProductSignalState

from app.gate.bundle import (
    CodeFinding,
    EnvironmentReadiness,
    EvidenceCitation,
    GateBundle,
)
from app.gate.policy import ImplementationPolicy
from app.gate.proposal import (
    DependencyAddition,
    GateBlocker,
    GateProposal,
    ProposedChange,
)
from app.gate.scope import (
    AcceptanceCriterion,
    DependencyPolicy,
    PermittedDependency,
    RequiredCheck,
    ResourceBudgets,
)
from app.gate.screen import ScreenFinding, check_live_fit, screen_proposal, validate_citations
from app.ids import uuid7
from app.implementation.ports import BuildRecipe

BASE_REVISION = "46ce0544d2e46b48fe5dcd9807185b87de43d6cb"
REPOSITORY = "ShopalyticsStore/shopalytics"

_FINDING_ID = uuid7()
_EVIDENCE_ID = uuid7()


def _policy(
    *,
    live_implementation_allowed: bool = True,
    dependency_policy: DependencyPolicy = DependencyPolicy.NONE,
    permitted_dependencies: tuple[PermittedDependency, ...] = (),
    live_window_seconds: int = 1800,
) -> ImplementationPolicy:
    return ImplementationPolicy(
        live_implementation_allowed=live_implementation_allowed,
        repository_ref=REPOSITORY,
        clone_url=f"https://github.com/{REPOSITORY}.git",
        base_revision="screen-test-base",
        workspace_snapshot_id="utrace-implementation-node24-2026-09-20",
        callback_hosts=("callbacks.utrace.dev",),
        build_recipe=BuildRecipe(
            node_version="24",
            package_manager="npm",
            install_command="npm ci",
            build_command="npm run build",
            start_command="npm run start:preview",
            application_port=3300,
            environment_profile="utrace-preview",
        ),
        permitted_paths=("src/lib/dashboard", "src/components/dashboard", "tests/unit"),
        blocked_paths=("src/lib/utrace", "src/app/api", "db"),
        required_checks=(
            RequiredCheck(
                name="lint",
                command="npm run lint",
                working_directory=".",
                timeout_seconds=900,
                after=(),
            ),
        ),
        dependency_policy=dependency_policy,
        permitted_dependencies=permitted_dependencies,
        dependency_manifest_paths=("package.json", "package-lock.json"),
        budgets=ResourceBudgets(
            elapsed_seconds=3600,
            agent_turns=400,
            agent_cost_cents=5_000,
            process_executions=200,
            artifact_bytes=20_000_000,
            correction_cycles=3,
            changed_files=12,
        ),
        live_window_seconds=live_window_seconds,
        implementation_limit=1,
        forbidden_path_patterns=("**/auth/**", "**/*secret*", "**/.github/workflows/**"),
    )


def _bundle(
    *,
    policy: ImplementationPolicy | None = None,
    flow: FlowType = FlowType.FEATURE_REQUEST_FLOW,
    criteria: bool = True,
    evidence: bool = True,
    findings: bool = True,
    allocation_state: str | None = "ready",
    implementation_count: int = 0,
) -> GateBundle:
    now = datetime.now(tz=UTC)
    return GateBundle(
        client_id=uuid7(),
        product_signal_id=uuid7(),
        product_signal_state=ProductSignalState.GATE_PENDING,
        product_signal_revision=3,
        initial_flow_type=FlowType.FEATURE_REQUEST_FLOW,
        established_flow_type=flow,
        classification_revision=1,
        acceptance_criteria_revision=0,
        concern_revision=2,
        pinned_configuration_release_id=uuid7(),
        policy=policy if policy is not None else _policy(),
        customer_account_id=uuid7(),
        originating_client_user_id=uuid7(),
        acceptance_criteria=(
            (
                AcceptanceCriterion(
                    criterion_id=str(uuid7()),
                    ordinal=0,
                    statement="the complete filter stack can be saved under a name",
                    observability="observed",
                ),
            )
            if criteria
            else ()
        ),
        confirmed_evidence=(
            (
                EvidenceCitation(
                    evidence_id=_EVIDENCE_ID,
                    evidence_class="transcript_fragment",
                    claim_state="confirmed",
                    source_ref="interview:1",
                    context_revision=2,
                    recorded_at=now,
                    summary="rebuilding the same filters every Monday",
                ),
            )
            if evidence
            else ()
        ),
        telemetry_citations=(),
        code_findings=(
            (
                CodeFinding(
                    finding_id=_FINDING_ID,
                    repository_ref=REPOSITORY,
                    base_revision=BASE_REVISION,
                    path="src/components/dashboard/ConversionDashboard.tsx",
                    first_line=60,
                    last_line=80,
                    excerpt_digest=f"sha256:{'0' * 64}",
                    note="filter stack lives in React state",
                ),
            )
            if findings
            else ()
        ),
        readiness=EnvironmentReadiness(
            base_allocation_id=uuid7() if allocation_state is not None else None,
            base_allocation_state=allocation_state,
            seed_manifest_digest="sha256:seed",
        ),
        implementation_count=implementation_count,
        assembled_at=now,
    )


def _proposal(
    *,
    decision: str = "approved_live",
    path: str = "src/lib/dashboard/saved-views.ts",
    cited_finding_ids: list[str] | None = None,
    cited_evidence_ids: list[str] | None = None,
    dependency_additions: list[DependencyAddition] | None = None,
    live_window_fits: bool = True,
    estimated_elapsed_seconds: int = 600,
) -> GateProposal:
    findings = [str(_FINDING_ID)] if cited_finding_ids is None else cited_finding_ids
    return GateProposal(
        recommended_decision=decision,  # type: ignore[arg-type]
        intended_outcome="save and reopen a named conversion view",
        in_scope_changes=["persist saved conversion views for the account"],
        excluded_changes=["sharing", "rename", "deletion", "dashboard redesign"],
        proposed_changes=[
            ProposedChange(
                path=path,
                change_kind="create",
                rationale="the filter stack is not persisted anywhere today",
                cited_finding_ids=findings,
            )
        ],
        dependency_additions=dependency_additions or [],
        cited_evidence_ids=(
            [str(_EVIDENCE_ID)] if cited_evidence_ids is None else cited_evidence_ids
        ),
        cited_finding_ids=findings,
        live_window_fits=live_window_fits,
        estimated_elapsed_seconds=estimated_elapsed_seconds,
        blocker=None,
        blocker_detail=None,
    )


def _blockers(findings: tuple[ScreenFinding, ...]) -> set[GateBlocker]:
    return {item.blocker for item in findings}


def test_a_complete_proposal_passes_the_screen() -> None:
    """A cited, in-scope, in-window proposal produces no finding at all."""
    bundle = _bundle()
    proposal = _proposal()
    assert validate_citations(bundle, proposal) == ()
    assert screen_proposal(bundle, proposal, base_revision=BASE_REVISION) == ()


def test_an_uncited_evidence_identifier_invalidates_the_proposal() -> None:
    """A proposal may cite only what the bundle carries."""
    findings = validate_citations(
        _bundle(), _proposal(cited_evidence_ids=[str(uuid7())])
    )
    assert _blockers(findings) == {GateBlocker.UNVERIFIABLE_CITATION}


def test_a_change_without_a_code_finding_is_refused() -> None:
    """Every proposed change has to rest on an exact-revision finding."""
    findings = validate_citations(_bundle(), _proposal(cited_finding_ids=[]))
    assert GateBlocker.MISSING_CODE_FINDINGS in _blockers(findings)


def test_a_blocked_code_surface_is_refused() -> None:
    """A blocked path stays readable but can never be proposed as a change."""
    findings = screen_proposal(
        _bundle(),
        _proposal(path="src/lib/utrace/chart-state.ts"),
        base_revision=BASE_REVISION,
    )
    assert GateBlocker.BLOCKED_CODE_SURFACE in _blockers(findings)


def test_a_path_outside_every_permitted_surface_is_refused() -> None:
    """Scope cannot grow to a surface the pinned policy never permitted."""
    findings = screen_proposal(
        _bundle(), _proposal(path="scripts/db/setup.ts"), base_revision=BASE_REVISION
    )
    assert GateBlocker.BLOCKED_CODE_SURFACE in _blockers(findings)


def test_a_forbidden_security_target_is_refused() -> None:
    """Auth, secrets, and pipeline surfaces are forbidden by the screen itself."""
    findings = screen_proposal(
        _bundle(),
        _proposal(path="src/components/dashboard/auth/session.ts"),
        base_revision=BASE_REVISION,
    )
    assert GateBlocker.SECURITY_RISK in _blockers(findings)


def test_a_path_escaping_the_repository_is_refused() -> None:
    """A traversal is a security finding, not a path to normalize."""
    findings = screen_proposal(
        _bundle(),
        _proposal(path="../../etc/passwd"),
        base_revision=BASE_REVISION,
    )
    assert GateBlocker.SECURITY_RISK in _blockers(findings)


def test_a_dependency_addition_under_a_none_policy_is_refused() -> None:
    """`none` permits no addition at all."""
    findings = screen_proposal(
        _bundle(),
        _proposal(
            dependency_additions=[
                DependencyAddition(
                    manifest_path="package.json", package_name="zustand", exact_version="5.0.0"
                )
            ]
        ),
        base_revision=BASE_REVISION,
    )
    assert GateBlocker.DEPENDENCY_POLICY_VIOLATION in _blockers(findings)


def test_an_allowlisted_dependency_at_its_exact_version_passes() -> None:
    """`allowlist` permits exactly the pinned name at the pinned version."""
    permitted = PermittedDependency(
        manifest_path="package.json",
        package_name="zustand",
        exact_version="5.0.0",
        license_class="permissive",
    )
    bundle = _bundle(
        policy=_policy(
            dependency_policy=DependencyPolicy.ALLOWLIST, permitted_dependencies=(permitted,)
        )
    )
    accepted = _proposal(
        dependency_additions=[
            DependencyAddition(
                manifest_path="package.json", package_name="zustand", exact_version="5.0.0"
            )
        ]
    )
    refused = _proposal(
        dependency_additions=[
            DependencyAddition(
                manifest_path="package.json", package_name="zustand", exact_version="4.0.0"
            )
        ]
    )
    assert screen_proposal(bundle, accepted, base_revision=BASE_REVISION) == ()
    assert GateBlocker.DEPENDENCY_POLICY_VIOLATION in _blockers(
        screen_proposal(bundle, refused, base_revision=BASE_REVISION)
    )


def test_live_work_that_fits_the_window_is_accepted() -> None:
    """A bounded change inside the pinned live window runs live."""
    assert check_live_fit(_bundle(), _proposal(estimated_elapsed_seconds=600)) is None


def test_live_work_that_exceeds_the_window_is_refused() -> None:
    """Work that does not fit the live session gets no implementation, never deferral."""
    refusal = check_live_fit(
        _bundle(policy=_policy(live_window_seconds=300)),
        _proposal(estimated_elapsed_seconds=1200),
    )
    assert refusal is not None
    assert refusal.blocker is GateBlocker.EXCESSIVE_LIVE_SCOPE


def test_a_policy_denying_live_implementation_is_refused() -> None:
    """Deny wins: a policy without live implementation authorizes nothing."""
    refusal = check_live_fit(
        _bundle(policy=_policy(live_implementation_allowed=False)), _proposal()
    )
    assert refusal is not None
    assert refusal.blocker is GateBlocker.POLICY_DENIED


def test_an_established_bugfix_classification_is_an_unsupported_flow() -> None:
    """The demo implements Feature Request Flow only."""
    findings = screen_proposal(
        _bundle(flow=FlowType.BUGFIX_FLOW), _proposal(), base_revision=BASE_REVISION
    )
    assert GateBlocker.UNSUPPORTED_FLOW in _blockers(findings)


def test_missing_evidence_criteria_findings_and_readiness_each_block() -> None:
    """Each missing precondition produces its own typed blocker."""
    findings = screen_proposal(
        _bundle(criteria=False, evidence=False, findings=False, allocation_state="pending"),
        _proposal(),
        base_revision=BASE_REVISION,
    )
    assert {
        GateBlocker.INSUFFICIENT_EVIDENCE,
        GateBlocker.MISSING_CODE_FINDINGS,
        GateBlocker.ENVIRONMENT_NOT_READY,
    } <= _blockers(findings)


def test_a_second_attempt_is_refused() -> None:
    """The demo grants one Implementation Attempt per Product Signal."""
    findings = screen_proposal(
        _bundle(implementation_count=1),
        _proposal(),
        base_revision=BASE_REVISION,
    )
    assert GateBlocker.BUDGET_EXHAUSTED in _blockers(findings)


def test_a_finding_at_another_revision_is_not_citable() -> None:
    """The gate approves against one pinned revision and nothing else."""
    findings = screen_proposal(
        _bundle(), _proposal(), base_revision="0" * 40
    )
    assert GateBlocker.UNVERIFIABLE_CITATION in _blockers(findings)
