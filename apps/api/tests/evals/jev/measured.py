"""The checks-shown judgment as it was measured, kept so the evaluation still runs.

Production stopped asking Jev which agreed checks a new version showed after this
evaluation: the voice agent judges that from the activity it is told about. These
are verbatim copies of what was removed (`app.interview.attention` and
`app.interview.voice.checks_shown` as they stood before the removal), and the
instructions' and debrief cue's wording from before, so the replay and the
counterfactual measure what they measured then.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from typesafe_sdk import Choice, Question

from app.interview.observations import ObservedActivity, describe_activity

__all__ = [
    "CHECK_SHOWN_CONFIDENCE",
    "CHECK_SHOWN_OPTIONS",
    "activity_lines",
    "checks_question",
    "checks_report",
    "reported_by_the_application",
]

CHECK_SHOWN_OPTIONS: Final[tuple[str, ...]] = ("shown", "not_shown", "contradicted", "unclear")
CHECK_SHOWN_CONFIDENCE: Final = 0.75
_CHECK_SHOWN: Final = "check_shown::"
_SHOWN: Final = "shown"
_CONTRADICTED: Final = "contradicted"
_NO_MATCH: Final = "unclear"


def _checks_shown_questions(checks: Mapping[str, str]) -> dict[str, Question]:
    """One question per agreed check, keyed `check_shown::<criterion id>`."""
    return {
        f"{_CHECK_SHOWN}{check_id}": Choice(
            instructions=(
                "`activity_in_new_version` lists, oldest first and in the product's own "
                "terms, what the customer did in the new version of the product and what "
                "it did in response. Follow it step by step. Did the customer do what "
                f"this agreed check describes, and did the product respond as it says: "
                f"{statement}"
            ),
            criteria={
                _SHOWN: "Yes: the customer did it and the product responded as the check says.",
                "not_shown": "The customer has not done this yet.",
                _CONTRADICTED: (
                    "The customer did it, but the product responded differently from what "
                    "the check says."
                ),
                _NO_MATCH: "The activity does not settle which of the above holds.",
            },
        )
        for check_id, statement in checks.items()
    }


def checks_question(
    moment: Mapping[str, Any], provenance: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Question]]:
    """The state and questions `judge_checks_shown` sent Jev for one moment."""
    return (
        {**provenance, "activity_in_new_version": list(moment["activity"])},
        _checks_shown_questions(moment["checks"]),
    )


def activity_lines(activity: Sequence[ObservedActivity]) -> list[str]:
    """The activity as product-term lines, a line repeated back to back kept once."""
    lines: list[str] = []
    for item in activity:
        line = describe_activity(item)
        if not lines or lines[-1] != line:
            lines.append(line)
    return lines


def checks_report(*, shown: Sequence[str], contradicted: Sequence[str], remaining: Sequence[str]) -> str:
    """What the agent was told about the agreed checks after the activity that showed them."""
    parts: list[str] = []
    if shown:
        parts.append(
            "With that, the new version showed these agreed checks working: "
            f"{'; '.join(shown)}. Never ask them to do these again."
        )
    if contradicted:
        parts.append(
            "The product did something different from these agreed checks: "
            f"{'; '.join(contradicted)}. Once they say how it went, ask them what they saw."
        )
    if remaining:
        parts.append(
            f"Still to try: {'; '.join(remaining)}. Let them carry on, with no questions "
            "and no instructions you already gave, until they say how it went."
        )
    else:
        parts.append(
            "Every agreed check has now been tried. Once they say how it went, take "
            "their words as their answers, and record them."
        )
    return " ".join(parts)


#: Today's wording, in which the agent judges shown checks, and the wording it replaced.
_JUDGED: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (
        re.compile(
            r"From\s+what the application tells you they did in the new version, judge which"
            r"\s+checks\s+it already showed:"
        ),
        "The application tells you which checks the new version already showed:",
    ),
    (
        re.compile(
            r"judge from what the application\s+tells you they did which checks the new version"
            r"\s+showed;"
        ),
        "the application tells you which checks the new version showed;",
    ),
)


def reported_by_the_application(text: str) -> str:
    """Instructions or a debrief cue as they read while Jev reported shown checks."""
    for pattern, reported in _JUDGED:
        text = pattern.sub(reported, text)
    return text
