"""``demo verify``: the acceptance boundary of ``docs/SCENARIO.md`` as assertions.

The happy path passes only when one exact candidate completes the journey with
the originating growth lead, the PR represents the validated source and build,
and the Case File cites that candidate's checks, dataset, and criterion-level
evidence. Anything less exits non-zero naming precisely what did not hold.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from utrace_contracts.domain.lifecycle import (
    CaseFileStage,
    CriterionResult,
    DeliveryState,
    OfferIssuanceState,
    OfferState,
    ProductSignalState,
)

from demo_harness import scenario
from demo_harness.checks import Finding, holds, violated
from demo_harness.control_plane import ControlPlane
from demo_harness.journey import JourneySnapshot, observe, prohibitions

__all__ = ["verify"]


class Core(BaseModel):
    """The subset of the immutable Case File core the boundary asserts."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    metadata: CoreMetadata
    variant: str
    product_trace: ProductTrace | None = None
    implementation: Implementation | None = None


class CoreMetadata(BaseModel):
    """The identities the Case File binds itself to."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    client_id: UUID
    product_signal_id: UUID
    originating_user_ref: str
    preview_version_id: UUID | None = None
    validation_attempt_id: UUID | None = None


class Citation(BaseModel):
    """One authorized evidence slice a claim cites."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    evidence_record_id: UUID
    evidence_class: str
    source_ref: str


class CriterionTrace(BaseModel):
    """One acceptance criterion with its criterion-level evidence."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    criterion_id: UUID
    ordinal: int
    statement: str
    result: CriterionResult
    user_confirmed: bool
    observed_evidence: tuple[Citation, ...]
    contradicting_evidence: tuple[Citation, ...]


class ProductTrace(BaseModel):
    """Criterion-level observed evidence and the workflow comparison."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    workflow_ref: str
    criteria: tuple[CriterionTrace, ...]


class CheckResult(BaseModel):
    """One pinned check and how it ended."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str
    outcome: str


class Implementation(BaseModel):
    """The exact identities a reviewer needs to trust the delivered change."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    repository_ref: str
    pull_request_ref: str | None = None
    implementation_attempt_id: UUID
    preview_version_id: UUID
    source_commit: str
    source_digest: str
    build_digest: str
    dataset_manifest_digest: str
    checks: tuple[CheckResult, ...]
    cleanup_state: str


Core.model_rebuild()


async def verify(control_plane: ControlPlane, *, client_id: UUID) -> list[Finding]:
    """Return one finding per clause of the scenario's acceptance boundary."""
    snapshot = await observe(control_plane, client_id=client_id)
    findings = prohibitions(snapshot)
    findings.append(_same_originating_user(snapshot))
    core = await _core(control_plane, snapshot=snapshot)
    findings.extend(await _exact_candidate(control_plane, snapshot=snapshot, core=core))
    findings.extend(_case_file_citations(snapshot=snapshot, core=core))
    findings.extend(_resource_states(snapshot))
    return findings


def _same_originating_user(snapshot: JourneySnapshot) -> Finding:
    """Check that the validating participant is the case's originating user."""
    case = snapshot.case.case
    validation = snapshot.validation
    if validation is None:
        return violated(
            "same_originating_user",
            "no Validation Attempt exists, so the validating participant cannot be compared "
            "with the originating user",
        )
    if validation.client_user_id != case.originating_client_user_id:
        return violated(
            "same_originating_user",
            f"the case originated with client user {case.originating_client_user_id} but "
            f"{validation.client_user_id} validated it; substituting a different user fails "
            f"acceptance",
        )
    return holds(
        "same_originating_user",
        f"client user {case.originating_client_user_id} both reported and validated",
    )


async def _core(control_plane: ControlPlane, *, snapshot: JourneySnapshot) -> Core | None:
    """Return the published Case File's immutable core, when one exists."""
    record = snapshot.case.case_file
    if record is None or record.stage is not CaseFileStage.PUBLISHED:
        return None
    detail = await control_plane.case_file(snapshot.client_id, record.id)
    if detail.core is None:
        return None
    return Core.model_validate(detail.core)


async def _exact_candidate(
    control_plane: ControlPlane, *, snapshot: JourneySnapshot, core: Core | None
) -> list[Finding]:
    """Check that the PR carries the exact validated source and build."""
    validation = snapshot.validation
    delivery = snapshot.delivery
    if validation is None:
        return [violated("exact_candidate_delivered", "no Validation Attempt exists")]
    if delivery is None:
        return [
            violated(
                "exact_candidate_delivered",
                "no delivery record exists, so no PR represents the validated candidate",
            )
        ]

    findings: list[Finding] = []
    candidate = await control_plane.preview_version(
        snapshot.client_id, validation.preview_version_id
    )
    if delivery.validated_source_commit != candidate.source_commit:
        findings.append(
            violated(
                "exact_candidate_delivered",
                f"the PR was opened for {delivery.validated_source_commit}, but the validated "
                f"candidate {candidate.preview_version_id} was built from "
                f"{candidate.source_commit}",
            )
        )
    elif delivery.state is not DeliveryState.DELIVERED or delivery.pull_request_ref is None:
        findings.append(
            violated(
                "exact_candidate_delivered",
                f"delivery is in {delivery.state.value} with pull request "
                f"{delivery.pull_request_ref}; the scenario delivers one draft PR",
            )
        )
    else:
        findings.append(
            holds(
                "exact_candidate_delivered",
                f"{delivery.pull_request_ref} carries {candidate.source_commit[:12]}, build "
                f"{candidate.build_digest[:23]}, from candidate {candidate.preview_version_id}",
            )
        )

    if core is None or core.implementation is None:
        findings.append(
            violated(
                "case_file_names_the_candidate",
                "no published Case File core with an implementation section exists",
            )
        )
    elif core.implementation.preview_version_id != validation.preview_version_id:
        findings.append(
            violated(
                "case_file_names_the_candidate",
                f"the Case File names candidate {core.implementation.preview_version_id}, but "
                f"{validation.preview_version_id} was validated",
            )
        )
    elif core.implementation.source_commit != candidate.source_commit:
        findings.append(
            violated(
                "case_file_names_the_candidate",
                f"the Case File records source {core.implementation.source_commit}, but the "
                f"candidate was built from {candidate.source_commit}",
            )
        )
    else:
        findings.append(
            holds(
                "case_file_names_the_candidate",
                f"candidate {core.implementation.preview_version_id}, source "
                f"{core.implementation.source_commit[:12]}, build "
                f"{core.implementation.build_digest[:23]}",
            )
        )
    return findings


def _case_file_citations(*, snapshot: JourneySnapshot, core: Core | None) -> list[Finding]:
    """Check that the Case File cites checks, dataset, and criterion evidence."""
    if core is None:
        return [
            violated(
                "case_file_cites_checks", "no published Case File core exists"
            ),
            violated("case_file_cites_dataset", "no published Case File core exists"),
            violated("case_file_cites_criteria", "no published Case File core exists"),
        ]

    findings: list[Finding] = []
    implementation = core.implementation
    if implementation is None or not implementation.checks:
        findings.append(
            violated(
                "case_file_cites_checks",
                "the Case File records no pinned check results for the candidate",
            )
        )
    else:
        failed_checks = [check.name for check in implementation.checks if check.outcome != "passed"]
        findings.append(
            holds(
                "case_file_cites_checks",
                f"{len(implementation.checks)} checks recorded: "
                f"{[check.name for check in implementation.checks]}",
            )
            if not failed_checks
            else violated(
                "case_file_cites_checks",
                f"the Case File records non-passing checks {failed_checks}",
            )
        )

    validation = snapshot.validation
    if implementation is None:
        findings.append(violated("case_file_cites_dataset", "no implementation section exists"))
    elif validation is None:
        findings.append(
            violated("case_file_cites_dataset", "no Validation Attempt exists to compare against")
        )
    elif implementation.dataset_manifest_digest != validation.dataset_manifest_digest:
        findings.append(
            violated(
                "case_file_cites_dataset",
                f"the Case File cites dataset {implementation.dataset_manifest_digest}, but "
                f"validation ran against {validation.dataset_manifest_digest}",
            )
        )
    else:
        findings.append(
            holds(
                "case_file_cites_dataset",
                f"dataset {implementation.dataset_manifest_digest[:23]} matches the validated run",
            )
        )

    findings.append(_criterion_findings(core=core, snapshot=snapshot))
    return findings


def _criterion_findings(*, core: Core, snapshot: JourneySnapshot) -> Finding:
    """Check the criterion-level evidence for the saved-view validation task."""
    trace = core.product_trace
    if trace is None or not trace.criteria:
        return violated(
            "case_file_cites_criteria",
            "the Case File records no criterion-level trace, so the saved view's acceptance "
            "points are not evidenced",
        )
    unmet = [
        criterion.ordinal
        for criterion in trace.criteria
        if criterion.result is not CriterionResult.MET
    ]
    if unmet:
        return violated(
            "case_file_cites_criteria",
            f"criteria {unmet} are not met in the Case File's trace",
        )
    uncited = [
        criterion.ordinal
        for criterion in trace.criteria
        if not criterion.observed_evidence and not criterion.user_confirmed
    ]
    if uncited:
        return violated(
            "case_file_cites_criteria",
            f"criteria {uncited} cite neither observed evidence nor a user confirmation",
        )
    contradicted = [
        criterion.ordinal for criterion in trace.criteria if criterion.contradicting_evidence
    ]
    if contradicted:
        return violated(
            "case_file_cites_criteria",
            f"criteria {contradicted} carry contradicting evidence; the canonical outcome "
            f"requires evidence without contradiction",
        )
    validation = snapshot.validation
    counted = "" if validation is None else f", matching {len(validation.criterion_proofs)} proofs"
    return holds(
        "case_file_cites_criteria",
        f"{len(trace.criteria)} criteria met with criterion-level evidence for "
        f"{trace.workflow_ref!r}{counted}",
    )


def _resource_states(snapshot: JourneySnapshot) -> list[Finding]:
    """Check that scenario-owned resources reached their required state."""
    case = snapshot.case.case
    findings = [
        holds("case_closed_validated", f"the case is in {case.state.value}")
        if case.state is ProductSignalState.CLOSED_VALIDATED
        else violated(
            "case_closed_validated",
            f"the case is in {case.state.value}; the journey completes after the Case File "
            f"is published and cleanup has run",
        )
    ]
    findings.append(_resources_cleaned(snapshot))

    record = snapshot.case.case_file
    findings.append(
        holds("case_file_published", f"Case File {record.id} is {record.stage.value}")
        if record is not None and record.stage is scenario.TERMINAL_CASE_FILE_STAGE
        else violated(
            "case_file_published",
            "no Case File reached published"
            if record is None
            else f"Case File {record.id} is only {record.stage.value}",
        )
    )

    offer = snapshot.offer()
    if offer is None:
        findings.append(violated("credit_earned_and_issued", "no offer was written for this case"))
    elif offer.state is not OfferState.EARNED:
        findings.append(
            violated(
                "credit_earned_and_issued",
                f"offer {offer.outreach_offer_id} is {offer.state.value}, not earned",
            )
        )
    elif offer.issuance_state is not OfferIssuanceState.ISSUED:
        findings.append(
            violated(
                "credit_earned_and_issued",
                f"offer {offer.outreach_offer_id} is earned but issuance is "
                f"{None if offer.issuance_state is None else offer.issuance_state.value}",
            )
        )
    elif offer.amount_minor_units != scenario.REWARD_MINOR_UNITS:
        findings.append(
            violated(
                "credit_earned_and_issued",
                f"the offer is for {offer.amount_minor_units} minor units, not the fixed "
                f"{scenario.REWARD_MINOR_UNITS}",
            )
        )
    else:
        findings.append(
            holds(
                "credit_earned_and_issued",
                f"{offer.amount_minor_units} {offer.currency} minor units issued as "
                f"{offer.provider_transaction_ref}",
            )
        )
    return findings


def _resources_cleaned(snapshot: JourneySnapshot) -> Finding:
    """Check that the case gave back everything the scenario says it must.

    The cleanup summary is the case's own read model over durable rows: the
    Preview Runtime Allocations it still holds, the Environment Leases still
    open, the invitations still claimable, and the cleanup the implementation
    workspace recorded before it was destroyed. Anything left behind is named
    rather than summarized.
    """
    cleanup = snapshot.case.cleanup
    still_held: list[str] = []
    if cleanup.preview_runtime_allocations_retained:
        still_held.append(
            f"{cleanup.preview_runtime_allocations_retained} "
            f"Preview Runtime Allocation(s)"
        )
    if cleanup.environment_leases_open:
        still_held.append(f"{cleanup.environment_leases_open} open Environment Lease(s)")
    if cleanup.invitations_outstanding:
        still_held.append(f"{cleanup.invitations_outstanding} claimable invitation(s)")
    if cleanup.workspace_cleanup_state is None:
        still_held.append("no recorded implementation workspace cleanup")
    if still_held:
        return violated(
            "resources_cleaned",
            f"the case still holds {', '.join(still_held)}; the scenario completes only "
            f"once the dataset, the workspace, the invitations, and the unneeded preview "
            f"runtimes have followed their cleanup policy",
        )
    given_back = [
        allocation
        for allocation in snapshot.runtimes
        if allocation.released_at is not None or allocation.terminated_at is not None
    ]
    return holds(
        "resources_cleaned",
        f"{len(given_back)} of {len(snapshot.runtimes)} Preview Runtime Allocation(s) "
        f"given back "
        f"({cleanup.preview_runtime_allocations_terminated} terminated, "
        f"{cleanup.preview_runtime_allocations_released} released), no open lease or claimable "
        f"invitation, workspace {cleanup.workspace_cleanup_state}",
    )
