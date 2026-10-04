"""Running one Jev batch over a context bundle, checking it, and recording it.

Every attention question in the interview runtime goes through here: the batch is
checked by `app.connectors.jev_acceptance` against the thresholds calibrated for
its exact questions, the bundle it used is rechecked for freshness, and the
answers and verdict are written as one immutable `semantic_judgements` row. The
questions themselves live with their callers in `app.interview.attention` and
`app.interview.gesture_attention`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from psycopg.types.json import Jsonb
from typesafe_sdk import Question

from app.connectors.jev_acceptance import (
    AcceptanceOutcome,
    ChoiceThreshold,
    JevPrimitive,
    accept_all,
    check_answer_primitives,
    check_choice,
    require_no_match_option,
)
from app.connectors.typesafe_jev import JevBatchResult, JevClient
from app.context.bundles import AssembledBundle
from app.context.freshness import check_bundle_freshness
from app.context.sources import SECTION_RECENT_CONVERSATION
from app.db.pool import Database
from app.envelope import JsonValue
from app.ids import uuid7
from app.logging import get_logger

__all__ = [
    "STALE_BUNDLE",
    "AttentionResult",
    "bundle_state",
    "check_batch",
    "conversation_state",
    "judge",
    "record_judgement",
]

_log = get_logger(__name__)

STALE_BUNDLE = "context_bundle_stale"


@dataclass(frozen=True, slots=True)
class AttentionResult:
    """One recorded Jev judgement and the acceptance verdict code reached."""

    semantic_judgement_id: UUID
    accepted: bool
    fresh: bool
    """Whether the bundle was still fresh when the answer arrived; a stale one is never accepted."""
    detail: str
    selections: Mapping[str, str]
    confidences: Mapping[str, float]


async def judge(
    database: Database,
    jev: JevClient,
    *,
    bundle: AssembledBundle,
    state: Mapping[str, JsonValue],
    purpose: str,
    questions: Mapping[str, Question],
    supplied_options: Mapping[str, Sequence[str]],
    thresholds: Mapping[str, ChoiceThreshold],
) -> AttentionResult:
    """Run one batch, check it, recheck bundle freshness, and record the result."""
    for question_id, options in supplied_options.items():
        precheck = require_no_match_option(
            question_id=question_id,
            declared_options=options,
            no_match_options=thresholds[question_id].no_match_options,
        )
        if not precheck.accepted:
            raise ValueError(precheck.detail)

    batch = await jev.judge(state=dict(state), questions=dict(questions))
    outcome = check_batch(
        batch=batch, supplied_options=supplied_options, thresholds=thresholds
    )
    freshness = await check_bundle_freshness(database, bundle_id=bundle.bundle_id)
    if not freshness.fresh:
        outcome = AcceptanceOutcome(
            accepted=False,
            rejections=outcome.rejections,
            detail=f"{STALE_BUNDLE}: {freshness.detail}",
        )

    selections = {name: answer.choice for name, answer in batch.choices.items()}
    confidences = {name: answer.confidence for name, answer in batch.choices.items()}
    judgement_id = await record_judgement(
        database,
        bundle=bundle,
        purpose=purpose,
        batch=batch,
        outcome=outcome,
        question_ids=tuple(questions),
    )
    _log.info(
        "jev_attention_recorded",
        client_id=str(bundle.client_id),
        product_signal_id=str(bundle.product_signal_id),
        context_bundle_id=str(bundle.bundle_id),
        purpose=purpose,
        model_id=batch.model_id,
        accepted=outcome.accepted,
        rejections=[rejection.value for rejection in outcome.rejections],
        detail=outcome.detail,
        semantic_judgement_id=str(judgement_id),
    )
    return AttentionResult(
        semantic_judgement_id=judgement_id,
        accepted=outcome.accepted,
        fresh=freshness.fresh,
        detail=outcome.detail,
        selections=selections,
        confidences=confidences,
    )


def check_batch(
    *,
    batch: JevBatchResult,
    supplied_options: Mapping[str, Sequence[str]],
    thresholds: Mapping[str, ChoiceThreshold],
) -> AcceptanceOutcome:
    """Apply the contract's acceptance checks to one batch."""
    expected = dict.fromkeys(thresholds, JevPrimitive.CHOICE)
    outcomes = [
        check_answer_primitives(
            expected=expected, returned=batch.returned_primitives()
        )
    ]
    for question_id, threshold in thresholds.items():
        answer = batch.choices.get(question_id)
        if answer is None:
            continue
        outcomes.append(
            check_choice(
                question_id=question_id,
                choice=answer.choice,
                confidence=answer.confidence,
                supplied_options=supplied_options[question_id],
                threshold=threshold,
            )
        )
    return accept_all(outcomes)


def bundle_state(bundle: AssembledBundle) -> dict[str, JsonValue]:
    """Render the bundle as the supplied state, with its provenance attached.

    Jev receives a question-specific subset of the bundle, already pre-filtered by
    scope in code. The full transcript, DOM, and telemetry history never travel
    here.
    """
    return {
        "context_bundle_id": str(bundle.bundle_id),
        "context_revision": bundle.context_revision,
        "digest": bundle.digest,
        "cutoff_at": bundle.cutoff_at.isoformat(),
        "context": [
            {
                "section": item.section,
                "source_kind": item.source_kind,
                "source_id": item.source_id,
                "source_revision": item.source_revision,
                "body": dict(item.body),
            }
            for item in bundle.included
        ],
        "omitted_sources": [
            {
                "section": dropped.section,
                "source_kind": dropped.source_kind,
                "source_id": dropped.source_id,
                "reason": dropped.reason,
            }
            for dropped in bundle.omitted
        ],
    }


def conversation_state(bundle: AssembledBundle) -> dict[str, JsonValue]:
    """Render a conversation-only bundle as the latest exchange, with its provenance.

    Transcript fragments arrive a few words at a time; consecutive fragments of one
    speaker are joined into what they said, and the newest participant words and
    the agent words after them are named, so the question can point at them.
    """
    turns: list[tuple[str, str]] = []
    for item in bundle.section(SECTION_RECENT_CONVERSATION):
        speaker = str(item.body["speaker"])
        text = str(item.body["text"])
        if turns and turns[-1][0] == speaker:
            turns[-1] = (speaker, turns[-1][1] + text)
        else:
            turns.append((speaker, text))
    agent_last = turns.pop()[1] if turns and turns[-1][0] == "agent" else ""
    participant_last = turns.pop()[1] if turns and turns[-1][0] == "participant" else ""
    earlier = " ".join(
        f"{'Agent' if speaker == 'agent' else 'Participant'}: {text.strip()}"
        for speaker, text in turns
    )
    return {
        "context_bundle_id": str(bundle.bundle_id),
        "context_revision": bundle.context_revision,
        "digest": bundle.digest,
        "cutoff_at": bundle.cutoff_at.isoformat(),
        "earlier": earlier,
        "participant_last_said": participant_last.strip(),
        "agent_last_said": agent_last.strip(),
    }


async def record_judgement(
    database: Database,
    *,
    bundle: AssembledBundle,
    purpose: str,
    batch: JevBatchResult,
    outcome: AcceptanceOutcome,
    question_ids: Sequence[str],
) -> UUID:
    """Write the immutable judgement record, answers and verdict together."""
    judgement_id = uuid7()
    answers: dict[str, JsonValue] = {
        name: {
            "choice": answer.choice,
            "confidence": answer.confidence,
            "probabilities": dict(answer.probabilities),
        }
        for name, answer in batch.choices.items()
    }
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.semantic_judgements (
                id, client_id, product_signal_id, interview_session_id,
                context_bundle_id, purpose, model_id, question_ids, answers,
                acceptance, accepted, rejections
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                judgement_id,
                bundle.client_id,
                bundle.product_signal_id,
                bundle.interview_session_id,
                bundle.bundle_id,
                purpose,
                batch.model_id,
                list(question_ids),
                Jsonb(answers),
                Jsonb({"detail": outcome.detail}),
                outcome.accepted,
                [rejection.value for rejection in outcome.rejections],
            ),
        )
    return judgement_id
