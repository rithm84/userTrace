"""Intake Item machine.

``received -> routing -> routed`` with ``routing_blocked -> routing`` recovery.
Cancellation is legal only before a route is committed. Routing is serialized per
client by the controller that owns the lock.
"""

from dataclasses import dataclass

from utrace_contracts.domain.lifecycle import IntakeItemState, RoutingOutcome

from app.lifecycle.machine import Machine, Transition

__all__ = ["INTAKE_ITEM_MACHINE", "IntakeItemFacts"]

MACHINE_VERSION = 1


@dataclass(frozen=True, slots=True)
class IntakeItemFacts:
    """Facts the Intake Item guards read."""

    routing_outcome: RoutingOutcome | None
    blocked_reason: str | None
    route_committed: bool


def _require_outcome(facts: IntakeItemFacts) -> str | None:
    if facts.routing_outcome is None:
        return "a routed Intake Item must commit exactly one routing outcome"
    return None


def _require_blocked_reason(facts: IntakeItemFacts) -> str | None:
    if not facts.blocked_reason:
        return "routing_blocked requires the unresolved required facts as its reason"
    return None


def _require_uncommitted(facts: IntakeItemFacts) -> str | None:
    if facts.route_committed:
        return "cancellation is authorized only before the route is committed"
    return None


INTAKE_ITEM_MACHINE: Machine[IntakeItemFacts] = Machine(
    name="intake_item",
    version=MACHINE_VERSION,
    initial=IntakeItemState.RECEIVED.value,
    terminal=frozenset({IntakeItemState.ROUTED.value, IntakeItemState.CANCELED.value}),
    transitions=(
        Transition(
            trigger="begin_routing",
            sources=frozenset(
                {IntakeItemState.RECEIVED.value, IntakeItemState.ROUTING_BLOCKED.value}
            ),
            target=IntakeItemState.ROUTING.value,
            guard=None,
        ),
        Transition(
            trigger="commit_route",
            sources=frozenset({IntakeItemState.ROUTING.value}),
            target=IntakeItemState.ROUTED.value,
            guard=_require_outcome,
        ),
        Transition(
            trigger="block_routing",
            sources=frozenset({IntakeItemState.ROUTING.value}),
            target=IntakeItemState.ROUTING_BLOCKED.value,
            guard=_require_blocked_reason,
        ),
        Transition(
            trigger="cancel",
            sources=frozenset(
                {
                    IntakeItemState.RECEIVED.value,
                    IntakeItemState.ROUTING.value,
                    IntakeItemState.ROUTING_BLOCKED.value,
                }
            ),
            target=IntakeItemState.CANCELED.value,
            guard=_require_uncommitted,
        ),
    ),
)
