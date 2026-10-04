"""Validation Attempt machine.

Only the immutable originating user validates, against one exact candidate and
its agreed dataset. ``validated`` needs explicit confirmation plus proportionate
affirmative proof for every acceptance criterion; silence, elapsed time, and the
absence of errors prove nothing. An interruption is not a verdict.
"""

from dataclasses import dataclass

from utrace_contracts.domain.lifecycle import ValidationAttemptState, ValidationOutcome

from app.lifecycle.machine import Machine, Transition

__all__ = ["VALIDATION_ATTEMPT_MACHINE", "ValidationAttemptFacts"]

MACHINE_VERSION = 1


@dataclass(frozen=True, slots=True)
class ValidationAttemptFacts:
    """Facts the Validation Attempt guards read."""

    originating_user_matches: bool
    candidate_ready: bool
    observation_ready: bool
    same_candidate: bool
    outcome: ValidationOutcome | None
    criteria_total: int
    criteria_affirmatively_proven: int
    user_confirmed_every_criterion: bool
    unresolved_contradiction: bool


def _may_begin(facts: ValidationAttemptFacts) -> str | None:
    if not facts.originating_user_matches:
        return "only the immutable originating user may validate"
    if not facts.candidate_ready:
        return "criterion-level validation requires the exact ready candidate"
    if not facts.observation_ready:
        return "criterion-level validation requires the complete observation path"
    return None


def _may_resume(facts: ValidationAttemptFacts) -> str | None:
    if not facts.same_candidate:
        return "validation resumes only against the same exact candidate"
    return _may_begin(facts)


def _may_complete(facts: ValidationAttemptFacts) -> str | None:
    if facts.outcome is None:
        return "a completed Validation Attempt must record a structured outcome"
    if facts.outcome is not ValidationOutcome.VALIDATED:
        return None
    if facts.unresolved_contradiction:
        return "an unresolved contradiction prevents a supported conclusion"
    if not facts.user_confirmed_every_criterion:
        return "validated requires explicit originating-user confirmation per criterion"
    if facts.criteria_affirmatively_proven < facts.criteria_total:
        return (
            "validated requires affirmative proof for every criterion: "
            f"{facts.criteria_affirmatively_proven} of {facts.criteria_total} proven"
        )
    return None


VALIDATION_ATTEMPT_MACHINE: Machine[ValidationAttemptFacts] = Machine(
    name="validation_attempt",
    version=MACHINE_VERSION,
    initial=ValidationAttemptState.PENDING.value,
    terminal=frozenset(
        {
            ValidationAttemptState.COMPLETED.value,
            ValidationAttemptState.ABANDONED.value,
        }
    ),
    transitions=(
        Transition(
            trigger="begin",
            sources=frozenset({ValidationAttemptState.PENDING.value}),
            target=ValidationAttemptState.IN_PROGRESS.value,
            guard=_may_begin,
        ),
        Transition(
            trigger="interrupt",
            sources=frozenset({ValidationAttemptState.IN_PROGRESS.value}),
            target=ValidationAttemptState.INTERRUPTED.value,
            guard=None,
        ),
        Transition(
            trigger="resume",
            sources=frozenset({ValidationAttemptState.INTERRUPTED.value}),
            target=ValidationAttemptState.IN_PROGRESS.value,
            guard=_may_resume,
        ),
        Transition(
            trigger="complete",
            sources=frozenset({ValidationAttemptState.IN_PROGRESS.value}),
            target=ValidationAttemptState.COMPLETED.value,
            guard=_may_complete,
        ),
        Transition(
            trigger="abandon",
            sources=frozenset(
                {
                    ValidationAttemptState.PENDING.value,
                    ValidationAttemptState.IN_PROGRESS.value,
                    ValidationAttemptState.INTERRUPTED.value,
                }
            ),
            target=ValidationAttemptState.ABANDONED.value,
            guard=None,
        ),
    ),
)
