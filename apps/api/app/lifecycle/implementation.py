"""Implementation Attempt machine.

One nonterminal attempt exists per Product Signal. Technical retries and
check-fix cycles reuse the attempt's identity and consume no revision; a
separately gated revision gets its own attempt. Every mutating step requires the
current fencing token, so a stale worker can log but never publish.
"""

from dataclasses import dataclass

from utrace_contracts.domain.lifecycle import ImplementationAttemptState

from app.lifecycle.machine import Machine, Transition

__all__ = [
    "IMPLEMENTATION_ATTEMPT_MACHINE",
    "NONTERMINAL_ATTEMPT_STATES",
    "ImplementationAttemptFacts",
]

MACHINE_VERSION = 1

NONTERMINAL_ATTEMPT_STATES = frozenset(
    {
        ImplementationAttemptState.AUTHORIZED.value,
        ImplementationAttemptState.QUEUED.value,
        ImplementationAttemptState.ACQUIRING_ENVIRONMENT.value,
        ImplementationAttemptState.EXECUTING.value,
        ImplementationAttemptState.CHECKING.value,
        ImplementationAttemptState.PUBLISHING_CANDIDATE.value,
        ImplementationAttemptState.PAUSED.value,
        ImplementationAttemptState.INFRASTRUCTURE_BLOCKED.value,
    }
)

_RUNNING_STATES = frozenset(
    {
        ImplementationAttemptState.EXECUTING.value,
        ImplementationAttemptState.CHECKING.value,
    }
)


@dataclass(frozen=True, slots=True)
class ImplementationAttemptFacts:
    """Facts the Implementation Attempt guards read."""

    lease_active: bool
    fencing_token_current: bool
    execution_budget_remaining: bool
    within_return_deadline: bool
    readiness_fresh: bool
    diff_within_approved_scope: bool


def _lease_authorizes_mutation(facts: ImplementationAttemptFacts) -> str | None:
    if not facts.lease_active:
        return "workspace mutation requires an active attempt-scoped Environment Lease"
    if not facts.fencing_token_current:
        return "the operation carries a stale fencing token"
    return None


def _may_execute(facts: ImplementationAttemptFacts) -> str | None:
    refusal = _lease_authorizes_mutation(facts)
    if refusal is not None:
        return refusal
    if not facts.execution_budget_remaining:
        return "the pinned execution budget is exhausted"
    return None


def _may_publish(facts: ImplementationAttemptFacts) -> str | None:
    refusal = _lease_authorizes_mutation(facts)
    if refusal is not None:
        return refusal
    if not facts.diff_within_approved_scope:
        return "the accepted diff leaves the approved-scope object"
    return None


def _may_resume(facts: ImplementationAttemptFacts) -> str | None:
    if not facts.readiness_fresh:
        return "resume requires fresh readiness checks"
    return _may_execute(facts)


def _may_resume_after_repair(facts: ImplementationAttemptFacts) -> str | None:
    if not facts.within_return_deadline:
        return "the case-level return-and-closure deadline has expired"
    return _may_resume(facts)


IMPLEMENTATION_ATTEMPT_MACHINE: Machine[ImplementationAttemptFacts] = Machine(
    name="implementation_attempt",
    version=MACHINE_VERSION,
    initial=ImplementationAttemptState.AUTHORIZED.value,
    terminal=frozenset(
        {
            ImplementationAttemptState.CANDIDATE_READY.value,
            ImplementationAttemptState.SCOPE_CHANGE_REQUIRED.value,
            ImplementationAttemptState.CHECKS_FAILED.value,
            ImplementationAttemptState.UNSAFE.value,
            ImplementationAttemptState.CANCELED.value,
            ImplementationAttemptState.BUDGET_EXHAUSTED.value,
        }
    ),
    transitions=(
        Transition(
            trigger="queue",
            sources=frozenset({ImplementationAttemptState.AUTHORIZED.value}),
            target=ImplementationAttemptState.QUEUED.value,
            guard=None,
        ),
        Transition(
            trigger="acquire_environment",
            sources=frozenset({ImplementationAttemptState.QUEUED.value}),
            target=ImplementationAttemptState.ACQUIRING_ENVIRONMENT.value,
            guard=None,
        ),
        Transition(
            trigger="begin_execution",
            sources=frozenset({ImplementationAttemptState.ACQUIRING_ENVIRONMENT.value}),
            target=ImplementationAttemptState.EXECUTING.value,
            guard=_may_execute,
        ),
        Transition(
            trigger="run_checks",
            sources=frozenset({ImplementationAttemptState.EXECUTING.value}),
            target=ImplementationAttemptState.CHECKING.value,
            guard=_lease_authorizes_mutation,
        ),
        # A correction cycle stays inside the attempt and consumes no revision.
        Transition(
            trigger="correction_cycle",
            sources=frozenset({ImplementationAttemptState.CHECKING.value}),
            target=ImplementationAttemptState.EXECUTING.value,
            guard=_may_execute,
        ),
        Transition(
            trigger="checks_passed",
            sources=frozenset({ImplementationAttemptState.CHECKING.value}),
            target=ImplementationAttemptState.PUBLISHING_CANDIDATE.value,
            guard=_may_publish,
        ),
        Transition(
            trigger="publish_candidate",
            sources=frozenset({ImplementationAttemptState.PUBLISHING_CANDIDATE.value}),
            target=ImplementationAttemptState.CANDIDATE_READY.value,
            guard=_may_publish,
        ),
        Transition(
            trigger="checks_failed",
            sources=frozenset({ImplementationAttemptState.CHECKING.value}),
            target=ImplementationAttemptState.CHECKS_FAILED.value,
            guard=None,
        ),
        Transition(
            trigger="require_scope_change",
            sources=_RUNNING_STATES,
            target=ImplementationAttemptState.SCOPE_CHANGE_REQUIRED.value,
            guard=None,
        ),
        Transition(
            trigger="mark_unsafe",
            sources=_RUNNING_STATES,
            target=ImplementationAttemptState.UNSAFE.value,
            guard=None,
        ),
        Transition(
            trigger="pause",
            sources=frozenset(
                {
                    ImplementationAttemptState.QUEUED.value,
                    ImplementationAttemptState.ACQUIRING_ENVIRONMENT.value,
                    ImplementationAttemptState.EXECUTING.value,
                    ImplementationAttemptState.CHECKING.value,
                    ImplementationAttemptState.PUBLISHING_CANDIDATE.value,
                }
            ),
            target=ImplementationAttemptState.PAUSED.value,
            guard=None,
        ),
        Transition(
            trigger="resume",
            sources=frozenset({ImplementationAttemptState.PAUSED.value}),
            target=ImplementationAttemptState.EXECUTING.value,
            guard=_may_resume,
        ),
        Transition(
            trigger="block_infrastructure",
            sources=frozenset(
                {
                    ImplementationAttemptState.QUEUED.value,
                    ImplementationAttemptState.ACQUIRING_ENVIRONMENT.value,
                    ImplementationAttemptState.EXECUTING.value,
                    ImplementationAttemptState.CHECKING.value,
                    ImplementationAttemptState.PUBLISHING_CANDIDATE.value,
                    ImplementationAttemptState.PAUSED.value,
                }
            ),
            target=ImplementationAttemptState.INFRASTRUCTURE_BLOCKED.value,
            guard=None,
        ),
        Transition(
            trigger="resume_after_repair",
            sources=frozenset({ImplementationAttemptState.INFRASTRUCTURE_BLOCKED.value}),
            target=ImplementationAttemptState.EXECUTING.value,
            guard=_may_resume_after_repair,
        ),
        Transition(
            trigger="exhaust_budget",
            sources=NONTERMINAL_ATTEMPT_STATES,
            target=ImplementationAttemptState.BUDGET_EXHAUSTED.value,
            guard=None,
        ),
        Transition(
            trigger="cancel",
            sources=NONTERMINAL_ATTEMPT_STATES,
            target=ImplementationAttemptState.CANCELED.value,
            guard=None,
        ),
    ),
)
