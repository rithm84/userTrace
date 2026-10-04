"""The batched Jev questions routing asks, and how their answers are accepted.

Three independent questions share one supplied state: whether the batch warrants
investigation, its flow type, and whether the supplied context is sufficient.
None of them consumes another's answer, so they travel in one request.

Thresholds are calibrated per question and pinned beside the question
definitions. An answer that is neither a clear yes nor a clear no is unresolved:
code then retrieves scoped context and re-evaluates, and unresolved uncertainty
ends as `routing_blocked` rather than an invented noise verdict.

Contract: docs/arch-components/cross-component-contracts.md#jev-decision-contract
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from typesafe_sdk import Choice, Noul
from utrace_contracts.domain.lifecycle import FlowType

from app.connectors.jev_acceptance import (
    AcceptanceOutcome,
    AcceptanceRejection,
    ChoiceThreshold,
    JevPrimitive,
    NoulThreshold,
    check_answer_primitives,
    check_choice,
    check_noul,
    require_no_match_option,
)
from app.connectors.typesafe_jev import JevBatchResult, JevClient
from app.envelope import JsonValue
from app.intake.context import RoutingContext
from app.logging import get_logger

__all__ = [
    "CONTEXT_SUFFICIENT",
    "FLOW_TYPE",
    "WARRANTS_INVESTIGATION",
    "RoutingJudgement",
    "ask_routing_questions",
]

_log = get_logger(__name__)

WARRANTS_INVESTIGATION: Final[str] = "warrants_investigation"
FLOW_TYPE: Final[str] = "flow_type"
CONTEXT_SUFFICIENT: Final[str] = "context_sufficient"

_FLOW_UNCLEAR: Final[str] = "unclear"

_FLOW_BY_OPTION: Final[Mapping[str, FlowType]] = {
    FlowType.BUGFIX_FLOW.value: FlowType.BUGFIX_FLOW,
    FlowType.FEATURE_REQUEST_FLOW.value: FlowType.FEATURE_REQUEST_FLOW,
}

_WARRANTS_THRESHOLD: Final[NoulThreshold] = NoulThreshold(
    question_id=WARRANTS_INVESTIGATION, yes_at_or_above=0.70, no_at_or_below=0.30
)
_CONTEXT_THRESHOLD: Final[NoulThreshold] = NoulThreshold(
    question_id=CONTEXT_SUFFICIENT, yes_at_or_above=0.65, no_at_or_below=0.35
)
_FLOW_THRESHOLD: Final[ChoiceThreshold] = ChoiceThreshold(
    question_id=FLOW_TYPE,
    minimum_confidence=0.55,
    no_match_options=frozenset({_FLOW_UNCLEAR}),
)


@dataclass(frozen=True, slots=True)
class RoutingJudgement:
    """The typed answers routing accepted, and why anything was left unresolved.

    ``None`` means unresolved. ``warrants_investigation`` of ``False`` is an
    accepted clear negative, which is what noise is; it is not an absent answer.
    """

    model_id: str
    warrants_investigation: bool | None
    flow_type: FlowType | None
    context_sufficient: bool | None
    unresolved: tuple[str, ...]
    questions: Mapping[str, JsonValue]
    answers: Mapping[str, JsonValue]
    acceptance: Mapping[str, JsonValue]

    def resolved(self) -> bool:
        """Return whether every question routing depends on was resolved."""
        return not self.unresolved


type ComposedQuestion = Noul | Choice


def _questions() -> dict[str, ComposedQuestion]:
    """Compose the batch. Every question is answerable from the supplied state."""
    return {
        WARRANTS_INVESTIGATION: Noul(
            instructions=(
                "Does `report` describe a product failure, workflow friction, or a "
                "capability gap in the product described by `product_overview` that a "
                "product team should investigate?"
            ),
            criteria={
                "true": (
                    "The person reports a problem, an unmet need, or friction in a "
                    "workflow. A capability already existing in `product_overview` "
                    "does not make the report unworthy of investigation."
                ),
                "false": (
                    "Unrelated chatter, praise, or a question that a factual pointer "
                    "answers without any product investigation."
                ),
            },
        ),
        FLOW_TYPE: Choice(
            instructions=(
                "Which flow does `report` belong to, judged only on what the person "
                "wrote?"
            ),
            criteria={
                FlowType.BUGFIX_FLOW.value: (
                    "The person reports or exhibits a product failure: something "
                    "behaves incorrectly or stops working."
                ),
                FlowType.FEATURE_REQUEST_FLOW.value: (
                    "The person asks for, or implies the need for, a new or changed "
                    "capability."
                ),
                _FLOW_UNCLEAR: (
                    "The report does not say enough to separate a failure from a "
                    "capability gap."
                ),
            },
        ),
        CONTEXT_SUFFICIENT: Noul(
            instructions=(
                "Is `report`, together with the other supplied fields, enough to "
                "understand the concern without fetching more conversation or "
                "product documentation?"
            ),
            criteria={
                "true": "The concern is stated plainly enough to act on.",
                "false": (
                    "The report depends on something not supplied, such as an earlier "
                    "message, a referenced thread, or product behaviour that is not "
                    "described."
                ),
            },
        ),
    }


def _expected_primitives(
    composed: Mapping[str, ComposedQuestion],
) -> dict[str, JevPrimitive]:
    """Declare the primitive each asked question must come back as."""
    return {
        name: JevPrimitive.NOUL if isinstance(question, Noul) else JevPrimitive.CHOICE
        for name, question in composed.items()
    }


def _noul_decision(
    result: JevBatchResult, *, question_id: str, threshold: NoulThreshold
) -> tuple[bool | None, AcceptanceOutcome]:
    """Resolve one Noul into yes, no, or unresolved against its own bands."""
    answer = result.nouls.get(question_id)
    if answer is None:
        return None, AcceptanceOutcome(
            accepted=False,
            rejections=(AcceptanceRejection.MISSING_ANSWER,),
            detail=f"{question_id}: no answer returned",
        )
    outcome = check_noul(
        question_id=question_id, noul=answer.noul, threshold=threshold
    )
    if outcome.accepted:
        return True, outcome
    if AcceptanceRejection.BELOW_THRESHOLD in outcome.rejections:
        return False, outcome
    return None, outcome


def _choice_decision(
    result: JevBatchResult,
    *,
    question_id: str,
    supplied_options: tuple[str, ...],
    threshold: ChoiceThreshold,
) -> tuple[str | None, bool, AcceptanceOutcome]:
    """Resolve one Choice into a selection, an explicit no-match, or unresolved."""
    answer = result.choices.get(question_id)
    if answer is None:
        return (
            None,
            False,
            AcceptanceOutcome(
                accepted=False,
                rejections=(AcceptanceRejection.MISSING_ANSWER,),
                detail=f"{question_id}: no answer returned",
            ),
        )
    outcome = check_choice(
        question_id=question_id,
        choice=answer.choice,
        confidence=answer.confidence,
        supplied_options=supplied_options,
        threshold=threshold,
    )
    if outcome.accepted:
        return answer.choice, True, outcome
    if AcceptanceRejection.NO_MATCH_SELECTED in outcome.rejections:
        return None, True, outcome
    return None, False, outcome


async def ask_routing_questions(
    client: JevClient, *, context: RoutingContext
) -> RoutingJudgement:
    """Ask one batched routing request and accept its answers against the contract."""
    composed = _questions()
    flow_options = (
        FlowType.BUGFIX_FLOW.value,
        FlowType.FEATURE_REQUEST_FLOW.value,
        _FLOW_UNCLEAR,
    )
    precheck = require_no_match_option(
        question_id=FLOW_TYPE,
        declared_options=flow_options,
        no_match_options=(_FLOW_UNCLEAR,),
    )
    if not precheck.accepted:
        raise ValueError(precheck.detail)

    result = await client.judge(state=dict(context.state()), questions=composed)

    acceptance: dict[str, JsonValue] = {}
    unresolved: list[str] = []
    primitives = check_answer_primitives(
        expected=_expected_primitives(composed), returned=result.returned_primitives()
    )
    acceptance["primitives"] = primitives.detail
    if not primitives.accepted:
        unresolved.append("answer_primitives")

    warrants, warrants_outcome = _noul_decision(
        result, question_id=WARRANTS_INVESTIGATION, threshold=_WARRANTS_THRESHOLD
    )
    acceptance[WARRANTS_INVESTIGATION] = warrants_outcome.detail
    if warrants is None:
        unresolved.append(WARRANTS_INVESTIGATION)

    sufficient, sufficient_outcome = _noul_decision(
        result, question_id=CONTEXT_SUFFICIENT, threshold=_CONTEXT_THRESHOLD
    )
    acceptance[CONTEXT_SUFFICIENT] = sufficient_outcome.detail
    if sufficient is None:
        unresolved.append(CONTEXT_SUFFICIENT)

    flow_choice, _, flow_outcome = _choice_decision(
        result,
        question_id=FLOW_TYPE,
        supplied_options=flow_options,
        threshold=_FLOW_THRESHOLD,
    )
    acceptance[FLOW_TYPE] = flow_outcome.detail
    flow = _FLOW_BY_OPTION.get(flow_choice) if flow_choice is not None else None
    if flow is None and warrants is not False:
        unresolved.append(FLOW_TYPE)

    judgement = RoutingJudgement(
        model_id=result.model_id,
        warrants_investigation=warrants,
        flow_type=flow,
        context_sufficient=sufficient,
        unresolved=tuple(unresolved),
        questions=_question_record(composed),
        answers=_answer_record(result),
        acceptance=acceptance,
    )
    _log.info(
        "intake_routing_judged",
        client_id=str(context.scope.client_id),
        intake_item_id=str(context.scope.intake_item_id),
        model_id=result.model_id,
        warrants_investigation=warrants,
        flow_type=flow.value if flow is not None else None,
        unresolved=judgement.unresolved,
    )
    return judgement


def _question_record(
    composed: Mapping[str, ComposedQuestion],
) -> Mapping[str, JsonValue]:
    """Return the asked question definitions, for the immutable decision record."""
    return {name: question.model_dump(mode="json") for name, question in composed.items()}


def _answer_record(result: JevBatchResult) -> Mapping[str, JsonValue]:
    """Return the returned probabilities and selections, retained with the decision."""
    record: dict[str, JsonValue] = {
        name: {"type": "noul", "noul": answer.noul}
        for name, answer in result.nouls.items()
    }
    for name, choice in result.choices.items():
        record[name] = {
            "type": "choice",
            "choice": choice.choice,
            "confidence": choice.confidence,
            "probabilities": dict(choice.probabilities),
        }
    record["usage"] = {
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }
    return record
