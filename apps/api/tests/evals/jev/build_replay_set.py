"""Write `replay_set.json`: the recorded run's moments, labeled, and the scripted ones.

    uv run python -m tests.evals.jev.build_replay_set --export session-2026-11-01.json

The export comes from `export_session`. Each recorded moment keeps Jev's answer at
the time, so a replay can say how faithfully it was rebuilt. The labels below say
what a careful observer would want: whether the agent should have said a few words,
and which agreed checks the activity showed. A label a careful reader could set
either way is marked ambiguous, and the replay reports results with and without it.
"""

import argparse
import dataclasses
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from tests.evals.jev.recorded import (
    RecordedAnswer,
    acknowledgement_moments,
    check_moments,
    classification_moments,
    described_between,
    last_conversation,
)
from tests.evals.jev.scenarios import (
    acknowledgement_scenarios,
    check_scenarios,
    classification_scenarios,
)

REPLAY_SET: Final = Path(__file__).with_name("replay_set.json")
_SOURCE: Final = "recorded run 2026-11-01 (Product Signal 01a19627-8d6d-70cc-b1ca-165f98e70e89)"

_STEP = "a step of rebuilding the filters on the way to the save the agent asked for"
#: The recorded acknowledgement moments, in order: label, why, ambiguous.
_ACK_LABELS: Final[tuple[tuple[str, str, bool], ...]] = (
    ("stay_quiet", "the new version's load, which its own debrief cue speaks for", True),
    ("stay_quiet", _STEP, False),
    ("stay_quiet", _STEP, False),
    ("stay_quiet", "a render during the rebuild", False),
    ("stay_quiet", _STEP, False),
    ("stay_quiet", "a render during the rebuild", False),
    ("stay_quiet", _STEP, False),
    ("stay_quiet", _STEP, False),
    ("stay_quiet", "a render during the rebuild", False),
    ("skip", "no activity can be attributed to this moment", False),
    ("stay_quiet", "clicking the name field, a step toward the save", False),
    ("skip", "no activity can be attributed to this moment", False),
    ("stay_quiet", "the click on Save, a step before the save itself", False),
    ("acknowledge", "the save the agent asked for", False),
)

#: The recorded run's agreed checks: save, refresh-and-reopen, relative range, placement.
_SAVE = {"01a19629-08c0-741c-a6c8-c7c7bbfec96a", "01a1962a-a926-7a23-8d82-293ed6d57488"}
_REOPEN = {"01a19629-08c3-798f-a4d0-f126955a468c", "01a1962a-a928-7517-a8a3-a64100b9042b"}
_RELATIVE = {"01a19629-08c5-75f9-9cf8-dc666f2fff6c", "01a1962a-a92a-776d-b47c-2595bae39407"}
_PLACEMENT = {"01a1962a-a92c-75c2-a31e-5496decb4e12"}


def _check_labels(moment: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Label each recorded check from what the activity holds; placement is never visible."""
    activity = moment["activity"]
    saved = any(line.startswith("saved view created") for line in activity)
    saved_at = next((i for i, line in enumerate(activity) if line.startswith("saved view created")), -1)
    reopened = saved and any(
        line.startswith("saved view reopened") and i > saved_at
        and "the page loaded" in activity[saved_at:i]
        for i, line in enumerate(activity)
    )
    labels: dict[str, str] = {}
    ambiguous: list[str] = []
    for check in moment["checks"]:
        if check in _SAVE:
            labels[check] = "shown" if saved else "not_shown"
        elif check in _REOPEN:
            # Every reopen in this run restored the saved filters in full.
            labels[check] = "shown" if reopened else "not_shown"
        elif check in _RELATIVE:
            labels[check] = "shown" if reopened else "not_shown"
            if reopened:
                ambiguous.append(check)
        elif check in _PLACEMENT:
            labels[check] = "not_shown"
        else:
            raise ValueError(f"unlabeled check {check}")
    return labels, ambiguous


def _recorded(answer: RecordedAnswer) -> dict[str, Any]:
    return dataclasses.asdict(answer)


def build(export: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    acknowledgements: list[dict[str, Any]] = []
    recorded_acks = acknowledgement_moments(export)
    if len(recorded_acks) != len(_ACK_LABELS):
        raise ValueError("the export's acknowledgement moments do not match their labels")
    for index, ((moment, answer), (label, why, ambiguous)) in enumerate(
        zip(recorded_acks, _ACK_LABELS, strict=True)
    ):
        if label == "skip":
            continue
        acknowledgements.append(
            {
                "id": f"recorded-ack-{index:02d}",
                "source": _SOURCE,
                **moment,
                "label": label,
                "why": why,
                "ambiguous": ambiguous,
                "recorded": _recorded(answer),
            }
        )
    # Two moments the run never judged, because an acknowledgement had just been
    # spoken: the refresh after the save, and the reopen that restored everything.
    saved_at = datetime.fromisoformat(recorded_acks[-1][1].at)
    reload_until = datetime.fromisoformat("2026-11-01T08:14:07+00:00")
    reopen_until = datetime.fromisoformat("2026-11-01T08:14:12+00:00")
    conversation = last_conversation(export, before=reopen_until)
    if not conversation["agent_last_said"].endswith("There it is."):
        raise ValueError("the newest bundle should hold the spoken acknowledgement")
    for name, since, until, label, why, ambiguous in (
        ("reload", saved_at, reload_until, "stay_quiet", "the refresh, unannounced, before the reopen", False),
        (
            "reopen",
            reload_until,
            reopen_until,
            "acknowledge",
            "the reopen the agreed checks named, with every filter back; unannounced",
            True,
        ),
    ):
        acknowledgements.append(
            {
                "id": f"recorded-ack-unjudged-{name}",
                "source": _SOURCE,
                **conversation,
                "just_did": described_between(export, since=since, until=until),
                "label": label,
                "why": why,
                "ambiguous": ambiguous,
                "recorded": None,
            }
        )
    for scenario in acknowledgement_scenarios():
        acknowledgements.append(
            {
                "id": scenario.id,
                "source": "scripted",
                "earlier": scenario.earlier,
                "participant_last_said": scenario.participant_last_said,
                "agent_last_said": scenario.agent_last_said,
                "just_did": list(scenario.just_did),
                "label": scenario.label,
                "why": scenario.why,
                "ambiguous": scenario.ambiguous,
                "recorded": None,
            }
        )

    checks: list[dict[str, Any]] = []
    for index, (moment, answer) in enumerate(check_moments(export)):
        labels, ambiguous_checks = _check_labels(moment)
        checks.append(
            {
                "id": f"recorded-checks-{index:02d}",
                "source": _SOURCE,
                **moment,
                "labels": labels,
                "ambiguous": ambiguous_checks,
                "why": "the recorded validation activity at this judgement",
                "recorded": _recorded(answer),
            }
        )
    for check_scenario in check_scenarios():
        checks.append(
            {
                "id": check_scenario.id,
                "source": "scripted",
                "checks": dict(check_scenario.checks),
                "activity": list(check_scenario.activity),
                "labels": dict(check_scenario.labels),
                "ambiguous": sorted(check_scenario.ambiguous),
                "why": check_scenario.why,
                "recorded": None,
            }
        )

    classifications: list[dict[str, Any]] = []
    recorded_classifications = classification_moments(export)
    for index, (moment, answer) in enumerate(recorded_classifications):
        classifications.append(
            {
                "id": f"recorded-classification-{index:02d}",
                "source": _SOURCE,
                **moment,
                "label": "feature_request_flow",
                "why": "Saved Conversion Views is a new capability",
                "recorded": _recorded(answer),
            }
        )
    # Each scripted interview keeps the recorded bundle's lifecycle and policy and
    # replaces what the participant said; it has no agreement yet.
    lifecycle = [
        item
        for item in recorded_classifications[0][0]["context"]
        if item["section"] in ("lifecycle_and_policy", "prior_commands_and_results")
    ]
    for classification in classification_scenarios():
        turns = [
            {
                "section": "recent_conversation",
                "source_kind": "voice_transcript_turn",
                "source_id": f"{classification.id}-{index}",
                "source_revision": "1",
                "body": {"speaker": speaker, "text": text},
            }
            for index, (speaker, text) in enumerate(classification.turns)
        ]
        classifications.append(
            {
                "id": classification.id,
                "source": "scripted",
                "context": [*turns, *lifecycle],
                "label": classification.label,
                "why": classification.why,
                "recorded": None,
            }
        )
    return {
        "acknowledgements": acknowledgements,
        "checks": checks,
        "classifications": classifications,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--export", type=Path, required=True)
    args = parser.parse_args()
    replay_set = build(json.loads(args.export.read_text(encoding="utf-8")))
    REPLAY_SET.write_text(json.dumps(replay_set, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print({name: len(items) for name, items in replay_set.items()})


if __name__ == "__main__":
    main()
