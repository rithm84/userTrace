"""Jev, GPT-6.1 Sol, and plain rules, asked the same interview questions.

- **Jev** is asked exactly what production asks. The production function builds the
  question and the state (`judge_acknowledgement`, `propose_flow_classification`); a
  capturing client stops it at the call, so the question text never drifts from
  production, and the call is then made with the real client and accepted under the
  production thresholds. The checks-shown question, since removed from production,
  is asked as it was measured (`measured`).
- **Sol, as a judge**, gets the same question, criteria, and state in one structured
  Responses API request on the voice agent's configuration (`gpt-6.1-sol`, Ultrafast,
  low effort) and picks one of the same options. It has no confidence to threshold.
- **Sol, as the agent** (acknowledgements only), is the voice agent itself deciding
  in its turn: its own instructions, the conversation as messages, and the activity
  appended as the "Jev off" arm of the live ablation appends it
  (`ACKNOWLEDGE_IF_AWAITED`). A reply with words other than a backchannel is an
  acknowledgement.
- **Rules** read only the activity's milestone and the conversation's punctuation.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from openai import AsyncOpenAI
from typesafe_sdk import Choice, Question

from app.connectors.jev_acceptance import ChoiceThreshold, check_choice
from app.connectors.openai_models import (
    GPT_6_1_SOL,
    OpenAIEffort,
    OpenAIModelConfiguration,
    OpenAIServiceTier,
    TokenUsage,
    cost_usd,
)
from app.connectors.typesafe_jev import JevBatchResult, JevClient
from app.interview import attention
from app.interview.voice.instructions import _DISCOVERY_BY_FLOW, _INSTRUCTIONS
from tests.evals.jev import measured

__all__ = [
    "ACKNOWLEDGE_IF_AWAITED",
    "SOL",
    "Verdict",
    "jev_acknowledgement",
    "jev_checks",
    "jev_classification",
    "rule_acknowledgement",
    "rule_checks",
    "sol_agent_acknowledgement",
    "sol_judge",
]

SOL: Final = OpenAIModelConfiguration(
    model_id=GPT_6_1_SOL, effort=OpenAIEffort.LOW, service_tier=OpenAIServiceTier.ULTRAFAST
)

#: What the "Jev off" arm appends after the activity, speakably, for Sol to decide on.
ACKNOWLEDGE_IF_AWAITED: Final = (
    "If the conversation was waiting on exactly this (they finished what you asked them "
    "to do, they showed what they said they were about to show, or something they were "
    "waiting for appeared), acknowledge it out loud now in a few words, the way someone "
    "watching would, without describing or listing what they did, and not in words you "
    'have already used to acknowledge them, such as "There it is." once and "Nice." the '
    "next time. Otherwise reply with nothing at all, not even \"mm-hmm\": it is one step of "
    "something they are still doing, it answers nothing anyone was waiting on, or saying "
    "something would interrupt them or narrate what they are doing."
)
_PROVENANCE: Final[Mapping[str, Any]] = {
    "context_bundle_id": "01a19627-0000-7000-8000-000000000000",
    "context_revision": 1,
    "digest": "sha256:replay",
    "cutoff_at": "2026-11-01T08:14:00+00:00",
}
_BACKCHANNEL: Final = re.compile(r"^(?:(?:mm-?hmm|mhm|uh-huh|mm)[.!,]?\s*)*$", re.IGNORECASE)


@dataclass
class Verdict:
    """One judge's answer to one moment."""

    #: The option each question chose, by question id, before any threshold.
    choices: dict[str, str]
    #: What the caller acts on: for an acknowledgement `acknowledge` or `stay_quiet`; for
    #: checks `shown`, `contradicted`, or `none` per check; for a classification the
    #: flow or `unresolved`.
    decided: dict[str, str]
    seconds: float
    confidences: dict[str, float] = field(default_factory=dict)
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    said: str | None = None


class _Captured(Exception):
    def __init__(self, state: Mapping[str, Any], questions: Mapping[str, Question]) -> None:
        super().__init__("captured")
        self.state = state
        self.questions = questions


class _CapturingJev(JevClient):
    """Stops a production attention function at its Jev call, keeping what it would send."""

    def __init__(self) -> None:
        super().__init__(api_key="unused", model_id="typesafe-ai/jev", timeout_seconds=1.0)

    async def judge(self, *, state: Any, questions: Mapping[str, Question]) -> JevBatchResult:
        raise _Captured(state, questions)


class _ConversationBundle:
    """Just enough of a context bundle for `conversation_state` and `bundle_state`."""

    def __init__(self, *, items: Sequence[Mapping[str, Any]]) -> None:
        self.bundle_id = _PROVENANCE["context_bundle_id"]
        self.context_revision = _PROVENANCE["context_revision"]
        self.digest = _PROVENANCE["digest"]
        self.cutoff_at = datetime.fromisoformat(_PROVENANCE["cutoff_at"])
        self.included = tuple(_ItemView(item) for item in items)
        self.omitted: tuple[object, ...] = ()
        self.client_id = self.product_signal_id = self.interview_session_id = None

    def section(self, section: str) -> tuple[_ItemView, ...]:
        return tuple(item for item in self.included if item.section == section)


class _ItemView:
    def __init__(self, item: Mapping[str, Any]) -> None:
        self.section = item["section"]
        self.source_kind = item.get("source_kind", "voice_transcript_turn")
        self.source_id = item.get("source_id", "replay")
        self.source_revision = item.get("source_revision", "1")
        self.body = item["body"]


async def _production_request(call: Any) -> tuple[dict[str, Any], Mapping[str, Question]]:
    try:
        await call
    except _Captured as captured:
        return dict(captured.state), captured.questions
    raise AssertionError("the production function did not reach its Jev call")


def _acknowledgement_request(moment: Mapping[str, Any]) -> Any:
    """The production acknowledgement question over a moment's activity.

    A moment already holds the conversation fields `conversation_state` would build
    from turns, so the bundle is empty and the caller sets them on the captured state.
    """
    return attention.judge_acknowledgement(
        None,  # type: ignore[arg-type]
        _CapturingJev(),
        bundle=_ConversationBundle(items=()),  # type: ignore[arg-type]
        activity=moment["just_did"],
    )


async def acknowledgement_question(
    moment: Mapping[str, Any],
) -> tuple[dict[str, Any], Mapping[str, Question]]:
    """The state and question production sends Jev for this moment, verbatim."""
    state, questions = await _production_request(_acknowledgement_request(moment))
    state.update(
        earlier=moment["earlier"],
        participant_last_said=moment["participant_last_said"],
        agent_last_said=moment["agent_last_said"],
    )
    return state, questions


async def checks_question(
    moment: Mapping[str, Any],
) -> tuple[dict[str, Any], Mapping[str, Question]]:
    """The state and questions the removed `judge_checks_shown` sent Jev."""
    return measured.checks_question(moment, _PROVENANCE)


async def classification_question(
    moment: Mapping[str, Any],
) -> tuple[dict[str, Any], Mapping[str, Question]]:
    state, questions = await _production_request(
        attention.propose_flow_classification(
            None,  # type: ignore[arg-type]
            _CapturingJev(),
            bundle=_ConversationBundle(items=moment["context"]),  # type: ignore[arg-type]
        )
    )
    state["context"] = moment["context"]
    return state, questions


def _accepted(
    question_id: str, choice: str, confidence: float, *, options: Sequence[str], floor: float, no_match: set[str]
) -> bool:
    return check_choice(
        question_id=question_id,
        choice=choice,
        confidence=confidence,
        supplied_options=options,
        threshold=ChoiceThreshold(
            question_id=question_id, minimum_confidence=floor, no_match_options=frozenset(no_match)
        ),
    ).accepted


async def _jev(
    jev: JevClient, state: Mapping[str, Any], questions: Mapping[str, Question]
) -> tuple[JevBatchResult, float]:
    started = time.perf_counter()
    batch = await jev.judge(state=dict(state), questions=dict(questions))
    return batch, time.perf_counter() - started


async def jev_acknowledgement(jev: JevClient, moment: Mapping[str, Any]) -> Verdict:
    state, questions = await acknowledgement_question(moment)
    batch, seconds = await _jev(jev, state, questions)
    (question_id,) = questions
    answer = batch.choices[question_id]
    accepted = _accepted(
        question_id,
        answer.choice,
        answer.confidence,
        options=attention._ACKNOWLEDGEMENT_OPTIONS,
        floor=attention._ACKNOWLEDGEMENT_CONFIDENCE,
        no_match={"stay_quiet", "unclear"},
    )
    return Verdict(
        choices={"acknowledge": answer.choice},
        confidences={"acknowledge": answer.confidence},
        decided={"acknowledge": "acknowledge" if accepted else "stay_quiet"},
        seconds=seconds,
        input_tokens=batch.input_tokens,
        output_tokens=batch.output_tokens,
    )


async def jev_checks(jev: JevClient, moment: Mapping[str, Any]) -> Verdict:
    state, questions = await checks_question(moment)
    batch, seconds = await _jev(jev, state, questions)
    choices: dict[str, str] = {}
    confidences: dict[str, float] = {}
    decided: dict[str, str] = {}
    for check in moment["checks"]:
        answer = batch.choices.get(f"check_shown::{check}")
        if answer is None:
            decided[check] = "none"
            continue
        choices[check], confidences[check] = answer.choice, answer.confidence
        accepted = _accepted(
            f"check_shown::{check}",
            answer.choice,
            answer.confidence,
            options=measured.CHECK_SHOWN_OPTIONS,
            floor=measured.CHECK_SHOWN_CONFIDENCE,
            no_match={"not_shown", "unclear"},
        )
        decided[check] = answer.choice if accepted else "none"
    return Verdict(
        choices=choices,
        confidences=confidences,
        decided=decided,
        seconds=seconds,
        input_tokens=batch.input_tokens,
        output_tokens=batch.output_tokens,
    )


async def jev_classification(jev: JevClient, moment: Mapping[str, Any]) -> Verdict:
    state, questions = await classification_question(moment)
    batch, seconds = await _jev(jev, state, questions)
    (question_id,) = questions
    answer = batch.choices[question_id]
    accepted = _accepted(
        question_id,
        answer.choice,
        answer.confidence,
        options=attention._FLOW_OPTIONS,
        floor=attention._CLASSIFICATION_CONFIDENCE,
        no_match={"unclear"},
    )
    return Verdict(
        choices={"flow": answer.choice},
        confidences={"flow": answer.confidence},
        decided={"flow": answer.choice if accepted else "unresolved"},
        seconds=seconds,
        input_tokens=batch.input_tokens,
        output_tokens=batch.output_tokens,
    )


def _question_text(question: Question) -> str:
    """One Choice question as prose: its instructions and each option's criterion."""
    if not isinstance(question, Choice):
        raise TypeError("only Choice questions are replayed")
    criteria = question.criteria or {}
    options = "\n".join(f"- {option}: {criterion}" for option, criterion in criteria.items())
    return f"{question.instructions}\n\nAnswer with exactly one of these options:\n{options}"


async def sol_judge(
    client: AsyncOpenAI,
    kind: str,
    moment: Mapping[str, Any],
) -> Verdict:
    """Sol answers the production question(s) for one moment in one structured request.

    `kind` is `acknowledgements`, `checks`, or `classifications`.
    """
    if kind == "acknowledgements":
        state, questions = await acknowledgement_question(moment)
    elif kind == "checks":
        state, questions = await checks_question(moment)
    else:
        state, questions = await classification_question(moment)
    state = {name: value for name, value in state.items() if name not in _PROVENANCE}
    keys = {f"q{index}": question_id for index, question_id in enumerate(questions)}
    schema = {
        "type": "object",
        "properties": {
            key: {"type": "string", "enum": list((questions[qid].criteria or {}).keys())}  # type: ignore[union-attr]
            for key, qid in keys.items()
        },
        "required": list(keys),
        "additionalProperties": False,
    }
    prose = "\n\n".join(f"Question {key}:\n{_question_text(questions[qid])}" for key, qid in keys.items())
    started = time.perf_counter()
    response = await client.responses.create(
        model=SOL.model_id,
        instructions=(
            "You judge one live product interview from the supplied state. Answer every "
            "question independently, from the state alone, by choosing one of its options. "
            "Reply only with the JSON object.\n\n" + prose
        ),
        input=[{"role": "user", "content": json.dumps(state, ensure_ascii=False)}],
        reasoning={"effort": SOL.effort.value},
        service_tier=SOL.service_tier.value,
        text={"format": {"type": "json_schema", "name": "answers", "schema": schema, "strict": True}},
        max_output_tokens=4000,
        store=False,
    )
    seconds = time.perf_counter() - started
    answers = json.loads(response.output_text)
    by_question = {keys[key]: value for key, value in answers.items()}
    if kind == "acknowledgements":
        choice = next(iter(by_question.values()))
        choices = {"acknowledge": choice}
        decided = {"acknowledge": "acknowledge" if choice == "acknowledge" else "stay_quiet"}
    elif kind == "checks":
        choices = {qid.removeprefix("check_shown::"): value for qid, value in by_question.items()}
        decided = {
            check: value if value in ("shown", "contradicted") else "none"
            for check, value in choices.items()
        }
    else:
        choice = next(iter(by_question.values()))
        choices = {"flow": choice}
        decided = {"flow": choice if choice != "unclear" else "unresolved"}
    return _with_usage(Verdict(choices=choices, decided=decided, seconds=seconds), response)


def _with_usage(verdict: Verdict, response: Any) -> Verdict:
    usage = response.usage
    if usage is None:
        return verdict
    cached = getattr(usage.input_tokens_details, "cached_tokens", 0) or 0
    verdict.input_tokens = usage.input_tokens
    verdict.cached_tokens = cached
    verdict.output_tokens = usage.output_tokens
    verdict.cost_usd = cost_usd(
        SOL,
        TokenUsage(
            input_tokens=usage.input_tokens,
            cached_input_tokens=cached,
            cache_write_input_tokens=0,
            output_tokens=usage.output_tokens,
        ),
    )
    return verdict


_PRODUCT_KNOWLEDGE: Final = (
    "Shopalytics is an e-commerce analytics product. Its conversion dashboard filters "
    "sessions by date range, channel, customer segment, review topic, review sentiment, "
    "product, and conversion state, and shows conversion KPIs, a conversion trend, a "
    "product breakdown, and the matching reviews."
)


def agent_instructions() -> str:
    """The voice agent's instructions as a Saved Conversion Views session renders them."""
    preview = datetime(2026, 11, 1, tzinfo=UTC)
    return _INSTRUCTIONS.format(
        agent_name="Wren",
        client_name="Shopalytics",
        purpose="analyze the feature request",
        product_name="Shopalytics",
        discovery=_DISCOVERY_BY_FLOW["feature_request_flow"],
        preview_date=f"{preview:%A}, {preview:%B} {preview.day}, {preview.year}",
        preview_day=f"{preview:%B} {preview.day}",
        implementation_limit=3,
        product_knowledge=_PRODUCT_KNOWLEDGE,
    )


def _turns(moment: Mapping[str, Any]) -> list[dict[str, str]]:
    """The moment's conversation as the agent's context holds it: speakers as roles."""
    messages: list[dict[str, str]] = []
    for part in re.split(r"(?=\b(?:Agent|Participant): )", moment["earlier"]):
        part = part.strip()
        if part.startswith("Agent: "):
            messages.append({"role": "assistant", "content": part.removeprefix("Agent: ")})
        elif part.startswith("Participant: "):
            messages.append({"role": "user", "content": part.removeprefix("Participant: ")})
    if moment["participant_last_said"]:
        messages.append({"role": "user", "content": moment["participant_last_said"]})
    if moment["agent_last_said"]:
        messages.append({"role": "assistant", "content": moment["agent_last_said"]})
    return messages


async def sol_agent_acknowledgement(client: AsyncOpenAI, moment: Mapping[str, Any]) -> Verdict:
    """The voice agent's own reply when the moment is appended for it to decide on."""
    activity = "In the preview, the person just did this, oldest first: " + "; ".join(
        moment["just_did"]
    )
    started = time.perf_counter()
    response = await client.responses.create(
        model=SOL.model_id,
        instructions=agent_instructions(),
        input=[
            *_turns(moment),  # type: ignore[list-item]
            {
                "role": "user",
                "content": f"<application>{activity}. {ACKNOWLEDGE_IF_AWAITED}</application>",
            },
        ],
        reasoning={"effort": SOL.effort.value},
        service_tier=SOL.service_tier.value,
        max_output_tokens=2000,
        store=False,
    )
    seconds = time.perf_counter() - started
    said = response.output_text.strip()
    spoke = bool(said) and not _BACKCHANNEL.match(said)
    verdict = Verdict(
        choices={"acknowledge": "acknowledge" if spoke else "stay_quiet"},
        decided={"acknowledge": "acknowledge" if spoke else "stay_quiet"},
        seconds=seconds,
        said=said,
    )
    return _with_usage(verdict, response)


_COMPLETIONS: Final = ("saved view created", "saved view reopened", "the page loaded")
_ANNOUNCED: Final = re.compile(
    r"\b(let me|i'll|i will|watch|now i|going to|gonna|loading|one sec)\b", re.IGNORECASE
)


def rule_acknowledgement(moment: Mapping[str, Any], *, conversation: bool) -> Verdict:
    """Acknowledge a completion (a save, a reopen, a page load), never a step.

    With `conversation`, only when the agent's last words asked for something or the
    participant's last words announced it.
    """
    completed = any(line.startswith(_COMPLETIONS) for line in moment["just_did"])
    awaited = (
        moment["agent_last_said"].rstrip().endswith("?")
        or "?" in moment["earlier"][-200:]
        or bool(_ANNOUNCED.search(moment["participant_last_said"]))
    )
    acknowledge = completed and (awaited or not conversation)
    choice = "acknowledge" if acknowledge else "stay_quiet"
    return Verdict(choices={"acknowledge": choice}, decided={"acknowledge": choice}, seconds=0.0)


_FILTERS: Final = re.compile(r"^conversion filters applied \((.*)\)$")


def _filters(line: str) -> dict[str, str] | None:
    """The full filter set a `conversion filters applied` line names, if it names all of them."""
    match = _FILTERS.match(line)
    if match is None or "→" in line:
        return None
    pairs = [part.strip() for part in match.group(1).split(", ")]
    values: dict[str, str] = {}
    for pair in pairs:
        name, _, value = pair.rpartition(" ")
        for known in ("date range", "channels", "segments", "review topics", "conversion states", "products", "sentiments"):
            if pair.startswith(known + " "):
                name, value = known, pair.removeprefix(known + " ")
        values[name] = value
    return values


def rule_checks(moment: Mapping[str, Any]) -> Verdict:
    """The demo's checks read off its milestones: save, refresh then reopen, relative range.

    Specific to Saved Conversion Views: a check is matched to a rule by its words.
    """
    activity: list[str] = list(moment["activity"])
    saved_at = next((i for i, line in enumerate(activity) if line.startswith("saved view created")), None)
    saved_filters = None
    if saved_at is not None:
        saved_filters = next(
            (f for line in reversed(activity[:saved_at]) if (f := _filters(line)) is not None), None
        )
    reloaded_at = next(
        (i for i, line in enumerate(activity) if saved_at is not None and i > saved_at and line == "the page loaded"),
        None,
    )
    reopened_at = next(
        (
            i
            for i, line in enumerate(activity)
            if reloaded_at is not None and i > reloaded_at and line.startswith("saved view reopened")
        ),
        None,
    )
    restored = None
    if reopened_at is not None:
        restored = next((f for line in activity[reopened_at:] if (f := _filters(line)) is not None), None)
    decided: dict[str, str] = {}
    for check, statement in moment["checks"].items():
        words = statement.lower()
        if "relative" in words or "rolling" in words:
            if restored is None:
                decided[check] = "none"
            else:
                decided[check] = "shown" if restored.get("date range") == "last_30_days" else "contradicted"
        elif "reopen" in words or "restore" in words:
            if restored is None:
                decided[check] = "none"
            else:
                decided[check] = "shown" if restored == saved_filters else "contradicted"
        elif "available after" in words or "still available" in words:
            decided[check] = "shown" if reopened_at is not None else "none"
        elif "save" in words and "name" in words:
            decided[check] = "shown" if saved_at is not None else "none"
        else:
            decided[check] = "none"
    return Verdict(choices=dict(decided), decided=decided, seconds=0.0)
