"""Pure lifecycle machinery.

A machine is data: a name, a pinned version, its terminal states, and its
transitions. Guards are pure functions that return ``None`` to allow a move or a
reason string to refuse it. Nothing here touches the database; resolving a
transition and planning its commit are separate from applying it.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from utrace_contracts.domain.lifecycle import AggregateKind

from app.db.kernel import AggregateRef, ColumnValue, TransitionRequest
from app.envelope import AuditIntent, CommandIntent, EventAppend, JobIntent
from app.errors import GuardRejectedError, IllegalTransitionError

__all__ = ["Machine", "Transition", "TransitionPlan", "plan_transition", "resolve"]

type Guard[FactsT] = Callable[[FactsT], str | None]


@dataclass(frozen=True, slots=True)
class Transition[FactsT]:
    """One legal move: a trigger, its legal predecessors, its target, its guard."""

    trigger: str
    sources: frozenset[str]
    target: str
    guard: Guard[FactsT] | None


@dataclass(frozen=True, slots=True)
class Machine[FactsT]:
    """One lifecycle. ``version`` is pinned by every transition it authorizes."""

    name: str
    version: int
    initial: str
    terminal: frozenset[str]
    transitions: tuple[Transition[FactsT], ...]


@dataclass(frozen=True, slots=True)
class TransitionPlan:
    """The resolved move, before anything is written."""

    machine: str
    machine_version: int
    trigger: str
    sources: frozenset[str]
    target: str


def resolve[FactsT](
    machine: Machine[FactsT], current_state: str, trigger: str, facts: FactsT
) -> TransitionPlan:
    """Resolve one trigger against the current state, running its guard.

    Raises ``IllegalTransitionError`` when the machine has no such move and
    ``GuardRejectedError`` when the guard refuses it on the supplied facts.
    """
    for transition in machine.transitions:
        if transition.trigger != trigger or current_state not in transition.sources:
            continue
        if transition.guard is not None:
            refusal = transition.guard(facts)
            if refusal is not None:
                raise GuardRejectedError(
                    machine=machine.name, trigger=trigger, reason=refusal
                )
        return TransitionPlan(
            machine=machine.name,
            machine_version=machine.version,
            trigger=trigger,
            sources=transition.sources,
            target=transition.target,
        )
    raise IllegalTransitionError(
        machine=machine.name,
        machine_version=machine.version,
        state=current_state,
        trigger=trigger,
    )


def plan_transition[FactsT](
    machine: Machine[FactsT],
    facts: FactsT,
    *,
    trigger: str,
    current_state: str,
    client_id: UUID,
    aggregate_kind: AggregateKind,
    aggregate_id: UUID,
    expected_revision: int,
    state_column: str,
    mutation: Mapping[str, ColumnValue],
    event: EventAppend,
    jobs: Sequence[JobIntent],
    commands: Sequence[CommandIntent],
    audit: AuditIntent,
    additional_locks: Sequence[AggregateRef],
) -> TransitionRequest:
    """Resolve a trigger and return the request the kernel commits."""
    plan = resolve(machine, current_state, trigger, facts)
    return TransitionRequest(
        client_id=client_id,
        aggregate=AggregateRef(
            kind=aggregate_kind, row_id=aggregate_id, expected_revision=expected_revision
        ),
        machine=plan.machine,
        machine_version=plan.machine_version,
        state_column=state_column,
        from_states=plan.sources,
        to_state=plan.target,
        mutation=mutation,
        event=event,
        jobs=jobs,
        commands=commands,
        audit=audit,
        additional_locks=additional_locks,
    )
