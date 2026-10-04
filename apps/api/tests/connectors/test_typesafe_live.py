"""Live smokes through Vercel AI Gateway: one batched Jev request carrying a Noul, a
Choice, and a Score, and one request with a key the gateway rejects.

The state is the Shopalytics demo's canonical Discord request, so the smoke exercises the
shape intake actually sends: independent questions over one supplied state, with an
explicit no-match option on the Choice.
"""

from __future__ import annotations

import pytest
from typesafe_sdk import Choice, Noul, Score

from app.connectors.failures import ConnectorFailure, RetryAdvice, TechnicalCategory
from app.connectors.jev_acceptance import (
    ChoiceThreshold,
    JevPrimitive,
    NoulThreshold,
    ScoreThreshold,
    accept_all,
    check_answer_primitives,
    check_choice,
    check_noul,
    check_score,
    require_no_match_option,
)
from app.connectors.typesafe_jev import JevClient
from tests.connectors.conftest import optional_env, require_env

_CANONICAL_REQUEST = (
    "I keep rebuilding the same conversion filters every Monday. Can Shopalytics let me "
    "save a view for TikTok traffic from women 25-34 on products with negative sizing "
    "reviews, then reopen it later?"
)
_FLOW_OPTIONS = ("bugfix", "feature_request", "unclear")


async def test_batched_noul_choice_and_score_round_trip() -> None:
    values = require_env("VERCEL_AI_GATEWAY_API_KEY")
    model_id = optional_env("UTRACE_JEV_MODEL_ID") or "typesafe-ai/jev"

    precheck = require_no_match_option(
        question_id="flow_type",
        declared_options=_FLOW_OPTIONS,
        no_match_options=["unclear"],
    )
    assert precheck.accepted, precheck.detail

    async with JevClient(
        api_key=values["VERCEL_AI_GATEWAY_API_KEY"], model_id=model_id, timeout_seconds=30.0
    ) as client:
        result = await client.judge(
            state={"message": _CANONICAL_REQUEST, "surface": "discord"},
            questions={
                "warrants_investigation": Noul(
                    instructions=(
                        "Does `message` report a product failure, workflow friction, or a "
                        "capability gap that a product team should investigate?"
                    ),
                    criteria={
                        "true": "The person describes a problem or an unmet need.",
                        "false": "Unrelated chatter, or a question answered by a pointer.",
                    },
                ),
                "flow_type": Choice(
                    instructions="Which flow does `message` belong to?",
                    criteria={
                        "bugfix": "The person reports the product failing at something it should do.",
                        "feature_request": "The person asks for a new or changed capability.",
                        "unclear": "The message does not settle which of the other options applies.",
                    },
                ),
                "recurrence": Score(
                    instructions="How often does `message` say the described work recurs?",
                    criteria=[
                        "No recurrence is described.",
                        "The work recurs occasionally.",
                        "The work recurs on a stated, regular cadence.",
                    ],
                ),
            },
        )

    assert result.model_id == model_id
    assert result.input_tokens > 0

    primitives = check_answer_primitives(
        expected={
            "warrants_investigation": JevPrimitive.NOUL,
            "flow_type": JevPrimitive.CHOICE,
            "recurrence": JevPrimitive.SCORE,
        },
        returned=result.returned_primitives(),
    )
    assert primitives.accepted, primitives.detail

    noul = result.nouls["warrants_investigation"]
    choice = result.choices["flow_type"]
    score = result.scores["recurrence"]

    # Probabilities and evidence are retained, not collapsed into a verdict.
    assert 0.0 <= noul.noul <= 1.0
    assert set(choice.probabilities) == set(_FLOW_OPTIONS)
    assert abs(sum(choice.probabilities.values()) - 1.0) < 0.01
    assert set(score.legend) == {0, 1, 2}
    assert abs(sum(score.probabilities.values()) - 1.0) < 0.01

    decisions = accept_all(
        [
            check_noul(
                question_id="warrants_investigation",
                noul=noul.noul,
                threshold=NoulThreshold(
                    question_id="warrants_investigation",
                    yes_at_or_above=0.7,
                    no_at_or_below=0.3,
                ),
            ),
            check_choice(
                question_id="flow_type",
                choice=choice.choice,
                confidence=choice.confidence,
                supplied_options=_FLOW_OPTIONS,
                threshold=ChoiceThreshold(
                    question_id="flow_type",
                    minimum_confidence=0.5,
                    no_match_options=frozenset({"unclear"}),
                ),
            ),
            check_score(
                question_id="recurrence",
                score=score.score,
                confidence=score.confidence,
                threshold=ScoreThreshold(
                    question_id="recurrence", minimum_level=1.0, minimum_confidence=0.4
                ),
            ),
        ]
    )
    assert decisions.accepted, decisions.detail
    assert choice.choice == "feature_request", choice.probabilities


async def test_a_rejected_gateway_key_fails_as_authentication() -> None:
    rejected_key = "not-a-gateway-key"
    async with JevClient(
        api_key=rejected_key, model_id="typesafe-ai/jev", timeout_seconds=30.0
    ) as client:
        with pytest.raises(ConnectorFailure) as raised:
            await client.judge(
                state={"message": _CANONICAL_REQUEST},
                questions={"warrants_investigation": Noul(instructions="Is this a request?")},
            )

    assert raised.value.category is TechnicalCategory.AUTHENTICATION
    assert raised.value.retry_advice is RetryAdvice.AFTER_CONDITION_CHANGE
    assert raised.value.detail.provider_error_code == "authentication_error"
    assert rejected_key not in str(raised.value)
