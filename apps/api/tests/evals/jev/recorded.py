"""Rebuild every moment Jev judged in an exported session (`export_session`).

Each moment carries the state Jev was given, rebuilt with the production code that
built it (`conversation_state`, `bundle_state`, the activity feed's descriptions),
and the answer Jev returned at the time. The conversation and the classification's
context come verbatim from the recorded context bundle. An acknowledgement's
activity is rebuilt: the session records when each action was committed and when
each bundle was assembled, not the instant the feed read the activity, so a moment
is what was committed between one acknowledgement's bundle and the next. Replaying
Jev on the rebuilt state and comparing with its recorded answers measures how close
the rebuild is.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.context.sources import SECTION_RECENT_CONVERSATION
from app.interview.judgements import bundle_state, conversation_state
from app.interview.observations import ObservedActivity, _activity, identifier_words
from app.interview.voice.activity_feed import _describe
from tests.evals.jev.measured import activity_lines

__all__ = [
    "RecordedAnswer",
    "acknowledgement_moments",
    "check_moments",
    "classification_moments",
]

_ACKNOWLEDGEMENT = "activity::acknowledge"
_CHECK_SHOWN = "check_shown::"
_CLASSIFICATION = "classification::established_flow"
#: How far back the first acknowledgement's moment reaches when no earlier one bounds it.
_FIRST_MOMENT_SECONDS = 6.0


@dataclass(frozen=True, slots=True)
class RecordedAnswer:
    """What Jev answered at the time, per question, and whether it was accepted."""

    choices: Mapping[str, str]
    confidences: Mapping[str, float]
    accepted: bool
    at: str


class _Item:
    """A recorded bundle item, shaped as the production renderers read it."""

    def __init__(self, row: Mapping[str, Any]) -> None:
        self.section = row["section"]
        self.source_kind = row["source_kind"]
        self.source_id = row["source_id"]
        self.source_revision = row["source_revision"]
        self.body = row["body"]


class _Bundle:
    """A recorded context bundle, shaped as `conversation_state` and `bundle_state` read it."""

    def __init__(self, row: Mapping[str, Any]) -> None:
        self.bundle_id = row["id"]
        self.context_revision = row["context_revision"]
        self.digest = row["digest"]
        self.cutoff_at = datetime.fromisoformat(row["cutoff_at"])
        self.included = tuple(_Item(item) for item in row["items"] if item["included"])
        self.omitted: tuple[object, ...] = ()

    def section(self, section: str) -> tuple[_Item, ...]:
        return tuple(item for item in self.included if item.section == section)


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _control_names(export: Mapping[str, Any]) -> dict[str, str]:
    """Each clicked control as `describe_activity` names it, by target id."""
    names: dict[str, str] = {}
    for row in export["clicked_controls"]:
        target = row["target"]
        label = (target.get("label") or {}).get("value")
        definition = target.get("definition_id")
        if definition and label:
            described = f'{definition} showing "{label}"'
        else:
            described = definition or label or "unlabeled"
        names[row["target_id"]] = identifier_words(described)
    return names


def _activity_rows(export: Mapping[str, Any]) -> list[tuple[datetime, ObservedActivity]]:
    """The person's activity in commit order, with when each item was committed."""
    controls = _control_names(export)
    rows = sorted(export["activity"], key=lambda row: (row["accepted_at"], row["observed_at"]))
    return [(_time(row["accepted_at"]), _activity(row, controls=controls)) for row in rows]


def _answer(judgement: Mapping[str, Any]) -> RecordedAnswer:
    return RecordedAnswer(
        choices={name: answer["choice"] for name, answer in judgement["answers"].items()},
        confidences={name: answer["confidence"] for name, answer in judgement["answers"].items()},
        accepted=judgement["accepted"],
        at=judgement["created_at"],
    )


def _bundles(export: Mapping[str, Any]) -> dict[str, _Bundle]:
    return {row["id"]: _Bundle(row) for row in export["bundles"]}


def _conversation(bundle: _Bundle) -> dict[str, Any]:
    """The conversation as Jev received it, without the bundle's identifiers."""
    state = conversation_state(bundle)  # type: ignore[arg-type]
    return {
        name: state[name] for name in ("earlier", "participant_last_said", "agent_last_said")
    }


def acknowledgement_moments(
    export: Mapping[str, Any],
) -> list[tuple[dict[str, Any], RecordedAnswer]]:
    """Each acknowledgement Jev judged: the conversation, what was just done, and its answer."""
    bundles = _bundles(export)
    activity = _activity_rows(export)
    judged = [
        judgement
        for judgement in export["judgements"]
        if _ACKNOWLEDGEMENT in judgement["question_ids"]
    ]
    moments: list[tuple[dict[str, Any], RecordedAnswer]] = []
    previous: datetime | None = None
    for judgement in judged:
        bundle = bundles[judgement["context_bundle_id"]]
        read_at = bundle.cutoff_at
        since = previous or datetime.fromtimestamp(
            read_at.timestamp() - _FIRST_MOMENT_SECONDS, tz=read_at.tzinfo
        )
        previous = read_at
        lines = _described_between(activity, since=since, until=read_at)
        moments.append(({**_conversation(bundle), "just_did": lines}, _answer(judgement)))
    return moments


def described_between(export: Mapping[str, Any], *, since: datetime, until: datetime) -> list[str]:
    """What the activity feed would say about activity committed in (since, until]."""
    return _described_between(_activity_rows(export), since=since, until=until)


def last_conversation(export: Mapping[str, Any], *, before: datetime) -> dict[str, Any]:
    """The conversation as the newest acknowledgement bundle before a moment held it."""
    bundles = [
        bundle
        for bundle in _bundles(export).values()
        if bundle.cutoff_at <= before and bundle.section(SECTION_RECENT_CONVERSATION)
    ]
    return _conversation(max(bundles, key=lambda bundle: bundle.cutoff_at))


def _described_between(
    activity: Sequence[tuple[datetime, ObservedActivity]], *, since: datetime, until: datetime
) -> list[str]:
    # Everything committed before `since` has already been reported to the agent, so a
    # milestone heard then is said by what changed.
    _, reported = _describe([item for at, item in activity if at <= since], reported={})
    lines, _ = _describe([item for at, item in activity if since < at <= until], reported=reported)
    return lines


def check_moments(
    export: Mapping[str, Any],
) -> list[tuple[dict[str, Any], RecordedAnswer]]:
    """Each checks-shown judgement: the checks still pending, the activity, and its answer."""
    bundles = _bundles(export)
    activity = _activity_rows(export)
    criteria = {str(row["id"]): row["statement"] for row in export["criteria"]}
    attempts = [_time(row["created_at"]) for row in export["validation_attempts"]]
    moments: list[tuple[dict[str, Any], RecordedAnswer]] = []
    for judgement in export["judgements"]:
        checks = [
            question.removeprefix(_CHECK_SHOWN)
            for question in judgement["question_ids"]
            if question.startswith(_CHECK_SHOWN)
        ]
        if not checks:
            continue
        cutoff = bundles[judgement["context_bundle_id"]].cutoff_at
        opened = max(at for at in attempts if at <= cutoff)
        since_opened = [item for at, item in activity if opened <= at <= cutoff]
        moments.append(
            (
                {
                    "checks": {check: criteria[check] for check in checks},
                    "activity": activity_lines(since_opened),
                },
                _answer(judgement),
            )
        )
    return moments


def classification_moments(
    export: Mapping[str, Any],
) -> list[tuple[dict[str, Any], RecordedAnswer]]:
    """Each flow classification: the bundle's context as Jev received it, and its answer."""
    bundles = _bundles(export)
    moments: list[tuple[dict[str, Any], RecordedAnswer]] = []
    for judgement in export["judgements"]:
        if _CLASSIFICATION not in judgement["question_ids"]:
            continue
        state = bundle_state(bundles[judgement["context_bundle_id"]])  # type: ignore[arg-type]
        moments.append(({"context": state["context"]}, _answer(judgement)))
    return moments


def conversation_turns(export: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The whole recorded conversation as turns, for reading alongside the moments."""
    turns: list[tuple[str, str]] = []
    for item in max(
        (_Bundle(row) for row in export["bundles"]), key=lambda bundle: bundle.cutoff_at
    ).section(SECTION_RECENT_CONVERSATION):
        speaker, text = str(item.body["speaker"]), str(item.body["text"])
        if turns and turns[-1][0] == speaker:
            turns[-1] = (speaker, turns[-1][1] + text)
        else:
            turns.append((speaker, text))
    return turns


def lines_of(moment: Sequence[str]) -> str:
    return "; ".join(moment)
