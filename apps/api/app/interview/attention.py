"""Jev attention work that runs beside a live conversation.

Jev answers two focused semantic questions over a supplied context bundle: whether
what the person just did in the preview is worth the agent acknowledging out loud,
and which flow the evidence now supports. These judgements run on relevant updates,
never as a serial approval step before every utterance, and they authorize nothing:
the controller commits the decision. What the agent cursor shows comes from the
voice agent's own gesture marks, and which agreed checks a new version already
showed is the agent's own judgement from the activity it is told about.

Every batch is checked by `app.connectors.jev_acceptance` against thresholds
calibrated for that exact question, and the bundle it used is rechecked for
freshness before the answer is accepted (`app.interview.judgements`). A missing
answer, an unsupplied reference, an explicit unknown alternative, or a flat
distribution leaves the decision unresolved, which is a legitimate outcome rather
than a weak yes.
"""
from collections.abc import Sequence

from typesafe_sdk import Choice, Question

from app.connectors.jev_acceptance import ChoiceThreshold
from app.connectors.typesafe_jev import JevClient
from app.context.bundles import AssembledBundle
from app.db.pool import Database
from app.interview.judgements import (
    AttentionResult,
    bundle_state,
    conversation_state,
    judge,
)

__all__ = [
    "judge_acknowledgement",
    "propose_flow_classification",
]

_FLOW_OPTIONS: tuple[str, ...] = ("bugfix_flow", "feature_request_flow", "unclear")
_ACKNOWLEDGEMENT_OPTIONS: tuple[str, ...] = ("acknowledge", "stay_quiet", "unclear")

_NO_MATCH = "unclear"
_ACKNOWLEDGEMENT = "activity::acknowledge"
_STAY_QUIET = "stay_quiet"

# Confidence floors are calibrated per question type and never transferred.
_CLASSIFICATION_CONFIDENCE = 0.7
# Calibrated on 17 Saved Conversion Views moments judged twice and 6 held out judged
# three times: moments the conversation waited on (a page opened when asked or
# announced, a save, refresh, or reopen the agent asked for, the new version
# appearing) were acknowledged at 0.77 or more, and no walkthrough step, aside, or
# idle click above 0.32. Two held-out moments asked for less directly scored 0.41 to
# 0.49, so 0.5 stays quiet where Jev is unsure.
_ACKNOWLEDGEMENT_CONFIDENCE = 0.5


def _acknowledgement_questions() -> dict[str, Question]:
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
        questions=_acknowledgement_questions(),
        supplied_options={_ACKNOWLEDGEMENT: _ACKNOWLEDGEMENT_OPTIONS},
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
        supplied_options={question_id: _FLOW_OPTIONS},
        thresholds={
            question_id: ChoiceThreshold(
                question_id=question_id,
                minimum_confidence=_CLASSIFICATION_CONFIDENCE,
                no_match_options=frozenset({_NO_MATCH}),
            )
        },
    )
