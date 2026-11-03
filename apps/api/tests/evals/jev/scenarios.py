"""Scripted Saved Conversion Views moments, each labeled with why.

The recorded session holds one awaited moment among a dozen walkthrough steps, so
these add the moments a recall measure needs: a page the participant said they
would open, a step the agent asked for, a result they were waiting on, and the
steps, asides, and idle clicks around them. Activity lines are written the way the
activity feed writes them (`activity_feed._describe`): a milestone heard before is
said by the values that changed.

The checks moments replay the demo's 2026-10-03 validation pass
(`tests/interview/saved_view_validation.json`) at each point a new milestone
arrives, and edited copies in which the reopen restores some, none, or frozen
dates, or skips the refresh, against three phrasings of the agreed checks.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

__all__ = ["acknowledgement_scenarios", "check_scenarios", "classification_scenarios"]

AckLabel = Literal["acknowledge", "stay_quiet"]
CheckLabel = Literal["shown", "not_shown", "contradicted"]

_PASS = json.loads(
    (Path(__file__).parents[2] / "interview" / "saved_view_validation.json").read_text()
)
_GREETING: Final = (
    "Hi, I'm Wren, a product agent helping the Shopalytics team. I'll ask a few short "
    "questions while you show me your workflow; I can see what you do in this preview and "
    "may point things out. What would you like help with?"
)
_LOADED: Final = (
    "the page loaded",
    ("conversion filters applied (date range last_30_days, channels all, segments all, "
    "review topics all, conversion states Purchased, products all, sentiments all)"),
    "conversion render completed (surface conversion_kpis, status rendered, row count 1)",
)
_ASKED_TO_SAVE: Final = (
    "The new version has a saved view name field and a Save view button at the top. Could "
    "you set up your filter set and save it under a name you choose?"
)
_RESTORED: Final = (
    "conversion filters applied (date range last_30_days, channels TikTok, segments Women "
    "25-34, review topics Sizing, conversion states Purchased, products all, sentiments "
    "negative)"
)
_EARLIER_VALIDATION: Final = (
    f"Agent: {_GREETING} Participant: Every Monday I rebuild the same conversion filters "
    "and I'd love to save them. Agent: So you'd like to be able to save TikTok for the "
    "channel, Women 25-34 for the segment, Sizing for the review topic and Negative for the "
    "sentiment, and reopen them later. Does that sound right? Participant: Yes. Agent: Great, "
    "that's agreed; I'll get that started and tell you when it's ready to try. Agent: The "
    "new version is loading in your preview now."
)


@dataclass(frozen=True, slots=True)
class AckScenario:
    id: str
    earlier: str
    participant_last_said: str
    agent_last_said: str
    just_did: tuple[str, ...]
    label: AckLabel
    why: str
    ambiguous: bool = False


def acknowledgement_scenarios() -> list[AckScenario]:
    """Awaited moments and the steps, asides, and idle clicks around them."""
    walk_earlier = f"Agent: {_GREETING}"
    walk_said = "So every Monday I rebuild the same conversion filters, let me show you."
    return [
        AckScenario(
            "scripted-ack-dashboard-announced",
            f"Agent: {_GREETING}",
            "Hi! So every Monday I rebuild the same conversion filters, which is a pain. Let "
            "me pull up my conversion dashboard real quick.",
            "Sure, take your time.",
            _LOADED,
            "acknowledge",
            "the screen they said they would open came up",
        ),
        AckScenario(
            "scripted-ack-dashboard-waiting",
            f"Agent: {_GREETING} Participant: I want to save my Monday filters. Agent: Got it. "
            "Could you open the conversion dashboard and show me?",
            "Yeah, one sec, it's still loading on my end.",
            "No rush.",
            _LOADED,
            "acknowledge",
            "the page they were both waiting on appeared",
        ),
        AckScenario(
            "scripted-ack-walk-channel",
            walk_earlier,
            f"{walk_said} For example, TikTok for the channel,",
            "Mm-hmm.",
            (
                'clicked conversion filter channel showing "All"',
                "conversion filters applied (channels: all → TikTok)",
                "conversion render completed (surface review_list, status rendered, row count 92)",
            ),
            "stay_quiet",
            "one filter of several, set while naming each",
        ),
        AckScenario(
            "scripted-ack-walk-segment",
            f"{walk_earlier} Participant: {walk_said} For example, TikTok for the channel, "
            "Agent: Mm-hmm.",
            "then women twenty five to thirty four for the segment,",
            "",
            (
                'clicked conversion filter segment showing "All"',
                "conversion filters applied (segments: all → Women 25-34)",
            ),
            "stay_quiet",
            "the next filter of the same walkthrough",
        ),
        AckScenario(
            "scripted-ack-walk-sentiment",
            f"{walk_earlier} Participant: {walk_said} TikTok for the channel, women 25 to 34 "
            "for the segment, sizing for the review topic, Agent: Mm-hmm.",
            "and negative for the sentiment.",
            "",
            (
                'clicked conversion filter sentiment showing "All"',
                "conversion filters applied (sentiments: all → negative)",
                "conversion render completed (surface product_breakdown, status rendered, row count 4)",
            ),
            "stay_quiet",
            "the last filter of a walkthrough; what it builds to is their ask, not this click",
        ),
        AckScenario(
            "scripted-ack-show-first-step",
            walk_earlier,
            "Let me show you, I set these every Monday.",
            "Go ahead.",
            (
                'clicked conversion filter channel showing "All"',
                "conversion filters applied (channels: all → TikTok)",
            ),
            "stay_quiet",
            "the first step of a sequence they announced, not its end",
            ambiguous=True,
        ),
        AckScenario(
            "scripted-ack-show-refresh-loses",
            f"{walk_earlier} Participant: {walk_said} Agent: Mm-hmm. Participant: That's all "
            "of them. Agent: Mm-hmm.",
            "And now watch, when I refresh, it's all gone.",
            "",
            _LOADED,
            "acknowledge",
            "they showed what they said they were about to show: what a refresh does",
        ),
        AckScenario(
            "scripted-ack-show-blank-chart",
            walk_earlier,
            "Watch what happens to the trend chart when I pick TikTok.",
            "Go ahead.",
            (
                'clicked conversion filter channel showing "All"',
                "conversion filters applied (channels: all → TikTok)",
                "conversion render completed (surface conversion_trend, status empty, row count 0)",
            ),
            "acknowledge",
            "they showed what happens when they do something, as they said they would",
        ),
        AckScenario(
            "scripted-ack-aside",
            f"{walk_earlier} Participant: {walk_said} Agent: Mm-hmm.",
            "Sorry, one second, someone's at the door.",
            "No problem.",
            ("conversion render completed (surface review_list, status rendered, row count 40)",),
            "stay_quiet",
            "a background render during an aside answers nothing anyone was waiting on",
        ),
        AckScenario(
            "scripted-ack-idle-click-discovery",
            f"{walk_earlier} Participant: I rebuild TikTok, women 25 to 34, sizing and negative "
            "every Monday. Agent: What are you looking to learn from that set each week?",
            "Mostly whether the sizing complaints are hurting TikTok conversion.",
            "",
            ('clicked conversion filter product showing "All"',),
            "stay_quiet",
            "an idle click while answering a question; nobody asked for it",
        ),
        AckScenario(
            "scripted-ack-idle-while-building",
            f"{_EARLIER_VALIDATION.split(' Agent: The new version')[0]}",
            "How's it going?",
            "Still building it.",
            (
                'clicked conversion filter date range showing "Last 30 days"',
                "conversion filters applied (date range: last_30_days → last_7_days)",
            ),
            "stay_quiet",
            "exploring the old version while waiting; remarking on it would narrate",
        ),
        AckScenario(
            "scripted-ack-new-version-loaded",
            _EARLIER_VALIDATION.rsplit(" Agent: The new version", 1)[0],
            "Sounds good.",
            "The new version is loading in your preview now.",
            _LOADED,
            "stay_quiet",
            "the new version's load is debriefed by its own cue; an acknowledgement doubles it",
            ambiguous=True,
        ),
        AckScenario(
            "scripted-ack-validate-filter-step",
            _EARLIER_VALIDATION,
            "Sure, let me try.",
            "Mm-hmm.",
            (
                'clicked conversion filter segment showing "All"',
                "conversion filters applied (segments: all → Women 25-34)",
            ),
            "stay_quiet",
            "one filter of several on the way to the save they were asked for",
        ),
        AckScenario(
            "scripted-ack-validate-name-field",
            _EARLIER_VALIDATION,
            "Sure, let me try.",
            "Mm-hmm.",
            ("clicked saved view name field",),
            "stay_quiet",
            "a step toward the save, not the save",
        ),
        AckScenario(
            "scripted-ack-validate-saved",
            f"{_EARLIER_VALIDATION} Agent: {_ASKED_TO_SAVE}",
            "Okay, saving it as weekly sizing.",
            "Mm-hmm.",
            (
                'clicked saved view save control showing "Save view"',
                "saved view created (view name weekly sizing)",
            ),
            "acknowledge",
            "they finished what the agent asked: the save",
        ),
        AckScenario(
            "scripted-ack-validate-saved-silent",
            f"{_EARLIER_VALIDATION} Agent: {_ASKED_TO_SAVE}",
            "Sure.",
            "",
            ("saved view created (view name monday tiktok)",),
            "acknowledge",
            "they finished what the agent asked without a word",
        ),
        AckScenario(
            "scripted-ack-validate-refresh-step",
            f"{_EARLIER_VALIDATION} Agent: {_ASKED_TO_SAVE} Participant: Done. Agent: Great. "
            "Now refresh the page and reopen it from Saved views.",
            "Okay.",
            "",
            _LOADED,
            "stay_quiet",
            "the refresh is the first half of what was asked; the reopen is what they await",
        ),
        AckScenario(
            "scripted-ack-validate-reopened",
            f"{_EARLIER_VALIDATION} Agent: {_ASKED_TO_SAVE} Participant: Done. Agent: Great. "
            "Now refresh the page and reopen it from Saved views. Participant: Okay.",
            "",
            "",
            (
                "the page loaded",
                "clicked saved view reopen control",
                "saved view reopened (view name weekly sizing)",
                _RESTORED,
            ),
            "acknowledge",
            "the reopen they were asked for, with every filter back",
        ),
        AckScenario(
            "scripted-ack-validate-reopened-unprompted",
            f"{_EARLIER_VALIDATION} Agent: {_ASKED_TO_SAVE} Participant: Sure, let me try. "
            "Agent: There it is.",
            "Now let me refresh and see if it comes back.",
            "Mm-hmm.",
            ("saved view reopened (view name weekly sizing)", _RESTORED),
            "acknowledge",
            "the result they said they were checking for appeared",
        ),
        AckScenario(
            "scripted-ack-render-after-ack",
            f"{_EARLIER_VALIDATION} Agent: {_ASKED_TO_SAVE} Participant: Okay.",
            "",
            "There it is.",
            ("conversion render completed (surface product_breakdown, status rendered, row count 4)",),
            "stay_quiet",
            "a render after a moment already acknowledged",
        ),
        AckScenario(
            "scripted-ack-asked-to-open-selector",
            f"{_EARLIER_VALIDATION} Agent: {_ASKED_TO_SAVE} Participant: Done. Agent: Great. "
            "Can you open the Saved views list for me?",
            "Yep.",
            "",
            ('clicked saved view selector showing "Saved views"',),
            "acknowledge",
            "they did exactly what the agent asked",
        ),
    ]


@dataclass(frozen=True, slots=True)
class ChecksScenario:
    id: str
    checks: Mapping[str, str]
    activity: tuple[str, ...]
    labels: Mapping[str, CheckLabel]
    why: str
    ambiguous: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class _Pass:
    """One validation pass and what it showed, for labeling each phrasing of the checks."""

    name: str
    activity: tuple[str, ...]
    saved: bool
    reloaded_after_save: bool
    reopened_after_reload: bool
    #: How the reopen restored the filters: all of them, some, none, or with frozen dates.
    restore: Literal["none_yet", "all", "some", "nothing", "frozen_dates"]


def _passes() -> list[_Pass]:
    before = tuple(_PASS["before_reopen"])
    reopened = tuple(_PASS["reopened"])
    restored = reopened[2]
    some = restored.replace("segments Women 25-34", "segments all").replace(
        "products Aero High-Rise Legging", "products all"
    )
    nothing = before[0]
    frozen = restored.replace("date range last_30_days", "date range 2026-09-02 to 2026-10-01")
    without_reload = before[:50]
    return [
        _Pass("first-render", before[:6], False, False, False, "none_yet"),
        _Pass("filters-set", before[:47], False, False, False, "none_yet"),
        _Pass("save-clicked", before[:49], False, False, False, "none_yet"),
        _Pass("saved", before[:50], True, False, False, "none_yet"),
        _Pass("reloaded", before, True, True, False, "none_yet"),
        _Pass("reopened", before + reopened, True, True, True, "all"),
        _Pass("reopened-some", before + (*reopened[:2], some, *reopened[3:]), True, True, True, "some"),
        _Pass("reopened-nothing", before + (*reopened[:2], nothing), True, True, True, "nothing"),
        _Pass("reopened-frozen", before + (*reopened[:2], frozen, *reopened[3:]), True, True, True, "frozen_dates"),
        _Pass("reopened-without-reload", without_reload + reopened, True, False, False, "all"),
    ]


def _restore_label(p: _Pass) -> CheckLabel:
    if not p.reopened_after_reload:
        return "not_shown"
    return "shown" if p.restore == "all" else "contradicted"


_PHRASINGS: Final[Mapping[str, Mapping[str, tuple[str, Callable[[_Pass], CheckLabel]]]]] = {
    # The two checks the committed live attention test uses.
    "test": {
        "save": (
            "The person can save the complete current filter set under a name.",
            lambda p: "shown" if p.saved else "not_shown",
        ),
        "reopen": (
            ("After refreshing, the person can reopen the named saved view and restore all "
            "saved filter selections."),
            _restore_label,
        ),
    },
    # The checks the 2026-11-01 recorded run agreed, the placement check aside.
    "recorded": {
        "save": (
            "Save the current complete filter stack as a view under a name the person chooses.",
            lambda p: "shown" if p.saved else "not_shown",
        ),
        "reopen": (
            "After refreshing, reopen the saved view with one click and restore all saved filters.",
            _restore_label,
        ),
        "relative": (
            ("Reopening a saved view preserves Last 30 days as a relative date range rather "
            "than frozen dates."),
            lambda p: (
                "not_shown"
                if not (p.saved and p.restore != "none_yet")
                else "contradicted"
                if p.restore == "frozen_dates"
                else "shown"
                if p.restore in ("all", "some")
                else "not_shown"
            ),
        ),
    },
    # The scenario's acceptance points.
    "scenario": {
        "save": (
            "The complete filter stack can be saved under a name the person supplies.",
            lambda p: "shown" if p.saved else "not_shown",
        ),
        "persist": (
            "The saved view is still available after leaving or refreshing the dashboard.",
            lambda p: "shown" if p.reopened_after_reload else "not_shown",
        ),
        "restore": (
            ("Reopening the view restores every saved filter and the conversion display that "
            "goes with them."),
            lambda p: (
                "not_shown"
                if p.restore == "none_yet"
                else "shown"
                if p.restore == "all"
                else "contradicted"
            ),
        ),
    },
}

#: Labels a careful reader could set either way, reported separately: phrasing, check, pass.
_AMBIGUOUS: Final[frozenset[tuple[str, str, str]]] = frozenset(
    {
        # The reopen restored the relative token, which is what the check is about, but
        # "rather than frozen dates" is only shown by the absence of dates.
        ("recorded", "relative", "reopened"),
        # A reopen without a refresh still shows the view persisted on the page.
        ("scenario", "persist", "reopened-without-reload"),
        # The reopen restored nothing, yet the range it left is the relative default.
        # Every judge, the rules included, called this shown on the first replay.
        ("recorded", "relative", "reopened-nothing"),
    }
)


def check_scenarios() -> list[ChecksScenario]:
    """Each validation pass judged against each phrasing of the agreed checks."""
    scenarios: list[ChecksScenario] = []
    for p in _passes():
        for phrasing, checks in _PHRASINGS.items():
            labels = {check: label(p) for check, (_, label) in checks.items()}
            ambiguous = frozenset(
                check
                for which, check, pass_name in _AMBIGUOUS
                if which == phrasing and pass_name == p.name and check in checks
            )
            scenarios.append(
                ChecksScenario(
                    id=f"scripted-checks-{p.name}-{phrasing}",
                    checks={check: statement for check, (statement, _) in checks.items()},
                    activity=p.activity,
                    labels=labels,
                    why=f"the 2026-10-03 pass, {p.name}",
                    ambiguous=ambiguous,
                )
            )
    return scenarios


@dataclass(frozen=True, slots=True)
class ClassificationScenario:
    id: str
    #: Turns of the conversation that replace the recorded one.
    turns: Sequence[tuple[str, str]]
    label: Literal["feature_request_flow", "bugfix_flow", "unclear"]
    why: str


def classification_scenarios() -> list[ClassificationScenario]:
    """Interviews whose account points at a product failure, a feature, or nothing yet."""
    return [
        ClassificationScenario(
            "scripted-classification-reopen-broken",
            (
                ("agent", _GREETING),
                (
                    "participant",
                    ("We already have saved views, but when I reopen one the filters all reset "
                    "to All. It worked last month. It's broken."),
                ),
                ("agent", "So reopening a saved view drops every filter you saved. Is that right?"),
                ("participant", "Yes, every time since the last update."),
            ),
            "bugfix_flow",
            "an existing capability stopped working",
        ),
        ClassificationScenario(
            "scripted-classification-chart-error",
            (
                ("agent", _GREETING),
                (
                    "participant",
                    ("When I pick TikTok as the channel the conversion trend chart just shows an "
                    "error instead of the line. Let me show you."),
                ),
                ("agent", "Go ahead."),
                ("participant", "See? Error. It should just show the TikTok trend."),
            ),
            "bugfix_flow",
            "the product fails at something it is meant to do",
        ),
        ClassificationScenario(
            "scripted-classification-export",
            (
                ("agent", _GREETING),
                (
                    "participant",
                    ("I'd love a way to export the conversion table to CSV so I can send it to "
                    "my manager every Friday."),
                ),
                ("agent", "What do you do today instead?"),
                ("participant", "I copy it into a spreadsheet by hand."),
            ),
            "feature_request_flow",
            "a new capability for a manual workaround",
        ),
        ClassificationScenario(
            "scripted-classification-nothing-yet",
            (("agent", _GREETING), ("participant", "Hi, can you hear me?")),
            "unclear",
            "nothing said yet supports either flow",
        ),
    ]
