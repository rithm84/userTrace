"""Pure tests for the Jev acceptance checks.

Each test names the rule from the Jev decision contract that it holds in place.
"""

from __future__ import annotations

from app.connectors.jev_acceptance import (
    AcceptanceRejection,
    ChoiceThreshold,
    JevPrimitive,
    NoulThreshold,
    ScoreThreshold,
    accept_all,
    check_answer_primitives,
    check_choice,
    check_noul,
    check_score,
    check_selected_references,
    require_no_match_option,
)

_FLOW_CHOICE = ChoiceThreshold(
    question_id="flow_type",
    minimum_confidence=0.7,
    no_match_options=frozenset({"unclear"}),
)
_WARRANTS_NOUL = NoulThreshold(
    question_id="warrants_investigation", yes_at_or_above=0.8, no_at_or_below=0.2
)
_IMPACT_SCORE = ScoreThreshold(
    question_id="impact", minimum_level=1.0, minimum_confidence=0.6
)


def test_answer_primitives_must_match_the_declared_question_type() -> None:
    outcome = check_answer_primitives(
        expected={
            "warrants_investigation": JevPrimitive.NOUL,
            "flow_type": JevPrimitive.CHOICE,
        },
        returned={"warrants_investigation": "choice", "flow_type": "choice"},
    )
    assert not outcome.accepted
    assert AcceptanceRejection.WRONG_PRIMITIVE in outcome.rejections


def test_a_missing_answer_is_rejected_not_ignored() -> None:
    outcome = check_answer_primitives(
        expected={"flow_type": JevPrimitive.CHOICE, "impact": JevPrimitive.SCORE},
        returned={"flow_type": "choice"},
    )
    assert not outcome.accepted
    assert AcceptanceRejection.MISSING_ANSWER in outcome.rejections


def test_extra_answers_do_not_break_acceptance() -> None:
    outcome = check_answer_primitives(
        expected={"flow_type": JevPrimitive.CHOICE},
        returned={"flow_type": "choice", "some_speculative_question": "noul"},
    )
    assert outcome.accepted


def test_selected_references_must_come_from_the_supplied_set() -> None:
    supplied = ["msg_1", "msg_2"]
    assert check_selected_references(
        question_id="evidence", selected=["msg_2"], supplied=supplied
    ).accepted
    invented = check_selected_references(
        question_id="evidence", selected=["msg_3"], supplied=supplied
    )
    assert not invented.accepted
    assert AcceptanceRejection.UNSUPPLIED_REFERENCE in invented.rejections


def test_selecting_no_reference_is_a_legitimate_answer() -> None:
    assert check_selected_references(
        question_id="evidence", selected=[], supplied=["msg_1"]
    ).accepted


def test_a_threshold_cannot_be_transferred_to_another_question() -> None:
    """Thresholds are pinned with their question; reuse is a rejection, not a convenience."""
    noul = check_noul(question_id="is_duplicate", noul=0.95, threshold=_WARRANTS_NOUL)
    assert not noul.accepted
    assert AcceptanceRejection.THRESHOLD_NOT_CALIBRATED in noul.rejections

    choice = check_choice(
        question_id="sensitivity",
        choice="bugfix",
        confidence=0.99,
        supplied_options=["bugfix", "feature_request", "unclear"],
        threshold=_FLOW_CHOICE,
    )
    assert not choice.accepted
    assert AcceptanceRejection.THRESHOLD_NOT_CALIBRATED in choice.rejections

    score = check_score(
        question_id="urgency", score=2.0, confidence=0.9, threshold=_IMPACT_SCORE
    )
    assert not score.accepted
    assert AcceptanceRejection.THRESHOLD_NOT_CALIBRATED in score.rejections


def test_noul_bands_separate_yes_no_and_unresolved() -> None:
    assert check_noul(
        question_id="warrants_investigation", noul=0.91, threshold=_WARRANTS_NOUL
    ).accepted

    negative = check_noul(
        question_id="warrants_investigation", noul=0.05, threshold=_WARRANTS_NOUL
    )
    assert AcceptanceRejection.BELOW_THRESHOLD in negative.rejections

    middle = check_noul(
        question_id="warrants_investigation", noul=0.5, threshold=_WARRANTS_NOUL
    )
    assert AcceptanceRejection.UNRESOLVED_DISTRIBUTION in middle.rejections
    assert AcceptanceRejection.BELOW_THRESHOLD not in middle.rejections


def test_a_no_match_choice_is_unresolved_rather_than_a_weak_answer() -> None:
    outcome = check_choice(
        question_id="flow_type",
        choice="unclear",
        confidence=0.99,
        supplied_options=["bugfix", "feature_request", "unclear"],
        threshold=_FLOW_CHOICE,
    )
    assert not outcome.accepted
    assert outcome.rejections == (AcceptanceRejection.NO_MATCH_SELECTED,)


def test_a_choice_outside_the_supplied_options_is_rejected() -> None:
    outcome = check_choice(
        question_id="flow_type",
        choice="support_question",
        confidence=0.99,
        supplied_options=["bugfix", "feature_request", "unclear"],
        threshold=_FLOW_CHOICE,
    )
    assert AcceptanceRejection.UNSUPPLIED_REFERENCE in outcome.rejections


def test_low_choice_confidence_is_unresolved() -> None:
    outcome = check_choice(
        question_id="flow_type",
        choice="bugfix",
        confidence=0.4,
        supplied_options=["bugfix", "feature_request", "unclear"],
        threshold=_FLOW_CHOICE,
    )
    assert AcceptanceRejection.UNRESOLVED_DISTRIBUTION in outcome.rejections


def test_choice_accepts_a_confident_supplied_option() -> None:
    outcome = check_choice(
        question_id="flow_type",
        choice="feature_request",
        confidence=0.88,
        supplied_options=["bugfix", "feature_request", "unclear"],
        threshold=_FLOW_CHOICE,
    )
    assert outcome.accepted


def test_score_is_only_a_threshold_comparison() -> None:
    assert check_score(
        question_id="impact", score=1.4, confidence=0.9, threshold=_IMPACT_SCORE
    ).accepted
    below = check_score(
        question_id="impact", score=0.4, confidence=0.9, threshold=_IMPACT_SCORE
    )
    assert AcceptanceRejection.BELOW_THRESHOLD in below.rejections
    unsure = check_score(
        question_id="impact", score=1.9, confidence=0.2, threshold=_IMPACT_SCORE
    )
    assert AcceptanceRejection.UNRESOLVED_DISTRIBUTION in unsure.rejections


def test_a_choice_without_a_no_match_option_is_rejected_before_sending() -> None:
    missing = require_no_match_option(
        question_id="matching_signal",
        declared_options=["signal_a", "signal_b"],
        no_match_options=["no_match"],
    )
    assert not missing.accepted
    assert AcceptanceRejection.MISSING_NO_MATCH_OPTION in missing.rejections

    present = require_no_match_option(
        question_id="matching_signal",
        declared_options=["signal_a", "signal_b", "no_match"],
        no_match_options=["no_match"],
    )
    assert present.accepted


def test_accept_all_is_a_conjunction_over_decisions() -> None:
    good = check_noul(
        question_id="warrants_investigation", noul=0.92, threshold=_WARRANTS_NOUL
    )
    bad = check_choice(
        question_id="flow_type",
        choice="unclear",
        confidence=0.99,
        supplied_options=["bugfix", "feature_request", "unclear"],
        threshold=_FLOW_CHOICE,
    )
    assert accept_all([good]).accepted
    combined = accept_all([good, bad])
    assert not combined.accepted
    assert AcceptanceRejection.NO_MATCH_SELECTED in combined.rejections


def test_accept_all_rejects_an_empty_set_rather_than_passing_vacuously() -> None:
    outcome = accept_all([])
    assert not outcome.accepted
    assert AcceptanceRejection.MISSING_ANSWER in outcome.rejections
