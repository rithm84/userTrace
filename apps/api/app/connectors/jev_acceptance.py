"""Pure acceptance checks for Jev answers.

These functions implement the
[Jev decision contract](../../../../docs/arch-components/cross-component-contracts.md#jev-decision-contract).
They are pure: same inputs, same outcome, no I/O, no clock, no provider call. That is what
makes them testable without a live model and reusable by the control plane unchanged.

The contract's rules appear here as code, not as commentary:

- **Answer types.** An answer is accepted only when its primitive matches the primitive the
  question declared. A `choice` answer never satisfies a `noul` question.
- **Supplied references.** A selected source reference or target is accepted only when it
  is a member of the set the application supplied. Jev selects; it never invents an id.
- **Question-specific thresholds.** Every threshold carries the `question_id` it was
  calibrated for and is rejected against any other question, so a threshold cannot be
  transferred between questions or between primitives.
- **Unknown and no-match alternatives.** Selecting an explicit no-match or unknown option
  is an unresolved decision, never a weak yes. A Choice whose options may all be wrong
  must declare at least one such option, and `require_no_match_option` enforces that
  before the request is sent.
- **No interpolation of a Score into an amount.** A Score expectation may be compared
  against a level threshold and nothing else. This module exposes no function that turns a
  Score into a quantity, because `jev-1.13`'s levels are not numerically calibrated.
- **No complementary or independence assumptions.** Answers in one batch cannot see each
  other, so a probability from one question says nothing about another.
  `accept_all` combines already-accepted *decisions* with a logical AND; nothing here
  multiplies probabilities or derives a no-probability from a separate yes-question.

An unresolved decision is not a failure of the provider. Code chooses scoped retrieval or
conditional reasoning under the owning workflow, as the routing contract requires.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "AcceptanceOutcome",
    "AcceptanceRejection",
    "ChoiceThreshold",
    "JevPrimitive",
    "NoulThreshold",
    "ScoreThreshold",
    "accept_all",
    "check_answer_primitives",
    "check_choice",
    "check_noul",
    "check_score",
    "check_selected_references",
    "require_no_match_option",
]


class JevPrimitive(StrEnum):
    """The three System One primitives a question may declare."""

    NOUL = "noul"
    CHOICE = "choice"
    SCORE = "score"


class AcceptanceRejection(StrEnum):
    """Why a decision was not accepted. Closed set, checked by tests."""

    MISSING_ANSWER = "missing_answer"
    """The batch returned no answer under this question id."""
    WRONG_PRIMITIVE = "wrong_primitive"
    """The answer's primitive differs from the question's declared primitive."""
    THRESHOLD_NOT_CALIBRATED = "threshold_not_calibrated"
    """The threshold belongs to another question id; thresholds are not transferable."""
    UNSUPPLIED_REFERENCE = "unsupplied_reference"
    """A selected reference is not in the supplied candidate set."""
    NO_MATCH_SELECTED = "no_match_selected"
    """The answer chose an explicit no-match or unknown alternative."""
    MISSING_NO_MATCH_OPTION = "missing_no_match_option"
    """The question needed a no-match alternative and declared none."""
    BELOW_THRESHOLD = "below_threshold"
    """The answer is a clear negative against the question's own threshold."""
    UNRESOLVED_DISTRIBUTION = "unresolved_distribution"
    """The distribution is too flat, or falls between the question's yes and no bands."""


@dataclass(frozen=True, slots=True)
class AcceptanceOutcome:
    """The result of checking one decision, or a conjunction of decisions."""

    accepted: bool
    rejections: tuple[AcceptanceRejection, ...]
    detail: str
    """Human-readable reason, safe for logs and an operator-facing blocked-analysis record."""


def _rejected(rejection: AcceptanceRejection, detail: str) -> AcceptanceOutcome:
    """Build a single-reason rejection."""
    return AcceptanceOutcome(accepted=False, rejections=(rejection,), detail=detail)


def _accepted(detail: str) -> AcceptanceOutcome:
    """Build an acceptance."""
    return AcceptanceOutcome(accepted=True, rejections=(), detail=detail)


@dataclass(frozen=True, slots=True)
class NoulThreshold:
    """The yes and no bands calibrated for one Noul question.

    A Noul carries no confidence field, so uncertainty is expressed as the band between
    `no_at_or_below` and `yes_at_or_above`: a probability inside it is unresolved rather
    than a weak yes. A value near 0.5 means similar probability for yes and no, not medium
    intensity.
    """

    question_id: str
    yes_at_or_above: float
    no_at_or_below: float


@dataclass(frozen=True, slots=True)
class ChoiceThreshold:
    """The confidence floor and no-match options calibrated for one Choice question.

    Confidence measures distribution concentration, not correctness, so it gates whether
    the application may act on a selection, never whether the selection is true.
    """

    question_id: str
    minimum_confidence: float
    no_match_options: frozenset[str]


@dataclass(frozen=True, slots=True)
class ScoreThreshold:
    """The level and confidence floors calibrated for one Score question.

    `minimum_level` is compared against the returned expectation. The expectation is never
    read as a quantity, converted to an amount, or interpolated between levels.
    """

    question_id: str
    minimum_level: float
    minimum_confidence: float


def check_answer_primitives(
    *, expected: Mapping[str, JevPrimitive], returned: Mapping[str, str]
) -> AcceptanceOutcome:
    """Check that every asked question came back with an answer of the declared primitive.

    `returned` maps question id to the answer's `type` string as the provider reported it.
    Extra answers uTrace did not ask for are ignored, because irrelevant answers are the
    caller's to drop; missing or mistyped answers are rejections.
    """
    rejections: list[AcceptanceRejection] = []
    details: list[str] = []
    for question_id, primitive in expected.items():
        actual = returned.get(question_id)
        if actual is None:
            rejections.append(AcceptanceRejection.MISSING_ANSWER)
            details.append(f"{question_id}: no answer returned")
            continue
        if actual != primitive.value:
            rejections.append(AcceptanceRejection.WRONG_PRIMITIVE)
            details.append(f"{question_id}: expected {primitive.value}, got {actual}")
    if rejections:
        return AcceptanceOutcome(
            accepted=False, rejections=tuple(rejections), detail="; ".join(details)
        )
    return _accepted(f"{len(expected)} answers matched their declared primitives")


def check_selected_references(
    *, question_id: str, selected: Sequence[str], supplied: Iterable[str]
) -> AcceptanceOutcome:
    """Check that every selected reference was supplied in this question's context.

    An empty selection is accepted: choosing no reference is a legitimate answer, while
    inventing one is not.
    """
    supplied_set = frozenset(supplied)
    unknown = [reference for reference in selected if reference not in supplied_set]
    if unknown:
        return _rejected(
            AcceptanceRejection.UNSUPPLIED_REFERENCE,
            f"{question_id}: references not in the supplied set: {sorted(unknown)}",
        )
    return _accepted(f"{question_id}: {len(selected)} selected references were all supplied")


def check_noul(
    *, question_id: str, noul: float, threshold: NoulThreshold
) -> AcceptanceOutcome:
    """Accept a Noul only as a clear yes against its own calibrated bands."""
    if threshold.question_id != question_id:
        return _rejected(
            AcceptanceRejection.THRESHOLD_NOT_CALIBRATED,
            f"{question_id}: threshold was calibrated for {threshold.question_id}",
        )
    if noul >= threshold.yes_at_or_above:
        return _accepted(f"{question_id}: noul {noul} reached {threshold.yes_at_or_above}")
    if noul <= threshold.no_at_or_below:
        return _rejected(
            AcceptanceRejection.BELOW_THRESHOLD,
            f"{question_id}: noul {noul} is at or below {threshold.no_at_or_below}",
        )
    return _rejected(
        AcceptanceRejection.UNRESOLVED_DISTRIBUTION,
        f"{question_id}: noul {noul} falls between the no and yes bands",
    )


def check_choice(
    *,
    question_id: str,
    choice: str,
    confidence: float,
    supplied_options: Iterable[str],
    threshold: ChoiceThreshold,
) -> AcceptanceOutcome:
    """Accept a Choice only when it names a supplied, non-no-match option confidently."""
    if threshold.question_id != question_id:
        return _rejected(
            AcceptanceRejection.THRESHOLD_NOT_CALIBRATED,
            f"{question_id}: threshold was calibrated for {threshold.question_id}",
        )
    options = frozenset(supplied_options)
    if choice not in options:
        return _rejected(
            AcceptanceRejection.UNSUPPLIED_REFERENCE,
            f"{question_id}: choice {choice!r} was not among the supplied options",
        )
    if choice in threshold.no_match_options:
        return _rejected(
            AcceptanceRejection.NO_MATCH_SELECTED,
            f"{question_id}: choice {choice!r} is an explicit no-match alternative",
        )
    if confidence < threshold.minimum_confidence:
        return _rejected(
            AcceptanceRejection.UNRESOLVED_DISTRIBUTION,
            f"{question_id}: confidence {confidence} is below {threshold.minimum_confidence}",
        )
    return _accepted(f"{question_id}: chose {choice!r} at confidence {confidence}")


def check_score(
    *, question_id: str, score: float, confidence: float, threshold: ScoreThreshold
) -> AcceptanceOutcome:
    """Accept a Score only as a threshold comparison on its own calibrated level.

    The returned expectation is used for one comparison. It is never returned to the
    caller as a magnitude and never interpolated into an amount.
    """
    if threshold.question_id != question_id:
        return _rejected(
            AcceptanceRejection.THRESHOLD_NOT_CALIBRATED,
            f"{question_id}: threshold was calibrated for {threshold.question_id}",
        )
    if confidence < threshold.minimum_confidence:
        return _rejected(
            AcceptanceRejection.UNRESOLVED_DISTRIBUTION,
            f"{question_id}: confidence {confidence} is below {threshold.minimum_confidence}",
        )
    if score < threshold.minimum_level:
        return _rejected(
            AcceptanceRejection.BELOW_THRESHOLD,
            f"{question_id}: score {score} is below level {threshold.minimum_level}",
        )
    return _accepted(f"{question_id}: score {score} reached level {threshold.minimum_level}")


def require_no_match_option(
    *, question_id: str, declared_options: Iterable[str], no_match_options: Iterable[str]
) -> AcceptanceOutcome:
    """Check before sending that a Choice offers at least one declared no-match option.

    Run this while composing the request. A Choice that can only pick a wrong answer
    produces a confident wrong answer.
    """
    declared = frozenset(declared_options)
    present = declared & frozenset(no_match_options)
    if not present:
        return _rejected(
            AcceptanceRejection.MISSING_NO_MATCH_OPTION,
            f"{question_id}: no declared option represents no match or unknown",
        )
    return _accepted(f"{question_id}: no-match alternatives present: {sorted(present)}")


def accept_all(outcomes: Sequence[AcceptanceOutcome]) -> AcceptanceOutcome:
    """Combine independently evaluated decisions with a logical AND.

    This is a conjunction over *decisions already accepted on their own terms*, which is
    what the contract means by code resolving logical combinations. It deliberately does
    not touch probabilities: no product, no complement, no joint distribution.

    An empty sequence is rejected rather than vacuously accepted, so a caller that forgot
    to run its checks does not read as fully accepted.
    """
    if not outcomes:
        return _rejected(
            AcceptanceRejection.MISSING_ANSWER, "no decisions were supplied to combine"
        )
    rejections: tuple[AcceptanceRejection, ...] = ()
    details: list[str] = []
    for outcome in outcomes:
        rejections += outcome.rejections
        details.append(outcome.detail)
    if rejections:
        return AcceptanceOutcome(
            accepted=False, rejections=rejections, detail="; ".join(details)
        )
    return _accepted("; ".join(details))
