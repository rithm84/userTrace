"""Outreach Offer machine.

``reserved -> earned`` when the originating user completed the participation the
offer promised, or ``reserved -> released`` when they did not. Both are terminal:
an earned reward stays owed even if issuance fails, and a released reservation
returns its allowance without erasing the record that it existed.
"""

from dataclasses import dataclass

from utrace_contracts.domain.lifecycle import OfferState

from app.lifecycle.machine import Machine, Transition

__all__ = ["OFFER_MACHINE", "OfferFacts"]

MACHINE_VERSION = 1


@dataclass(frozen=True, slots=True)
class OfferFacts:
    """Facts the offer guards read, all derived from recorded participation."""

    participation_complete: bool
    outstanding_participation: bool
    entitlement_recorded: bool


def _may_earn(facts: OfferFacts) -> str | None:
    if not facts.participation_complete:
        return "the offer is earned only once the promised participation is complete"
    if facts.outstanding_participation:
        return "participation uTrace asked for is still outstanding"
    if not facts.entitlement_recorded:
        return "an earned offer records its one entitlement in the same transaction"
    return None


def _may_release(facts: OfferFacts) -> str | None:
    if facts.participation_complete and not facts.outstanding_participation:
        return "the participation was completed; the reward is earned, not released"
    return None


OFFER_MACHINE: Machine[OfferFacts] = Machine(
    name="outreach_offer",
    version=MACHINE_VERSION,
    initial=OfferState.RESERVED.value,
    terminal=frozenset({OfferState.EARNED.value, OfferState.RELEASED.value}),
    transitions=(
        Transition(
            trigger="earn",
            sources=frozenset({OfferState.RESERVED.value}),
            target=OfferState.EARNED.value,
            guard=_may_earn,
        ),
        Transition(
            trigger="release",
            sources=frozenset({OfferState.RESERVED.value}),
            target=OfferState.RELEASED.value,
            guard=_may_release,
        ),
    ),
)
