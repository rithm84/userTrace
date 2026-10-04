"""Jev attention work that runs beside a live conversation.

Jev answers focused semantic questions over a supplied context bundle: whether a
criterion is addressed, whether the new version already showed an agreed check,
whether what the person just did in the preview is worth the agent acknowledging
out loud, how a new statement relates to existing claims, which of the supplied
lookups deserves attention, and which flow the evidence now supports. These judgements
run on relevant updates, never as a serial approval step before every utterance,
and they authorize nothing: the controller commits the decision. What the agent
cursor shows is decided in `app.interview.gesture_attention`.

Every batch is checked by `app.connectors.jev_acceptance` against thresholds
calibrated for that exact question, and the bundle it used is rechecked for
freshness before the answer is accepted (`app.interview.judgements`). A missing
answer, an unsupplied reference, an explicit unknown alternative, or a flat
distribution leaves the decision unresolved, which is a legitimate outcome rather
than a weak yes.
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from typesafe_sdk import Choice, Question

from app.connectors.jev_acceptance import (
    AcceptanceOutcome,
    ChoiceThreshold,
    check_choice,
)
from app.connectors.typesafe_jev import JevClient
from app.context.bundles import AssembledBundle
from app.context.freshness import check_bundle_freshness
from app.db.pool import Database
from app.interview.judgements import (
    STALE_BUNDLE,
    AttentionResult,
    bundle_state,
    conversation_state,
    judge,
    record_judgement,
)
from app.logging import get_logger

__all__ = [
    "ACKNOWLEDGEMENT_OPTIONS",
    "CHECK_SHOWN_OPTIONS",
    "CLAIM_RELATION_OPTIONS",
    "COVERAGE_OPTIONS",
    "FLOW_OPTIONS",
    "ChecksShown",
    "acknowledgement_questions",
    "assess_evidence_coverage",
    "checks_shown_questions",
    "judge_acknowledgement",
    "judge_checks_shown",
    "propose_flow_classification",
    "relate_claim",
    "route_attention",
]

_log = get_logger(__name__)

COVERAGE_OPTIONS: tuple[str, ...] = ("addressed", "unanswered", "contradicted", "unclear")
CLAIM_RELATION_OPTIONS: tuple[str, ...] = (
    "supports",
    "contradicts",
    "unrelated",
    "unclear",
)
FLOW_OPTIONS: tuple[str, ...] = ("bugfix_flow", "feature_request_flow", "unclear")
CHECK_SHOWN_OPTIONS: tuple[str, ...] = ("shown", "not_shown", "contradicted", "unclear")
ACKNOWLEDGEMENT_OPTIONS: tuple[str, ...] = ("acknowledge", "stay_quiet", "unclear")

_NO_MATCH = "unclear"
_NO_TASK = "no_task"
_CHECK_SHOWN = "check_shown::"
_SHOWN = "shown"
_CONTRADICTED = "contradicted"
_ACKNOWLEDGEMENT = "activity::acknowledge"
_STAY_QUIET = "stay_quiet"

# Confidence floors are calibrated per question type and never transferred.
_COVERAGE_CONFIDENCE = 0.6
_RELATION_CONFIDENCE = 0.65
_ROUTING_CONFIDENCE = 0.55
_CLASSIFICATION_CONFIDENCE = 0.7
# Calibrated on the demo's real validation activity replayed at six moments (before
# the save, after it, after the reload, after the reopen, and two reopens edited to
# restore some or none of the filters), each judged four times: a correct reopen
# scored 0.89 to 0.93, a partial one at most 0.66, and a check on the relative date
# range 0.77 to 0.86, so 0.75 called 40 of 44 shown checks right with none wrong;
# the 4 missed were a check about today's dates that a preview on a fixed date
# cannot show.
_CHECK_SHOWN_CONFIDENCE = 0.75
# Calibrated on 17 Saved Conversion Views moments judged twice and 6 held out judged
# three times: moments the conversation waited on (a page opened when asked or
# announced, a save, refresh, or reopen the agent asked for, the new version
# appearing) were acknowledged at 0.77 or more, and no walkthrough step, aside, or
# idle click above 0.32. Two held-out moments asked for less directly scored 0.41 to
# 0.49, so 0.5 stays quiet where Jev is unsure.
_ACKNOWLEDGEMENT_CONFIDENCE = 0.5


@dataclass(frozen=True, slots=True)
class ChecksShown:
    """Which agreed checks the person's activity in the new version showed, by id."""

    semantic_judgement_id: UUID
    shown: frozenset[str]
    contradicted: frozenset[str]


async def assess_evidence_coverage(
    database: Database,
    jev: JevClient,
    *,
    bundle: AssembledBundle,
    criteria: Mapping[str, str],
) -> AttentionResult:
    """Ask, per acceptance criterion, whether the evidence addresses it.

    ``criteria`` maps a criterion identifier to its statement. Code checks which
    records exist; Jev assesses meaning, and the gaps become follow-up questions.
    """
    if not criteria:
        raise ValueError("evidence coverage needs at least one acceptance criterion")
    questions: dict[str, Question] = {}
    supplied: dict[str, tuple[str, ...]] = {}
    thresholds: dict[str, ChoiceThreshold] = {}
    for criterion_id, statement in criteria.items():
        question_id = f"coverage::{criterion_id}"
        questions[question_id] = Choice(
            instructions=(
                "Judge only from `context`. Does the participant's account and the "
                "selected observed evidence address this acceptance criterion: "
                f"{statement}"
            ),
            criteria={
                "addressed": "Some statement or observation speaks to this criterion.",
                "unanswered": "Nothing in the context speaks to this criterion.",
                "contradicted": "Something in the context conflicts with this criterion.",
                "unclear": "The context does not settle which of the above holds.",
            },
        )
        supplied[question_id] = COVERAGE_OPTIONS
        thresholds[question_id] = ChoiceThreshold(
            question_id=question_id,
            minimum_confidence=_COVERAGE_CONFIDENCE,
            no_match_options=frozenset({_NO_MATCH}),
        )
    return await judge(
        database,
        jev,
        bundle=bundle,
        state=bundle_state(bundle),
        purpose="evidence_coverage",
        questions=questions,
        supplied_options=supplied,
        thresholds=thresholds,
    )


async def relate_claim(
    database: Database,
    jev: JevClient,
    *,
    bundle: AssembledBundle,
    statement: str,
    claims: Mapping[str, str],
) -> AttentionResult:
    """Compare a new contextualized statement with the supplied relevant claims.

    A flag never rewrites the canonical account. Complex interpretation goes to a
    delegation; this only labels the relationship.
    """
    if not claims:
        raise ValueError("claim relationship needs at least one supplied claim")
    questions: dict[str, Question] = {}
    supplied: dict[str, tuple[str, ...]] = {}
    thresholds: dict[str, ChoiceThreshold] = {}
    for claim_id, claim_text in claims.items():
        question_id = f"relation::{claim_id}"
        questions[question_id] = Choice(
            instructions=(
                f"New statement: {statement}\n"
                f"Existing claim: {claim_text}\n"
                "How does the new statement relate to the existing claim? Preserve "
                "negations and uncertainty; do not resolve them."
            ),
            criteria={
                "supports": "The new statement agrees with the existing claim.",
                "contradicts": "The new statement conflicts with the existing claim.",
                "unrelated": "The two speak about different things.",
                "unclear": "The wording does not settle the relationship.",
            },
        )
        supplied[question_id] = CLAIM_RELATION_OPTIONS
        thresholds[question_id] = ChoiceThreshold(
            question_id=question_id,
            minimum_confidence=_RELATION_CONFIDENCE,
            no_match_options=frozenset({_NO_MATCH}),
        )
    return await judge(
        database,
        jev,
        bundle=bundle,
        state=bundle_state(bundle),
        purpose="claim_relationship",
        questions=questions,
        supplied_options=supplied,
        thresholds=thresholds,
    )


async def route_attention(
    database: Database,
    jev: JevClient,
    *,
    bundle: AssembledBundle,
    tasks: Mapping[str, str],
) -> AttentionResult:
    """Choose among the lookups and analyses the controller already authorized.

    Jev selects from supplied task identifiers; the controller still checks scope
    and budgets before anything runs.
    """
    if not tasks:
        raise ValueError("attention routing needs at least one supplied task")
    question_id = "attention::next_task"
    options = (*tasks, _NO_TASK)
    criteria: dict[str, str] = {task_id: summary for task_id, summary in tasks.items()}
    criteria[_NO_TASK] = "None of the supplied tasks would help right now."
    questions: dict[str, Question] = {
        question_id: Choice(
            instructions=(
                "Judge only from `context`. Which supplied task would most help the "
                "interview make progress right now?"
            ),
            criteria=criteria,
        )
    }
    return await judge(
        database,
        jev,
        bundle=bundle,
        state=bundle_state(bundle),
        purpose="attention_routing",
        questions=questions,
        supplied_options={question_id: options},
        thresholds={
            question_id: ChoiceThreshold(
                question_id=question_id,
                minimum_confidence=_ROUTING_CONFIDENCE,
                no_match_options=frozenset({_NO_TASK}),
            )
        },
    )


def checks_shown_questions(checks: Mapping[str, str]) -> dict[str, Question]:
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


async def judge_checks_shown(
    database: Database,
    jev: JevClient,
    *,
    bundle: AssembledBundle,
    checks: Mapping[str, str],
    activity: Sequence[str],
) -> ChecksShown:
    """Ask, per agreed check, whether the person's activity in the new version showed it.

    ``checks`` maps a criterion identifier to its statement, and ``activity`` is
    what the person did in the new version and what the product did in response,
    oldest first, in the product's own terms. Each check is decided on its own
    floor, so one unsure check does not hide another the activity plainly showed.
    """
    if not checks:
        raise ValueError("judging shown checks needs at least one agreed check")
    if not activity:
        raise ValueError("judging shown checks needs activity in the new version")
    questions = checks_shown_questions(checks)
    batch = await jev.judge(
        state={
            "context_bundle_id": str(bundle.bundle_id),
            "context_revision": bundle.context_revision,
            "digest": bundle.digest,
            "cutoff_at": bundle.cutoff_at.isoformat(),
            "activity_in_new_version": list(activity),
        },
        questions=questions,
    )
    verdicts: dict[str, str] = {}
    for check_id in checks:
        question_id = f"{_CHECK_SHOWN}{check_id}"
        answer = batch.choices.get(question_id)
        if answer is None:
            continue
        accepted = check_choice(
            question_id=question_id,
            choice=answer.choice,
            confidence=answer.confidence,
            supplied_options=CHECK_SHOWN_OPTIONS,
            threshold=ChoiceThreshold(
                question_id=question_id,
                minimum_confidence=_CHECK_SHOWN_CONFIDENCE,
                no_match_options=frozenset({"not_shown", _NO_MATCH}),
            ),
        )
        if accepted.accepted:
            verdicts[check_id] = answer.choice
    freshness = await check_bundle_freshness(database, bundle_id=bundle.bundle_id)
    if not freshness.fresh:
        verdicts = {}
    outcome = AcceptanceOutcome(
        accepted=bool(verdicts),
        rejections=(),
        detail=(
            f"{STALE_BUNDLE}: {freshness.detail}"
            if not freshness.fresh
            else f"decided {sorted(verdicts.items())}"
        ),
    )
    judgement_id = await record_judgement(
        database,
        bundle=bundle,
        purpose="evidence_coverage",
        batch=batch,
        outcome=outcome,
        question_ids=tuple(questions),
    )
    _log.info(
        "jev_attention_recorded",
        client_id=str(bundle.client_id),
        product_signal_id=str(bundle.product_signal_id),
        context_bundle_id=str(bundle.bundle_id),
        purpose="evidence_coverage",
        model_id=batch.model_id,
        accepted=outcome.accepted,
        detail=outcome.detail,
        semantic_judgement_id=str(judgement_id),
    )
    return ChecksShown(
        semantic_judgement_id=judgement_id,
        shown=frozenset(check for check, verdict in verdicts.items() if verdict == _SHOWN),
        contradicted=frozenset(
            check for check, verdict in verdicts.items() if verdict == _CONTRADICTED
        ),
    )


def acknowledgement_questions() -> dict[str, Question]:
    """The one question an acknowledgement is decided by, keyed `activity::acknowledge`."""
    return {
        _ACKNOWLEDGEMENT: Choice(
            instructions=(
                "The interview agent and the customer are talking while the customer uses "
                "their product preview. `earlier` is the conversation before, "
                "`participant_last_said` is the customer's latest words, `agent_last_said` "
                "is what the agent said after them, and `just_did` is what the customer "
                "just did in the preview, oldest first, in the product's own terms. "
                "Neither of them is talking right now. Should the agent briefly "
                "acknowledge out loud what the customer just did, in a few words such as "
                '"There it is"?'
            ),
            criteria={
                "acknowledge": (
                    "Yes: the conversation was waiting on exactly this, so the customer "
                    "will expect the agent to notice: they finished what the agent just "
                    "asked them to do, they showed what they said they were about to show "
                    "(a screen opening, or what happens when they do something), or "
                    "something they were waiting for appeared."
                ),
                _STAY_QUIET: (
                    "No: it is one step of a sequence they are still going through, such "
                    "as setting one filter of several while naming each, it answers "
                    "nothing anyone was waiting on, or remarking on it would interrupt "
                    "them or narrate what they are doing."
                ),
                _NO_MATCH: "The conversation does not settle whether they expect it noticed.",
            },
        )
    }


async def judge_acknowledgement(
    database: Database,
    jev: JevClient,
    *,
    bundle: AssembledBundle,
    activity: Sequence[str],
) -> AttentionResult:
    """Ask whether the agent should briefly acknowledge what the person just did.

    ``bundle`` carries the recent conversation and ``activity`` what the person just
    did in the preview, oldest first, in the product's own terms. Acceptance means
    the agent should say so now; when it should, and how often, is the caller's
    policy.
    """
    if not activity:
        raise ValueError("judging an acknowledgement needs activity to acknowledge")
    return await judge(
        database,
        jev,
        bundle=bundle,
        state={**conversation_state(bundle), "just_did": list(activity)},
        purpose="attention_routing",
        questions=acknowledgement_questions(),
        supplied_options={_ACKNOWLEDGEMENT: ACKNOWLEDGEMENT_OPTIONS},
        thresholds={
            _ACKNOWLEDGEMENT: ChoiceThreshold(
                question_id=_ACKNOWLEDGEMENT,
                minimum_confidence=_ACKNOWLEDGEMENT_CONFIDENCE,
                no_match_options=frozenset({_STAY_QUIET, _NO_MATCH}),
            )
        },
    )


async def propose_flow_classification(
    database: Database, jev: JevClient, *, bundle: AssembledBundle
) -> AttentionResult:
    """Propose the interview-established flow type from the current evidence.

    The proposal is advisory. The control plane records a typed, source-linked
    classification; insufficient evidence stays unresolved rather than inventing
    a final classification.
    """
    question_id = "classification::established_flow"
    questions: dict[str, Question] = {
        question_id: Choice(
            instructions=(
                "Judge only from `context`. Taking the participant's updated account "
                "and the observed evidence together, which flow does this concern now "
                "belong to?"
            ),
            criteria={
                "bugfix_flow": "The participant reports or exhibits a product failure.",
                "feature_request_flow": (
                    "The participant asks for or implies a new or changed capability."
                ),
                "unclear": "The evidence does not settle which of the two applies.",
            },
        )
    }
    return await judge(
        database,
        jev,
        bundle=bundle,
        state=bundle_state(bundle),
        purpose="flow_classification",
        questions=questions,
        supplied_options={question_id: FLOW_OPTIONS},
        thresholds={
            question_id: ChoiceThreshold(
                question_id=question_id,
                minimum_confidence=_CLASSIFICATION_CONFIDENCE,
                no_match_options=frozenset({_NO_MATCH}),
            )
        },
    )
