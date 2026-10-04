"""The participation rules that decide whether a reward is earned.

Pure, because the rules are where the PRD's completion conditions actually live:
a terminal state label is never enough, honest negative feedback never disqualifies,
and a stop before participation earns nothing.
"""

from __future__ import annotations

import pytest
from utrace_contracts.domain.lifecycle import GateDecisionKind, ValidationOutcome

from app.offers.entitlement import (
    CaseStopReason,
    EntitlementDecision,
    ParticipationFacts,
    decide,
)


def _facts(
    *,
    stop_reason: CaseStopReason,
    participation_started: bool,
    interview_reached_gate: bool,
    gate_decision: GateDecisionKind | None,
    validation_outcome: ValidationOutcome | None,
    outstanding_participation: bool,
) -> ParticipationFacts:
    return ParticipationFacts(
        participation_started=participation_started,
        interview_reached_gate=interview_reached_gate,
        gate_decision=gate_decision,
        implementation_dispatched=validation_outcome is not None,
        validation_outcome=validation_outcome,
        outstanding_participation=outstanding_participation,
        stop_reason=stop_reason,
    )


def test_a_completed_interview_with_no_implementation_earns() -> None:
    decision, reason = decide(
        _facts(
            stop_reason=CaseStopReason.GATE_NO_IMPLEMENTATION,
            participation_started=True,
            interview_reached_gate=True,
            gate_decision=GateDecisionKind.NO_IMPLEMENTATION,
            validation_outcome=None,
            outstanding_participation=False,
        )
    )
    assert decision is EntitlementDecision.EARNED
    assert "no implementation" in reason


def test_a_negative_verdict_does_not_deny_the_reward() -> None:
    """Reward eligibility never depends on a favourable verdict."""
    decision, _ = decide(
        _facts(
            stop_reason=CaseStopReason.VALIDATION_COMPLETED,
            participation_started=True,
            interview_reached_gate=True,
            gate_decision=GateDecisionKind.APPROVED_LIVE,
            validation_outcome=ValidationOutcome.NOT_VALIDATED,
            outstanding_participation=False,
        )
    )
    assert decision is EntitlementDecision.EARNED


@pytest.mark.parametrize(
    "stop_reason",
    [
        CaseStopReason.CLIENT_STOP,
        CaseStopReason.TECHNICAL_BLOCKER,
        CaseStopReason.SCOPE_LIMIT,
    ],
)
def test_a_system_initiated_stop_after_participation_earns(
    stop_reason: CaseStopReason,
) -> None:
    """Client stops follow the same rule as technical blockers and budget limits."""
    decision, _ = decide(
        _facts(
            stop_reason=stop_reason,
            participation_started=True,
            interview_reached_gate=True,
            gate_decision=GateDecisionKind.APPROVED_LIVE,
            validation_outcome=None,
            outstanding_participation=False,
        )
    )
    assert decision is EntitlementDecision.EARNED


def test_a_system_stop_with_outstanding_participation_records_it() -> None:
    decision, reason = decide(
        _facts(
            stop_reason=CaseStopReason.CLIENT_STOP,
            participation_started=True,
            interview_reached_gate=True,
            gate_decision=GateDecisionKind.APPROVED_LIVE,
            validation_outcome=None,
            outstanding_participation=True,
        )
    )
    assert decision is EntitlementDecision.NOT_EARNED
    assert "outstanding" in reason


def test_a_stop_before_participation_earns_nothing() -> None:
    decision, reason = decide(
        _facts(
            stop_reason=CaseStopReason.CLIENT_STOP,
            participation_started=False,
            interview_reached_gate=False,
            gate_decision=None,
            validation_outcome=None,
            outstanding_participation=False,
        )
    )
    assert decision is EntitlementDecision.NOT_EARNED
    assert "before the originating user participated" in reason


def test_voluntary_withdrawal_earns_nothing() -> None:
    for stop_reason in (
        CaseStopReason.PARTICIPANT_CANCELED,
        CaseStopReason.DECLINED_CONTINUATION,
    ):
        decision, _ = decide(
            _facts(
                stop_reason=stop_reason,
                participation_started=True,
                interview_reached_gate=True,
                gate_decision=GateDecisionKind.APPROVED_LIVE,
                validation_outcome=None,
                outstanding_participation=False,
            )
        )
        assert decision is EntitlementDecision.NOT_EARNED


def test_a_temporary_blocker_leaves_eligibility_pending() -> None:
    decision, _ = decide(
        _facts(
            stop_reason=CaseStopReason.TEMPORARY_BLOCKER,
            participation_started=True,
            interview_reached_gate=True,
            gate_decision=GateDecisionKind.APPROVED_LIVE,
            validation_outcome=None,
            outstanding_participation=False,
        )
    )
    assert decision is EntitlementDecision.PENDING


def test_contact_expiry_turns_on_whether_anything_was_outstanding() -> None:
    abandoned, _ = decide(
        _facts(
            stop_reason=CaseStopReason.CONTACT_EXPIRY_WITHOUT_RETURN,
            participation_started=True,
            interview_reached_gate=True,
            gate_decision=GateDecisionKind.APPROVED_LIVE,
            validation_outcome=None,
            outstanding_participation=True,
        )
    )
    assert abandoned is EntitlementDecision.NOT_EARNED

    fulfilled, _ = decide(
        _facts(
            stop_reason=CaseStopReason.CONTACT_EXPIRY_WITHOUT_RETURN,
            participation_started=True,
            interview_reached_gate=True,
            gate_decision=GateDecisionKind.APPROVED_LIVE,
            validation_outcome=None,
            outstanding_participation=False,
        )
    )
    assert fulfilled is EntitlementDecision.EARNED
