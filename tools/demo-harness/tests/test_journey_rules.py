"""The prohibitions and milestones the scenario's happy path implies."""

from __future__ import annotations

from uuid import uuid4

import pytest
from utrace_contracts.domain.lifecycle import (
    CaseFileStage,
    CaseFileVariant,
    DeliveryState,
    FlowType,
    GateDecisionKind,
    InterviewSessionState,
    OfferIssuanceState,
    OfferState,
    OutreachAttemptState,
    PreviewRuntimeAllocationState,
    ProductSignalState,
    ValidationOutcome,
)

from demo_harness.checks import failed
from demo_harness.journey import MILESTONES, JourneySnapshot, prohibitions, reached
from demo_harness.models import InvitationDisposition
from tests import factories


def _happy() -> JourneySnapshot:
    return factories.snapshot(
        case=factories.signal(
            state=ProductSignalState.CLOSED_VALIDATED,
            flow=FlowType.FEATURE_REQUEST_FLOW,
            criteria_revision=1,
        ),
        gates=[factories.gate(decision=GateDecisionKind.APPROVED_LIVE)],
        attempts=[factories.attempt()],
        sessions=[factories.session(state=InterviewSessionState.CLOSED)],
        outreach_attempts=[
            factories.outreach(
                state=OutreachAttemptState.CLAIMED,
                invitations=[factories.invitation(disposition=InvitationDisposition.CLAIMED)],
            )
        ],
        runtimes=[factories.runtime(state=PreviewRuntimeAllocationState.TERMINATED)],
        cleanup_summary=factories.cleanup(complete=True),
        record=factories.case_file(
            stage=CaseFileStage.PUBLISHED, variant=CaseFileVariant.VALIDATED_IMPLEMENTATION
        ),
        validation_record=factories.validation(
            outcome=ValidationOutcome.VALIDATED, client_user_id=factories.USER_ID
        ),
        delivery_record=factories.delivery(
            state=DeliveryState.DELIVERED, commit=factories.SOURCE_COMMIT
        ),
        dispositions=[
            factories.offer(
                state=OfferState.EARNED, issuance=OfferIssuanceState.ISSUED, amount=25_000
            )
        ],
        extra_cases=[],
    )


def _mid_interview() -> JourneySnapshot:
    """The same journey while the encounter is still open."""
    return factories.snapshot(
        case=factories.signal(
            state=ProductSignalState.INTERVIEWING,
            flow=FlowType.FEATURE_REQUEST_FLOW,
            criteria_revision=0,
        ),
        gates=[],
        attempts=[],
        sessions=[factories.session(state=InterviewSessionState.LIVE)],
        outreach_attempts=[
            factories.outreach(
                state=OutreachAttemptState.CLAIMED,
                invitations=[factories.invitation(disposition=InvitationDisposition.CLAIMED)],
            )
        ],
        runtimes=[factories.runtime(state=PreviewRuntimeAllocationState.BOUND)],
        cleanup_summary=factories.cleanup(complete=False),
        record=None,
        validation_record=None,
        delivery_record=None,
        dispositions=[
            factories.offer(
                state=OfferState.RESERVED, issuance=None, amount=25_000
            )
        ],
        extra_cases=[],
    )


def test_the_happy_path_breaks_no_prohibition() -> None:
    assert failed(prohibitions(_happy())) == []


def test_every_milestone_is_reached_across_the_happy_path() -> None:
    """The live milestones commit during the encounter, the rest after it.

    A single snapshot cannot carry both: an Interview Session that is live and a
    preview runtime that is still allocated are exactly what cleanup gives back.
    Watching accumulates them, so the two snapshots together must cover every
    milestone the scenario declares.
    """
    committed = reached(_mid_interview()) | reached(_happy())
    assert set(committed) == {milestone.name for milestone in MILESTONES}


def test_the_live_milestones_commit_during_the_encounter() -> None:
    committed = reached(_mid_interview())
    assert "interview_live" in committed
    assert "preview_ready" in committed
    assert "cleanup_complete" not in committed


def test_a_second_live_product_signal_is_refused() -> None:
    extra = factories.signal(
        state=ProductSignalState.AWAITING_INTERVIEW,
        flow=FlowType.FEATURE_REQUEST_FLOW,
        criteria_revision=0,
    ).model_copy(update={"id": uuid4()})
    snapshot = factories.snapshot(
        case=factories.signal(
            state=ProductSignalState.CLOSED_VALIDATED,
            flow=FlowType.FEATURE_REQUEST_FLOW,
            criteria_revision=1,
        ),
        gates=[factories.gate(decision=GateDecisionKind.APPROVED_LIVE)],
        attempts=[factories.attempt()],
        sessions=[],
        outreach_attempts=[],
        runtimes=[],
        cleanup_summary=factories.cleanup(complete=False),
        record=None,
        validation_record=None,
        delivery_record=None,
        dispositions=[],
        extra_cases=[extra],
    )
    broken = {finding.name for finding in failed(prohibitions(snapshot))}
    assert "one_product_signal" in broken


@pytest.mark.parametrize(
    "state",
    [
        ProductSignalState.CANCELED,
        ProductSignalState.CLOSED_NO_IMPLEMENTATION,
        ProductSignalState.CLOSED_BLOCKED,
    ],
)
def test_a_forbidden_terminal_state_is_refused(state: ProductSignalState) -> None:
    snapshot = factories.snapshot(
        case=factories.signal(
            state=state, flow=FlowType.FEATURE_REQUEST_FLOW, criteria_revision=1
        ),
        gates=[],
        attempts=[],
        sessions=[],
        outreach_attempts=[],
        runtimes=[],
        cleanup_summary=factories.cleanup(complete=False),
        record=None,
        validation_record=None,
        delivery_record=None,
        dispositions=[],
        extra_cases=[],
    )
    broken = {finding.name for finding in failed(prohibitions(snapshot))}
    assert "signal_state_permitted" in broken


@pytest.mark.parametrize(
    "decision", [GateDecisionKind.NO_IMPLEMENTATION]
)
def test_a_gate_result_other_than_approved_live_is_refused(
    decision: GateDecisionKind,
) -> None:
    snapshot = factories.snapshot(
        case=factories.signal(
            state=ProductSignalState.IMPLEMENTING,
            flow=FlowType.FEATURE_REQUEST_FLOW,
            criteria_revision=1,
        ),
        gates=[factories.gate(decision=decision)],
        attempts=[],
        sessions=[],
        outreach_attempts=[],
        runtimes=[],
        cleanup_summary=factories.cleanup(complete=False),
        record=None,
        validation_record=None,
        delivery_record=None,
        dispositions=[],
        extra_cases=[],
    )
    broken = {finding.name for finding in failed(prohibitions(snapshot))}
    assert "gate_decision_permitted" in broken


def test_a_repair_attempt_is_refused() -> None:
    snapshot = factories.snapshot(
        case=factories.signal(
            state=ProductSignalState.VALIDATING,
            flow=FlowType.FEATURE_REQUEST_FLOW,
            criteria_revision=1,
        ),
        gates=[factories.gate(decision=GateDecisionKind.APPROVED_LIVE)],
        attempts=[factories.attempt(), factories.attempt()],
        sessions=[],
        outreach_attempts=[],
        runtimes=[],
        cleanup_summary=factories.cleanup(complete=False),
        record=None,
        validation_record=None,
        delivery_record=None,
        dispositions=[],
        extra_cases=[],
    )
    broken = {finding.name for finding in failed(prohibitions(snapshot))}
    assert "initial_candidate_only" in broken


@pytest.mark.parametrize(
    "outcome",
    [
        ValidationOutcome.NOT_VALIDATED,
        ValidationOutcome.PARTIALLY_VALIDATED,
        ValidationOutcome.UNCLEAR,
    ],
)
def test_an_outcome_other_than_validated_is_refused(outcome: ValidationOutcome) -> None:
    snapshot = factories.snapshot(
        case=factories.signal(
            state=ProductSignalState.VALIDATING,
            flow=FlowType.FEATURE_REQUEST_FLOW,
            criteria_revision=1,
        ),
        gates=[factories.gate(decision=GateDecisionKind.APPROVED_LIVE)],
        attempts=[factories.attempt()],
        sessions=[],
        outreach_attempts=[],
        runtimes=[],
        cleanup_summary=factories.cleanup(complete=False),
        record=None,
        validation_record=factories.validation(
            outcome=outcome, client_user_id=factories.USER_ID
        ),
        delivery_record=None,
        dispositions=[],
        extra_cases=[],
    )
    broken = {finding.name for finding in failed(prohibitions(snapshot))}
    assert "validation_outcome" in broken


def test_a_released_offer_is_refused() -> None:
    snapshot = factories.snapshot(
        case=factories.signal(
            state=ProductSignalState.CLOSED_VALIDATED,
            flow=FlowType.FEATURE_REQUEST_FLOW,
            criteria_revision=1,
        ),
        gates=[factories.gate(decision=GateDecisionKind.APPROVED_LIVE)],
        attempts=[factories.attempt()],
        sessions=[],
        outreach_attempts=[],
        runtimes=[],
        cleanup_summary=factories.cleanup(complete=False),
        record=None,
        validation_record=None,
        delivery_record=None,
        dispositions=[
            factories.offer(state=OfferState.RELEASED, issuance=None, amount=25_000)
        ],
        extra_cases=[],
    )
    broken = {finding.name for finding in failed(prohibitions(snapshot))}
    assert "offer_not_released" in broken


def test_a_bugfix_classification_is_refused() -> None:
    snapshot = factories.snapshot(
        case=factories.signal(
            state=ProductSignalState.INTERVIEWING,
            flow=FlowType.BUGFIX_FLOW,
            criteria_revision=0,
        ),
        gates=[],
        attempts=[],
        sessions=[],
        outreach_attempts=[],
        runtimes=[],
        cleanup_summary=factories.cleanup(complete=False),
        record=None,
        validation_record=None,
        delivery_record=None,
        dispositions=[],
        extra_cases=[],
    )
    broken = {finding.name for finding in failed(prohibitions(snapshot))}
    assert "feature_request_flow" in broken
