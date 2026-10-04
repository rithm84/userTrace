"""Outreach Attempt machine.

``scheduled -> dispatching -> awaiting_response -> claimed`` with the alternates
``declined``, ``expired``, ``unreached``, and ``revoked``. Retries stay inside one
logical attempt: a provider resend does not create a second attempt.
"""

from dataclasses import dataclass

from utrace_contracts.domain.lifecycle import OutreachAttemptState

from app.lifecycle.machine import Machine, Transition

__all__ = ["OUTREACH_ATTEMPT_MACHINE", "OutreachAttemptFacts"]

MACHINE_VERSION = 1

_OPEN_STATES = frozenset(
    {
        OutreachAttemptState.SCHEDULED.value,
        OutreachAttemptState.DISPATCHING.value,
        OutreachAttemptState.AWAITING_RESPONSE.value,
    }
)


@dataclass(frozen=True, slots=True)
class OutreachAttemptFacts:
    """Facts the Outreach Attempt guards read."""

    bound_identity_matches: bool
    expired: bool
    contact_budget_remaining: int


def _claimable(facts: OutreachAttemptFacts) -> str | None:
    if not facts.bound_identity_matches:
        return "a claim must prove the invited Discord identity"
    if facts.expired:
        return "the invitation has expired"
    return None


def _dispatchable(facts: OutreachAttemptFacts) -> str | None:
    if facts.contact_budget_remaining <= 0:
        return "the pinned contact budget for this person and client is exhausted"
    return None


OUTREACH_ATTEMPT_MACHINE: Machine[OutreachAttemptFacts] = Machine(
    name="outreach_attempt",
    version=MACHINE_VERSION,
    initial=OutreachAttemptState.SCHEDULED.value,
    terminal=frozenset(
        {
            OutreachAttemptState.CLAIMED.value,
            OutreachAttemptState.DECLINED.value,
            OutreachAttemptState.EXPIRED.value,
            OutreachAttemptState.UNREACHED.value,
            OutreachAttemptState.REVOKED.value,
        }
    ),
    transitions=(
        Transition(
            trigger="dispatch",
            sources=frozenset({OutreachAttemptState.SCHEDULED.value}),
            target=OutreachAttemptState.DISPATCHING.value,
            guard=_dispatchable,
        ),
        Transition(
            trigger="delivery_confirmed",
            sources=frozenset({OutreachAttemptState.DISPATCHING.value}),
            target=OutreachAttemptState.AWAITING_RESPONSE.value,
            guard=None,
        ),
        Transition(
            trigger="delivery_failed",
            sources=frozenset({OutreachAttemptState.DISPATCHING.value}),
            target=OutreachAttemptState.UNREACHED.value,
            guard=None,
        ),
        Transition(
            trigger="claim",
            sources=frozenset({OutreachAttemptState.AWAITING_RESPONSE.value}),
            target=OutreachAttemptState.CLAIMED.value,
            guard=_claimable,
        ),
        Transition(
            trigger="decline",
            sources=frozenset({OutreachAttemptState.AWAITING_RESPONSE.value}),
            target=OutreachAttemptState.DECLINED.value,
            guard=None,
        ),
        Transition(
            trigger="expire",
            sources=_OPEN_STATES,
            target=OutreachAttemptState.EXPIRED.value,
            guard=None,
        ),
        Transition(
            trigger="revoke",
            sources=_OPEN_STATES,
            target=OutreachAttemptState.REVOKED.value,
            guard=None,
        ),
    ),
)
