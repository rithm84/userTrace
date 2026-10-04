"""Jev judgements that decide what the agent cursor shows.

Two moments call for one. When GPT-Live delegates, Jev judges from the latest
words whether the agent needs a control shown (or only the status of the change),
which supplied target, and whether to circle or point. While the agent speaks, Jev
judges each clause it says: whether it is clarifying or explaining something the
customer can see, which supplied target that is, and how to show it. Targets are
always chosen from what the shared browser runtime registers; Jev invents neither
selectors nor coordinates, and code verifies binding and freshness before a
gesture is dispatched. Nothing here matches words against controls.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from typesafe_sdk import Choice, Question

from app.connectors.jev_acceptance import AcceptanceOutcome, ChoiceThreshold
from app.connectors.typesafe_jev import JevClient
from app.context.bundles import AssembledBundle
from app.context.freshness import check_bundle_freshness
from app.db.pool import Database
from app.interview.judgements import (
    STALE_BUNDLE,
    AttentionResult,
    bundle_state,
    check_batch,
    conversation_state,
    judge,
    record_judgement,
)
from app.logging import get_logger

__all__ = [
    "DELEGATION_NEED_OPTIONS",
    "GESTURE_INTENT_OPTIONS",
    "SPEECH_GESTURE_OPTIONS",
    "DelegationRoute",
    "RecapGesture",
    "SpeechGesture",
    "debrief_gesture_questions",
    "delegation_questions",
    "recap_gesture_questions",
    "route_debrief_gesture",
    "route_delegation",
    "route_recap_gesture",
    "select_visual_target",
]

_log = get_logger(__name__)

GESTURE_INTENT_OPTIONS: tuple[str, ...] = ("show_on_screen", "other")
DELEGATION_NEED_OPTIONS: tuple[str, ...] = ("progress_update", "record", "other")
SPEECH_GESTURE_OPTIONS: tuple[str, ...] = ("show", "none")

_NO_MATCH = "unclear"
_NO_TARGET = "no_target"
_SHOW_ON_SCREEN = "show_on_screen"
_OTHER_NEED = "other"
_GESTURE_INTENT = "gesture::intent"
_DELEGATION_NEED = "delegation::need"
_PROGRESS_UPDATE = "progress_update"
_GESTURE_KIND = "gesture::kind"
_TARGET_SELECTION = "visual_target::selection"
_SPEECH_INTENT = "speech_gesture::intent"
_SHOW = "show"
_NO_GESTURE = "none"
_CUSTOMER_TURN = "speech_gesture::customer_turn"
_STILL_EXPLAINING = "still_explaining"
_RECAP = "speech_gesture::recap"
_RESTATING = "restating"
_OTHER_TALK = "other"

# Confidence floors are calibrated per question type and never transferred.
_TARGET_CONFIDENCE = 0.75

# Calibrated on 60 delegation moments, half held out, each judged twice against
# the Shopalytics conversion page: these floors gave no gesture where none was
# wanted and the most correct gestures, and the same on the held-out half.
_GESTURE_INTENT_CONFIDENCE = 0.8
_GESTURE_TARGET_CONFIDENCE = 0.3
_GESTURE_KIND_CONFIDENCE = 0.5
# Calibrated on 88 delegation moments, each judged twice, including agreements and
# validation answers being recorded and questions about how the product behaved: no
# floor from 0.5 to 0.95 answered any of them as progress, and 0.6 kept every
# progress question, where 0.8 lost "Should I reload the page?".
_PROGRESS_CONFIDENCE = 0.6
# Calibrated on 66 agent clauses over the real Shopalytics base and candidate
# registries (this demo's own clauses, the earlier pointer cases, and 16 held out),
# each judged twice: from 0 to 0.9 no floor showed a wrong control or showed one
# where none was meant; 0.5 kept 18 of 20 of the demo's clauses and all 16 held out.
_SPEECH_INTENT_CONFIDENCE = 0.5
_SPEECH_TARGET_CONFIDENCE = 0.5
# Calibrated on those clauses plus 18 from the 2026-10-03 session (the agent echoing
# each filter while the customer was still listing them, then its recap once they
# had finished), each judged twice: every echo was judged still explaining at 0.93
# or more and no recap above 0.37, so suppressing from 0.7 removed every echo and
# kept every recap and held-out gesture.
_STILL_EXPLAINING_CONFIDENCE = 0.7
# Calibrated on 75 discovery clauses (the earlier sets plus the 2026-10-03 afternoon
# session's recap, whose registry showed the applied filter values), each judged
# twice: from 0.3 to 0.6 no question about one detail, acknowledgement, or echo was
# circled, and no clause circled the wrong control; 0.7 lost recap clauses.
_RECAP_CONFIDENCE = 0.5
# A debrief offers only the few controls a new version added, so a real mention is
# picked with confidence: in live debriefs every control the agent named was chosen
# at 0.94 or more, and the one stray pick, on "The new version is ready", at 0.53.
_DEBRIEF_TARGET_CONFIDENCE = 0.75


@dataclass(frozen=True, slots=True)
class DelegationRoute:
    """What one delegation needs, as far as the application can serve it without a model.

    `target_id` is set only when showing that registered target is the accepted
    need; `asks_progress` only when the agent confidently asks for the status of
    the change being built. Neither set leaves the delegation to the model.
    """

    semantic_judgement_id: UUID
    target_id: str | None
    circles: bool
    asks_progress: bool
    detail: str


@dataclass(frozen=True, slots=True)
class SpeechGesture:
    """Which control to circle while the agent says one clause, if any.

    `target_id` is set only when the agent confidently refers to that registered
    target at a moment the cursor follows its words; otherwise the cursor stays
    where it is.
    """

    semantic_judgement_id: UUID
    target_id: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class RecapGesture:
    """What one clause of a possible recap names, and whether the turn is the recap.

    `target_id` is set when the clause confidently names that registered target
    and the customer had finished explaining. `restating` says whether the
    agent's turn so far is playing the request back; the caller decides, across
    the whole turn, whether to circle.
    """

    semantic_judgement_id: UUID
    target_id: str | None
    restating: bool
    detail: str


async def select_visual_target(
    database: Database,
    jev: JevClient,
    *,
    bundle: AssembledBundle,
    description: str,
    targets: Mapping[str, str],
) -> AttentionResult:
    """Choose which supplied registered target a description means.

    Jev invents neither selectors nor coordinates: it selects from the target
    identifiers the shared browser runtime currently registers, and code verifies
    binding and freshness before a gesture is dispatched.
    """
    return await _select_target(
        database,
        jev,
        bundle=bundle,
        instructions=f"Which supplied registered target does this describe: {description}",
        targets=targets,
    )


async def _select_target(
    database: Database,
    jev: JevClient,
    *,
    bundle: AssembledBundle,
    instructions: str,
    targets: Mapping[str, str],
) -> AttentionResult:
    """Ask one target-selection question over the supplied targets and check it."""
    if not targets:
        raise ValueError("visual target selection needs at least one supplied target")
    question_id = _TARGET_SELECTION
    options = (*targets, _NO_TARGET)
    criteria: dict[str, str] = {
        target_id: summary for target_id, summary in targets.items()
    }
    criteria[_NO_TARGET] = "None of the supplied targets matches."
    questions: dict[str, Question] = {
        question_id: Choice(instructions=instructions, criteria=criteria)
    }
    return await judge(
        database,
        jev,
        bundle=bundle,
        state=bundle_state(bundle),
        purpose="visual_target",
        questions=questions,
        supplied_options={question_id: options},
        thresholds={
            question_id: ChoiceThreshold(
                question_id=question_id,
                minimum_confidence=_TARGET_CONFIDENCE,
                no_match_options=frozenset({_NO_TARGET}),
            )
        },
    )


def delegation_questions(targets: Mapping[str, str]) -> dict[str, Question]:
    """The questions one delegation is routed by; the gesture ones only when targets exist.

    GPT-Live's delegation event carries no task, only the conversation around it,
    so the batch asks independent questions over the latest words: what the agent
    needs, and, when the preview registers controls, whether that is to show one,
    which (on that premise), and whether to circle it or point at it.
    """
    situation = (
        "The interview agent just paused to ask its application for help. `earlier` is "
        "the conversation before, `participant_last_said` is the customer's latest words, "
        "and `agent_last_said` is what the agent said as it asked for help."
    )
    questions: dict[str, Question] = {
        _DELEGATION_NEED: Choice(
            instructions=f"{situation} Which of these does the agent need right now?",
            criteria={
                _PROGRESS_UPDATE: (
                    "Only the status of the change being built for the customer: how it "
                    "is going, whether the new version is ready, or whether they need to "
                    "reload or refresh to see it."
                ),
                "record": (
                    "To record something the customer agreed, decided, or answered, such "
                    "as agreeing to a change and its checks, or their answers to the "
                    "agreed checks."
                ),
                _OTHER_NEED: (
                    "Anything else: showing something on screen, looking up how the "
                    "product works or behaved, answering another question, or deciding "
                    "what to ask next."
                ),
            },
        ),
    }
    if not targets:
        return questions
    criteria: dict[str, str] = {target_id: summary for target_id, summary in targets.items()}
    criteria[_NO_TARGET] = "None of these controls is what should be shown."
    questions[_GESTURE_INTENT] = Choice(
        instructions=f"{situation} What does the agent need from the application right now?",
        criteria={
            _SHOW_ON_SCREEN: (
                "To point at, circle, highlight, or show the customer a specific "
                "control, value, chart, table, or panel on their product screen."
            ),
            _OTHER_NEED: (
                "Anything else: checking progress, recording an agreement or answer, "
                "looking up product knowledge, or answering a question."
            ),
        },
    )
    questions[_TARGET_SELECTION] = Choice(
        instructions=(
            "Suppose the agent needs to show the customer something on their product "
            "screen. From `participant_last_said` and `agent_last_said` (with "
            "`earlier` for context), which supplied on-screen control should it show?"
        ),
        criteria=criteria,
    )
    questions[_GESTURE_KIND] = Choice(
        instructions=(
            "Suppose the agent needs to show the customer something on their product "
            "screen. Should the application circle it, or point the cursor at it?"
        ),
        criteria={
            "circle": (
                "Circle or highlight it: someone asked to circle, highlight, or mark "
                "it, or the agent is confirming exactly which one is meant."
            ),
            "point": "Point the cursor at it: someone asked where it is or to be shown it.",
            _NO_MATCH: "The conversation does not say how it should be shown.",
        },
    )
    return questions


async def route_delegation(
    database: Database,
    jev: JevClient,
    *,
    bundle: AssembledBundle,
    targets: Mapping[str, str],
) -> DelegationRoute:
    """Decide whether the application can serve a delegation itself, and how.

    A gesture is accepted only when the need to show something and the target both
    clear their floors; the circle-or-point kind only shapes it, so an unsure kind
    points. A progress update is accepted only when that need clears its own
    floor. One batch answers both, the bundle is rechecked for freshness, and the
    judgement is recorded with both verdicts.
    """
    questions = delegation_questions(targets)
    batch = await jev.judge(state=conversation_state(bundle), questions=questions)
    progress = check_batch(
        batch=batch,
        supplied_options={_DELEGATION_NEED: DELEGATION_NEED_OPTIONS},
        thresholds={
            _DELEGATION_NEED: ChoiceThreshold(
                question_id=_DELEGATION_NEED,
                minimum_confidence=_PROGRESS_CONFIDENCE,
                no_match_options=frozenset({"record", _OTHER_NEED}),
            )
        },
    )
    gesture = (
        check_batch(
            batch=batch,
            supplied_options={
                _GESTURE_INTENT: GESTURE_INTENT_OPTIONS,
                _TARGET_SELECTION: (*targets, _NO_TARGET),
            },
            thresholds={
                _GESTURE_INTENT: ChoiceThreshold(
                    question_id=_GESTURE_INTENT,
                    minimum_confidence=_GESTURE_INTENT_CONFIDENCE,
                    no_match_options=frozenset({_OTHER_NEED}),
                ),
                _TARGET_SELECTION: ChoiceThreshold(
                    question_id=_TARGET_SELECTION,
                    minimum_confidence=_GESTURE_TARGET_CONFIDENCE,
                    no_match_options=frozenset({_NO_TARGET}),
                ),
            },
        )
        if targets
        else AcceptanceOutcome(
            accepted=False, rejections=(), detail="no registered targets to show"
        )
    )
    freshness = await check_bundle_freshness(database, bundle_id=bundle.bundle_id)
    stale = None if freshness.fresh else f"{STALE_BUNDLE}: {freshness.detail}"
    shows = gesture.accepted and stale is None
    asks_progress = progress.accepted and stale is None and not shows
    outcome = AcceptanceOutcome(
        accepted=shows or asks_progress,
        rejections=(*gesture.rejections, *progress.rejections),
        detail="; ".join(
            part for part in (stale, f"gesture: {gesture.detail}", f"progress: {progress.detail}")
            if part is not None
        ),
    )
    judgement_id = await record_judgement(
        database,
        bundle=bundle,
        purpose="delegation_routing",
        batch=batch,
        outcome=outcome,
        question_ids=tuple(questions),
    )
    _log.info(
        "jev_attention_recorded",
        client_id=str(bundle.client_id),
        product_signal_id=str(bundle.product_signal_id),
        context_bundle_id=str(bundle.bundle_id),
        purpose="delegation_routing",
        model_id=batch.model_id,
        accepted=outcome.accepted,
        rejections=[rejection.value for rejection in outcome.rejections],
        detail=outcome.detail,
        semantic_judgement_id=str(judgement_id),
    )
    kind = batch.choices.get(_GESTURE_KIND)
    return DelegationRoute(
        semantic_judgement_id=judgement_id,
        target_id=batch.choices[_TARGET_SELECTION].choice if shows else None,
        circles=(
            shows
            and kind is not None
            and kind.choice == "circle"
            and kind.confidence >= _GESTURE_KIND_CONFIDENCE
        ),
        asks_progress=asks_progress,
        detail=outcome.detail,
    )


_SPEECH_SITUATION = (
    "The interview agent is talking to the customer, whose product screen is open. "
    "`earlier` is the conversation before, `participant_last_said` is the customer's "
    "latest words, `agent_last_said` is the agent's turn so far, and "
    "`agent_saying_now` is the part of it the agent is saying right now."
)


def recap_gesture_questions(targets: Mapping[str, str]) -> dict[str, Question]:
    """The questions a clause of the agent's recap of the request is judged by.

    They ask independently whether the words are about a specific control the
    customer can see, whether the agent's turn is playing the customer's request
    back to them, which supplied control the words name (on that premise), and
    whether the customer had finished explaining or the agent is echoing them
    mid-way.
    """
    return {
        _SPEECH_INTENT: _about_a_control(),
        _RECAP: Choice(
            instructions=(
                f"{_SPEECH_SITUATION} Taking `agent_last_said` as a whole, is the agent "
                "restating or summarizing back to the customer what they asked for or "
                "complained about, to check it understood them?"
            ),
            criteria={
                _RESTATING: (
                    "Yes: the agent is playing the customer's request or complaint back "
                    "to them, naming what it covers."
                ),
                _OTHER_TALK: (
                    "No: the agent asks about one detail, acknowledges, explains "
                    "something, or talks about anything else."
                ),
            },
        ),
        _TARGET_SELECTION: _which_control(targets),
        _CUSTOMER_TURN: Choice(
            instructions=(
                f"{_SPEECH_SITUATION} Has the customer finished explaining, or is the "
                "agent reacting while they are still partway through showing or "
                "listing something, with more to come?"
            ),
            criteria={
                "finished": (
                    "Finished: they made their point, answered, or are waiting for the "
                    "agent, so the agent has the floor."
                ),
                _STILL_EXPLAINING: (
                    "Still explaining: they are partway through showing or listing "
                    "steps, and the agent is reacting to one of them mid-way."
                ),
            },
        ),
    }


def debrief_gesture_questions(targets: Mapping[str, str]) -> dict[str, Question]:
    """The questions a clause of the agent's walkthrough of a new version is judged by.

    The supplied controls are only the ones the version added or moved, so the
    questions ask whether the words are about one of them and which.
    """
    return {
        _SPEECH_INTENT: _about_a_control(),
        _TARGET_SELECTION: _which_control(targets),
    }


def _about_a_control() -> Choice:
    return Choice(
        instructions=(
            f"{_SPEECH_SITUATION} Are the words in `agent_saying_now` about a specific "
            "control, value, chart, or panel on the customer's screen: clarifying or "
            "restating their request or complaint about it, explaining it, or asking "
            "about it?"
        ),
        criteria={
            _SHOW: (
                "Yes: they are about that specific thing on the screen, so showing it "
                "while the agent says them helps the customer follow."
            ),
            _NO_GESTURE: (
                "No: they are about nothing specific on the screen, such as a greeting, "
                "an acknowledgement, empathy, a question about the customer's work or "
                "habits, or something that is not on the screen."
            ),
        },
    )


def _which_control(targets: Mapping[str, str]) -> Choice:
    criteria: dict[str, str] = {target_id: summary for target_id, summary in targets.items()}
    criteria[_NO_TARGET] = "None of these controls is what `agent_saying_now` refers to."
    return Choice(
        instructions=(
            f"{_SPEECH_SITUATION} Suppose the agent shows the customer what "
            "`agent_saying_now` refers to. Which supplied on-screen control is it?"
        ),
        criteria=criteria,
    )


async def route_recap_gesture(
    database: Database,
    jev: JevClient,
    *,
    bundle: AssembledBundle,
    saying: str,
    targets: Mapping[str, str],
) -> RecapGesture:
    """Judge one clause of what may be the agent's recap of the customer's request.

    The clause names a control when the words are about it, both clear their
    floors, and the agent is not echoing a customer who is still partway through
    showing something. Whether the turn is playing the request back is judged
    alongside, and its own floor decides `restating`.
    """
    result = await _judge_spoken(
        database,
        jev,
        bundle=bundle,
        saying=saying,
        targets=targets,
        questions=recap_gesture_questions(targets),
        target_confidence=_SPEECH_TARGET_CONFIDENCE,
    )
    echoing = (
        result.selections.get(_CUSTOMER_TURN) == _STILL_EXPLAINING
        and result.confidences[_CUSTOMER_TURN] >= _STILL_EXPLAINING_CONFIDENCE
    )
    return RecapGesture(
        semantic_judgement_id=result.semantic_judgement_id,
        target_id=(
            result.selections[_TARGET_SELECTION] if result.accepted and not echoing else None
        ),
        restating=(
            result.fresh
            and result.selections.get(_RECAP) == _RESTATING
            and result.confidences[_RECAP] >= _RECAP_CONFIDENCE
        ),
        detail=result.detail,
    )


async def route_debrief_gesture(
    database: Database,
    jev: JevClient,
    *,
    bundle: AssembledBundle,
    saying: str,
    targets: Mapping[str, str],
) -> SpeechGesture:
    """Decide which of a new version's controls to circle as the agent describes it."""
    result = await _judge_spoken(
        database,
        jev,
        bundle=bundle,
        saying=saying,
        targets=targets,
        questions=debrief_gesture_questions(targets),
        target_confidence=_DEBRIEF_TARGET_CONFIDENCE,
    )
    return SpeechGesture(
        semantic_judgement_id=result.semantic_judgement_id,
        target_id=result.selections[_TARGET_SELECTION] if result.accepted else None,
        detail=result.detail,
    )


async def _judge_spoken(
    database: Database,
    jev: JevClient,
    *,
    bundle: AssembledBundle,
    saying: str,
    targets: Mapping[str, str],
    questions: Mapping[str, Question],
    target_confidence: float,
) -> AttentionResult:
    """Judge one spoken clause: whether it is about a control and which.

    Any further question is answered alongside without a floor of its own; its
    caller reads the selection.
    """
    if not targets:
        raise ValueError("a speech gesture needs at least one supplied target")
    return await judge(
        database,
        jev,
        bundle=bundle,
        state={**conversation_state(bundle), "agent_saying_now": saying},
        purpose="speech_gesture",
        questions=dict(questions),
        supplied_options={
            _SPEECH_INTENT: SPEECH_GESTURE_OPTIONS,
            _TARGET_SELECTION: (*targets, _NO_TARGET),
        },
        thresholds={
            _SPEECH_INTENT: ChoiceThreshold(
                question_id=_SPEECH_INTENT,
                minimum_confidence=_SPEECH_INTENT_CONFIDENCE,
                no_match_options=frozenset({_NO_GESTURE}),
            ),
            _TARGET_SELECTION: ChoiceThreshold(
                question_id=_TARGET_SELECTION,
                minimum_confidence=target_confidence,
                no_match_options=frozenset({_NO_TARGET}),
            ),
        },
    )
