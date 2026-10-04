"""One snapshot of the journey, and the milestones and prohibitions it implies.

``watch`` and ``verify`` read the same snapshot: watch prints milestones as they
commit, verify asserts the acceptance boundary over the finished journey. Both
fail loudly on a state ``docs/SCENARIO.md`` forbids.

Every milestone is read from the staff read model that owns the fact: the
outreach attempt that invited the originating user, the invitation they claimed,
the Preview Runtime Allocation that became ready, the Interview Session that went
live, and the case's own cleanup summary. Nothing here infers a milestone from a
neighbouring state.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from utrace_contracts.domain.lifecycle import (
    CaseFileStage,
    CaseFileVariant,
    DeliveryState,
    GateDecisionKind,
    ImplementationAttemptState,
    OfferIssuanceState,
    OfferState,
    OutreachAttemptState,
    PreviewAllocationKind,
    PreviewRuntimeAllocationState,
    ProductSignalState,
    ValidationOutcome,
)
from utrace_contracts.domain.records import ProductSignalRecord

from demo_harness import scenario
from demo_harness.checks import Finding, holds, violated
from demo_harness.control_plane import ControlPlane, ControlPlaneError
from demo_harness.models import (
    CaseDetail,
    DeliveryView,
    InvitationDisposition,
    InvitationView,
    OfferDisposition,
    OffersView,
    OutreachAttemptView,
    PreviewRuntimeAllocationView,
    ValidationRecord,
)

__all__ = [
    "MILESTONES",
    "JourneySnapshot",
    "Milestone",
    "NoScenarioCaseError",
    "observe",
    "prohibitions",
    "reached",
]

_FORBIDDEN_SIGNAL_STATES: Final[frozenset[ProductSignalState]] = frozenset(
    {
        ProductSignalState.CANCELED,
        ProductSignalState.CLOSED_NO_IMPLEMENTATION,
        ProductSignalState.CLOSED_BLOCKED,
    }
)

_SENT_ATTEMPT_STATES: Final[frozenset[OutreachAttemptState]] = frozenset(
    {
        OutreachAttemptState.AWAITING_RESPONSE,
        OutreachAttemptState.CLAIMED,
    }
)
"""An attempt whose invitation has left uTrace and is waiting on the user."""

_READY_ALLOCATION_STATES: Final[frozenset[PreviewRuntimeAllocationState]] = frozenset(
    {
        PreviewRuntimeAllocationState.READY,
        PreviewRuntimeAllocationState.BOUND,
    }
)
"""A seeded runtime the session can be handed. Both states are past seeding."""


class Milestone(BaseModel):
    """One scenario milestone and where the control plane reports it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str


MILESTONES: Final[tuple[Milestone, ...]] = (
    Milestone(
        name="product_signal_created",
        description="one Feature Request Product Signal exists for the routed Intake Item",
    ),
    Milestone(
        name="invitation_sent",
        description="the private invitation went out with the fixed reward reserved",
    ),
    Milestone(
        name="invitation_claimed",
        description="the invited Discord identity claimed the invitation",
    ),
    Milestone(
        name="preview_ready",
        description="the seeded Shopalytics base preview is ready and bound",
    ),
    Milestone(
        name="interview_live",
        description="the Interview Session with the originating user is live",
    ),
    Milestone(
        name="acceptance_criteria_agreed",
        description="the interview established the acceptance criteria revision",
    ),
    Milestone(
        name="gate_approved_live",
        description="the Implementation Gate returned approved_live",
    ),
    Milestone(
        name="candidate_ready",
        description="the initial Implementation Attempt reached candidate_ready",
    ),
    Milestone(
        name="validation_validated",
        description="the same growth lead validated the exact candidate",
    ),
    Milestone(
        name="pr_opened",
        description="one draft PR was delivered for the validated source",
    ),
    Milestone(
        name="case_file_published",
        description="the Shopalytics Case File was sealed and published",
    ),
    Milestone(
        name="credit_issued",
        description="the earned 250.00 USD credit carries a verified Stripe transaction",
    ),
    Milestone(
        name="cleanup_complete",
        description="every runtime, lease, and invitation the case held was given back",
    ),
)


class NoScenarioCaseError(RuntimeError):
    """The target holds no case for the Shopalytics journey yet."""

    def __init__(self, *, client_id: UUID) -> None:
        """Say what is missing and what creates it."""
        super().__init__(
            f"client {client_id} has no Product Signal yet; the journey starts when the "
            f"canonical Discord message routes (see `demo rehearse-intake`)"
        )


class JourneySnapshot(BaseModel):
    """Everything the staff read models say about the journey right now."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    client_id: UUID
    cases: list[ProductSignalRecord]
    case: CaseDetail
    outreach: list[OutreachAttemptView]
    runtimes: list[PreviewRuntimeAllocationView]
    validation: ValidationRecord | None = None
    delivery: DeliveryView | None = None
    offers: OffersView

    def offer(self) -> OfferDisposition | None:
        """Return the offer written against this case, when one exists."""
        for disposition in self.offers.offers:
            if disposition.product_signal_id == self.case.case.id:
                return disposition
        return None

    def invitations(self) -> list[InvitationView]:
        """Return every invitation the case sent, across its attempts."""
        return [
            invitation
            for attempt in self.outreach
            for invitation in attempt.invitations
        ]

    def ready_base_runtime(self) -> PreviewRuntimeAllocationView | None:
        """Return a base interview runtime that is seeded and handed over.

        A case can hold more than one base allocation over its life: preview
        capacity exhaustion replaces the first with a later one. Any of them
        being ready is what the milestone records.
        """
        for allocation in self.runtimes:
            if (
                allocation.allocation_kind is PreviewAllocationKind.BASE
                and allocation.state in _READY_ALLOCATION_STATES
            ):
                return allocation
        return None


async def observe(control_plane: ControlPlane, *, client_id: UUID) -> JourneySnapshot:
    """Assemble one snapshot of the journey from the staff read models."""
    listing = await control_plane.cases(client_id)
    if not listing.cases:
        raise NoScenarioCaseError(client_id=client_id)
    newest = listing.cases[0]
    detail = await control_plane.case(client_id, newest.id)
    offers = await control_plane.offers(client_id, scenario.REWARD_CURRENCY.upper())
    outreach = await control_plane.case_outreach(client_id, newest.id)
    runtimes = await control_plane.preview_runtime_allocations(client_id, newest.id)
    return JourneySnapshot(
        client_id=client_id,
        cases=listing.cases,
        case=detail,
        outreach=list(outreach.attempts),
        runtimes=list(runtimes.allocations),
        validation=await _validation(control_plane, client_id=client_id, case=detail),
        delivery=await _delivery(control_plane, client_id=client_id, case=detail),
        offers=offers,
    )


async def _validation(
    control_plane: ControlPlane, *, client_id: UUID, case: CaseDetail
) -> ValidationRecord | None:
    """Return the case's validation record, or nothing when none exists yet."""
    try:
        return await control_plane.validation(client_id, case.case.id)
    except ControlPlaneError as error:
        if error.status == 404:
            return None
        raise


async def _delivery(
    control_plane: ControlPlane, *, client_id: UUID, case: CaseDetail
) -> DeliveryView | None:
    """Return the case's delivery record, or nothing when none exists yet."""
    if case.case_file is None:
        return None
    try:
        return await control_plane.delivery(client_id, case.case_file.id)
    except ControlPlaneError as error:
        if error.status == 404:
            return None
        raise


def reached(snapshot: JourneySnapshot) -> dict[str, str]:
    """Return every milestone that has committed right now, with its evidence."""
    case = snapshot.case.case
    committed: dict[str, str] = {}

    committed["product_signal_created"] = (
        f"{case.id} in {case.state.value}, initially {case.initial_flow_type.value}"
    )

    offer = snapshot.offer()
    for sent in snapshot.outreach:
        if sent.state in _SENT_ATTEMPT_STATES and sent.invitations:
            reward = (
                "no reward reserved"
                if offer is None
                else (
                    f"{offer.amount_minor_units} {offer.currency} minor units "
                    f"{offer.state.value}"
                )
            )
            committed["invitation_sent"] = (
                f"{sent.purpose.value} attempt {sent.id} is {sent.state.value} with "
                f"{len(sent.invitations)} invitation(s), {reward}"
            )
            break

    for invitation in snapshot.invitations():
        if invitation.disposition is InvitationDisposition.CLAIMED:
            committed["invitation_claimed"] = (
                f"invitation {invitation.id} was claimed, opening Interview Session "
                f"{invitation.interview_session_id}"
            )
            break

    base = snapshot.ready_base_runtime()
    if base is not None:
        probe = (
            "no probe recorded"
            if base.readiness is None
            else f"{base.readiness.probe} {base.readiness.outcome}"
        )
        committed["preview_ready"] = (
            f"base allocation {base.id} is {base.state.value} on {base.origin_host} "
            f"({probe})"
        )

    live = snapshot.case.live_session()
    if live is not None:
        committed["interview_live"] = (
            f"Interview Session {live.id} is {live.state.value} in {live.phase.value}, "
            f"conversational readiness {live.readiness.conversational}"
        )

    if case.acceptance_criteria_revision > 0:
        committed["acceptance_criteria_agreed"] = (
            f"acceptance criteria revision {case.acceptance_criteria_revision}"
        )

    approved = snapshot.case.approved_live()
    if approved is not None:
        committed["gate_approved_live"] = (
            f"gate decision {approved.id} returned {approved.decision.value}"
        )

    for attempt in snapshot.case.implementation_attempts:
        if attempt.state is ImplementationAttemptState.CANDIDATE_READY:
            committed["candidate_ready"] = (
                f"attempt {attempt.id} on "
                f"{attempt.repository_ref}@{attempt.base_revision[:12]}"
            )

    validation = snapshot.validation
    if validation is not None and validation.outcome is ValidationOutcome.VALIDATED:
        committed["validation_validated"] = (
            f"validation attempt {validation.validation_attempt_id} on preview version "
            f"{validation.preview_version_id}, {len(validation.criterion_proofs)} criteria"
        )

    delivery = snapshot.delivery
    if (
        delivery is not None
        and delivery.state is DeliveryState.DELIVERED
        and delivery.pull_request_ref is not None
    ):
        committed["pr_opened"] = (
            f"{delivery.pull_request_ref} on {delivery.repository_ref} for "
            f"{delivery.validated_source_commit[:12]}"
        )

    case_file = snapshot.case.case_file
    if case_file is not None and case_file.stage is CaseFileStage.PUBLISHED:
        committed["case_file_published"] = (
            f"Case File {case_file.id} ({case_file.variant.value}), core digest "
            f"{'' if case_file.core_digest is None else case_file.core_digest[:23]}"
        )

    if (
        offer is not None
        and offer.issuance_state is OfferIssuanceState.ISSUED
        and offer.provider_transaction_ref is not None
    ):
        committed["credit_issued"] = (
            f"{offer.amount_minor_units} {offer.currency} minor units as "
            f"{offer.provider_transaction_ref}"
        )

    # The scenario's completion covers the workspace as well as the runtimes,
    # and this journey always implements, so both are required here. The read
    # model reports them separately because a case that closes with no
    # implementation has no workspace to destroy.
    cleanup = snapshot.case.cleanup
    if cleanup.complete and cleanup.workspace_cleanup_state is not None:
        committed["cleanup_complete"] = (
            f"{cleanup.preview_runtime_allocations_terminated} allocation(s) terminated, "
            f"{cleanup.preview_runtime_allocations_released} released, no open lease or "
            f"claimable invitation, workspace {cleanup.workspace_cleanup_state}"
        )

    return committed


def prohibitions(snapshot: JourneySnapshot) -> list[Finding]:
    """Return a finding for every state the scenario forbids."""
    case = snapshot.case.case
    findings: list[Finding] = []

    live = [record for record in snapshot.cases if record.state is not ProductSignalState.CANCELED]
    findings.append(
        holds("one_product_signal", f"{len(live)} live Product Signal for the client")
        if len(live) == 1
        else violated(
            "one_product_signal",
            f"{len(live)} live Product Signals exist "
            f"({[str(record.id) for record in live]}); the scenario creates exactly one",
        )
    )

    findings.append(
        violated(
            "signal_state_permitted",
            f"the case reached {case.state.value}, which the happy path forbids "
            f"(closure reason: {case.closure_reason})",
        )
        if case.state in _FORBIDDEN_SIGNAL_STATES
        else holds("signal_state_permitted", f"the case is in {case.state.value}")
    )

    findings.append(
        holds("feature_request_flow", f"initially {case.initial_flow_type.value}")
        if case.initial_flow_type is scenario.INITIAL_FLOW_TYPE
        else violated(
            "feature_request_flow",
            f"the case was routed as {case.initial_flow_type.value}, not "
            f"{scenario.INITIAL_FLOW_TYPE.value}",
        )
    )

    wrong_gate = [
        decision
        for decision in snapshot.case.gate_decisions
        if decision.decision is not GateDecisionKind.APPROVED_LIVE
    ]
    if wrong_gate:
        findings.append(
            violated(
                "gate_decision_permitted",
                f"the gate returned {[decision.decision.value for decision in wrong_gate]}; "
                f"the canonical result is {scenario.GATE_DECISION.value}",
            )
        )

    if len(snapshot.case.implementation_attempts) > 1:
        findings.append(
            violated(
                "initial_candidate_only",
                f"{len(snapshot.case.implementation_attempts)} Implementation Attempts "
                f"exist; the demo grants one per Product Signal",
            )
        )

    validation = snapshot.validation
    if validation is not None and validation.outcome is not None:
        findings.append(
            holds("validation_outcome", f"the outcome is {validation.outcome.value}")
            if validation.outcome is scenario.TERMINAL_VALIDATION_OUTCOME
            else violated(
                "validation_outcome",
                f"the outcome is {validation.outcome.value}, not "
                f"{scenario.TERMINAL_VALIDATION_OUTCOME.value}",
            )
        )

    case_file = snapshot.case.case_file
    if case_file is not None and case_file.variant is not CaseFileVariant.VALIDATED_IMPLEMENTATION:
        findings.append(
            violated(
                "case_file_variant",
                f"the Case File is the {case_file.variant.value} variant; the happy path "
                f"produces {scenario.TERMINAL_CASE_FILE_VARIANT.value}",
            )
        )

    offer = snapshot.offer()
    if offer is not None:
        if offer.state is OfferState.RELEASED:
            findings.append(
                violated(
                    "offer_not_released",
                    f"offer {offer.outreach_offer_id} released its reservation; the scenario "
                    f"earns the reward",
                )
            )
        if offer.issuance_blocked_reason is not None:
            findings.append(
                violated(
                    "issuance_not_blocked",
                    f"issuance is blocked: {offer.issuance_blocked_reason}",
                )
            )

    return findings
