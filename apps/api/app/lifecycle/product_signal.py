"""Product Signal machine.

States and transitions are owned by the lifecycle diagram
(``docs/diagrams/pele/architecture-signal-lifecycle.mmd``). Case File
publication is required for the three ``closed_*`` states; ``canceled``
produces no Case File. The demo grants one Implementation Attempt and no
revisions, so unsuccessful validation closes the case as blocked.

The cancellation scope is the Product Signal's own ``cancellation_scope_id``:
every product descendant, Job, and Command carries it so one terminal transaction
can stop new work and reconcile what was already dispatched.
"""

from dataclasses import dataclass

from utrace_contracts.domain.lifecycle import ProductSignalState

from app.lifecycle.machine import Machine, Transition

__all__ = [
    "NONTERMINAL_STATES",
    "PRODUCT_SIGNAL_MACHINE",
    "ProductSignalFacts",
]

MACHINE_VERSION = 1

NONTERMINAL_STATES = frozenset(
    {
        ProductSignalState.AWAITING_INTERVIEW.value,
        ProductSignalState.INTERVIEWING.value,
        ProductSignalState.GATE_PENDING.value,
        ProductSignalState.IMPLEMENTING.value,
        ProductSignalState.AWAITING_VALIDATION.value,
        ProductSignalState.VALIDATING.value,
    }
)


@dataclass(frozen=True, slots=True)
class ProductSignalFacts:
    """Facts the Product Signal guards read. All are durable, none is a model claim."""

    participation_occurred: bool
    case_file_published: bool


def _requires_participation(facts: ProductSignalFacts) -> str | None:
    if not facts.participation_occurred:
        return "a blocker Case File requires recorded participation"
    return None


def _requires_no_participation(facts: ProductSignalFacts) -> str | None:
    if facts.participation_occurred:
        return "participation occurred; the stop closes as closed_blocked with a Case File"
    return None


def _requires_case_file(facts: ProductSignalFacts) -> str | None:
    if not facts.case_file_published:
        return "a closed_* outcome requires its published Case File"
    return None


def _blocked_closure(facts: ProductSignalFacts) -> str | None:
    """A blocker closure needs both recorded participation and its Case File."""
    return _requires_participation(facts) or _requires_case_file(facts)


PRODUCT_SIGNAL_MACHINE: Machine[ProductSignalFacts] = Machine(
    name="product_signal",
    version=MACHINE_VERSION,
    initial=ProductSignalState.AWAITING_INTERVIEW.value,
    terminal=frozenset(
        {
            ProductSignalState.CLOSED_VALIDATED.value,
            ProductSignalState.CLOSED_NO_IMPLEMENTATION.value,
            ProductSignalState.CLOSED_BLOCKED.value,
            ProductSignalState.CANCELED.value,
        }
    ),
    transitions=(
        Transition(
            trigger="setup_ready",
            sources=frozenset({ProductSignalState.AWAITING_INTERVIEW.value}),
            target=ProductSignalState.INTERVIEWING.value,
            guard=None,
        ),
        Transition(
            trigger="evidence_sufficient",
            sources=frozenset({ProductSignalState.INTERVIEWING.value}),
            target=ProductSignalState.GATE_PENDING.value,
            guard=None,
        ),
        Transition(
            trigger="authorize_implementation",
            sources=frozenset({ProductSignalState.GATE_PENDING.value}),
            target=ProductSignalState.IMPLEMENTING.value,
            guard=None,
        ),
        Transition(
            trigger="no_implementation",
            sources=frozenset({ProductSignalState.GATE_PENDING.value}),
            target=ProductSignalState.CLOSED_NO_IMPLEMENTATION.value,
            guard=_requires_case_file,
        ),
        Transition(
            trigger="candidate_ready",
            sources=frozenset({ProductSignalState.IMPLEMENTING.value}),
            target=ProductSignalState.AWAITING_VALIDATION.value,
            guard=None,
        ),
        Transition(
            trigger="participant_ready",
            sources=frozenset({ProductSignalState.AWAITING_VALIDATION.value}),
            target=ProductSignalState.VALIDATING.value,
            guard=None,
        ),
        # The originating user, trying a new version, asks for a change to it: the
        # change goes back through the gate, within the case's implementation limit.
        Transition(
            trigger="revision_requested",
            sources=frozenset(
                {
                    ProductSignalState.AWAITING_VALIDATION.value,
                    ProductSignalState.VALIDATING.value,
                }
            ),
            target=ProductSignalState.GATE_PENDING.value,
            guard=_requires_participation,
        ),
        # The gate refused a requested change: the version they have stays the one
        # they validate.
        Transition(
            trigger="revision_declined",
            sources=frozenset({ProductSignalState.GATE_PENDING.value}),
            target=ProductSignalState.AWAITING_VALIDATION.value,
            guard=None,
        ),
        Transition(
            trigger="validated",
            sources=frozenset({ProductSignalState.VALIDATING.value}),
            target=ProductSignalState.CLOSED_VALIDATED.value,
            guard=_requires_case_file,
        ),
        # Partial, negative, or unclear validation closes with an unvalidated Case
        # File and no PR: changes are asked for while validating, not after a verdict.
        Transition(
            trigger="validation_unsuccessful",
            sources=frozenset({ProductSignalState.VALIDATING.value}),
            target=ProductSignalState.CLOSED_BLOCKED.value,
            guard=_requires_case_file,
        ),
        # Terminal expiry, unrecoverable blockers, and the client Stop control after
        # participation all close the same way: blocker Case File, no PR.
        Transition(
            trigger="close_blocked",
            sources=NONTERMINAL_STATES,
            target=ProductSignalState.CLOSED_BLOCKED.value,
            guard=_blocked_closure,
        ),
        Transition(
            trigger="client_stop_after_participation",
            sources=NONTERMINAL_STATES,
            target=ProductSignalState.CLOSED_BLOCKED.value,
            guard=_blocked_closure,
        ),
        Transition(
            trigger="client_stop_before_participation",
            sources=NONTERMINAL_STATES,
            target=ProductSignalState.CANCELED.value,
            guard=_requires_no_participation,
        ),
        Transition(
            trigger="cancel",
            sources=NONTERMINAL_STATES,
            target=ProductSignalState.CANCELED.value,
            guard=None,
        ),
    ),
)
