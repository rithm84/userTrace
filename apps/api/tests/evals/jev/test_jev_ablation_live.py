"""Live: one scripted interview, with Jev deciding acknowledgements or Sol deciding them.

`UTRACE_JEV_ABLATION` picks the arm, and the evaluation changes nothing in the
product: the "off" arms patch the running agent from here. In every arm the agent
judges from the activity which agreed checks the new version already showed, as
production does since this evaluation removed Jev's checks-shown judgment; the runs
recorded in `docs/arch-components/benchmarks.md` predate that, when the `jev` arm
also had Jev report shown checks and the other arms patched it out.

- `jev`: production as it is. Jev decides which moments are acknowledged.
- `sol`: no Jev call. Every moment the floor allows is appended for the agent to
  speak to with `judges.ACKNOWLEDGE_IF_AWAITED`, so Sol, the agent itself, decides
  whether to say a few words or nothing. The 15-second spacing between
  acknowledgements goes, since the agent may answer a moment with nothing.
- `quiet`: no Jev call and no acknowledgements at all.

The participant is scripted, the same in every arm: they say they will open the
dashboard and it loads; set three filters while naming each; ask for the change,
say what it is for, and agree. Once the new version loads and is debriefed, they
say only "Okay, let me try it", then rebuild the filters, save, refresh, and reopen
without a word, and say "Looks like it all came back". Every question after that
gets the same answer. The run writes everything to `UTRACE_EVAL_RESULTS_DIR`
(default `tmp/jev-eval/ablation` at the repository root) and asserts nothing about
quality: its numbers go into `docs/arch-components/benchmarks.md`.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from openai import AsyncOpenAI
from pipecat.metrics.metrics import LLMTokenUsage
from pipecat.services.openai.responses.llm import OpenAIResponsesLLMService

from app.connectors.typesafe_jev import JevBatchResult, JevClient
from app.db.pool import Database
from app.interview.ports import RegisteredTarget
from app.interview.voice import acknowledgements, activity_feed
from app.interview.voice.acknowledgements import Acknowledgements
from app.interview.voice.judge import VoiceJudge
from tests.conftest import require_env
from tests.evals.jev.judges import ACKNOWLEDGE_IF_AWAITED
from tests.gate.factories import approve_agreed_change
from tests.interview import realtime_harness as harness
from tests.interview.fixtures import ObservedDocument, record_observation
from tests.interview.simulated_interview import _filters_applied, _rendered, _validation_settled
from tests.supabase_auth import SignedInUser

_OPENING: Final = (
    "Hi! So every Monday I rebuild the same conversion filters, which is a pain. "
    "Let me pull up my conversion dashboard real quick."
)
_WALK: Final[tuple[tuple[str, str, str, str, str], ...]] = (
    ("Okay. So first, TikTok for the channel,", "channel", "TikTok", "channels", "TikTok"),
    ("then women twenty five to thirty four for the segment,", "segment", "Women 25-34", "segments", "Women 25-34"),
    ("and, uh, sizing for the review topic.", "review_topic", "Sizing", "review_topics", "Sizing"),
)
_ASK: Final = (
    "So that's it, every Monday. It would be great to save it as a view and reopen it in "
    "one click."
)
_PURPOSE: Final = (
    "Mostly whether the sizing complaints are hurting TikTok conversion for that group, "
    "week to week. And yes, keep all of those filters, with last 30 days rolling."
)
_AGREE: Final = "That works for me, nothing to add. Go ahead."
_TRY: Final = "Okay, let me try it."
_DONE: Final = "Looks like it all came back."
_ANSWER: Final = "Yes, everything matched, and it still says last 30 days."
#: The filters they rebuild in the new version, in order, as each control then shows them.
_REBUILD: Final[tuple[tuple[str, str, str, str], ...]] = (
    ("channel", "TikTok", "channels", "TikTok"),
    ("segment", "Women 25-34", "segments", "Women 25-34"),
    ("review_topic", "Sizing", "review_topics", "Sizing"),
)
_VIEW_NAME: Final = "monday sizing"
#: The dashboard's filters as it opens (`realtime_harness.open_page`). Every filter
#: milestone carries the whole set, as the product's does, so a reopen that restores
#: exactly what was saved reads as exactly that.
_OPENED: Final[Mapping[str, str]] = {
    "date_range": "last_30_days",
    "channels": "all",
    "segments": "all",
    "review_topics": "all",
    "sentiments": "all",
    "products": "all",
}
_PAUSE_SECONDS: Final[float] = 1.6
_SPACING_SECONDS: Final[float] = 15.0
_AGREEMENT_TURNS: Final[int] = 4
_VALIDATION_TURNS: Final[int] = 5
_CUE_DEADLINE_SECONDS: Final[float] = 30.0
_ARMS: Final = ("jev", "sol", "quiet")


def _results_dir() -> Path:
    configured = os.environ.get("UTRACE_EVAL_RESULTS_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[5] / "tmp" / "jev-eval" / "ablation"


def _patch_arm(arm: str, monkeypatch: pytest.MonkeyPatch, record: dict[str, Any]) -> None:
    """Switch the running agent to an arm, and record every model call either way."""
    llm_usage: list[dict[str, Any]] = record.setdefault("llm_usage", [])
    jev_calls: list[dict[str, Any]] = record.setdefault("jev_calls", [])
    moments: list[dict[str, Any]] = record.setdefault("acknowledgement_moments", [])

    original_usage = OpenAIResponsesLLMService.start_llm_usage_metrics

    async def usage(self: OpenAIResponsesLLMService, tokens: LLMTokenUsage) -> None:
        llm_usage.append({"at": time.time(), **tokens.model_dump()})
        await original_usage(self, tokens)

    monkeypatch.setattr(OpenAIResponsesLLMService, "start_llm_usage_metrics", usage)

    original_judge = JevClient.judge

    async def judge(self: JevClient, *, state: Any, questions: Any) -> JevBatchResult:
        started = time.time()
        batch = await original_judge(self, state=state, questions=questions)
        jev_calls.append(
            {
                "at": started,
                "seconds": time.time() - started,
                "questions": list(questions),
                "input_tokens": batch.input_tokens,
                "output_tokens": batch.output_tokens,
            }
        )
        return batch

    monkeypatch.setattr(JevClient, "judge", judge)

    original_worth = Acknowledgements.worth_saying

    async def worth_saying(self: Acknowledgements, activity: Sequence[str], *, read_at: float) -> bool:
        asked = time.time()
        spoken = await original_worth(self, activity, read_at=read_at)
        moments.append({"at": asked, "activity": list(activity), "sent_speakably": spoken})
        return spoken

    monkeypatch.setattr(Acknowledgements, "worth_saying", worth_saying)

    if arm == "jev":
        return

    async def every_moment(self: VoiceJudge, *, subject: Any, activity: Sequence[str]) -> bool:
        return arm == "sol"

    monkeypatch.setattr(VoiceJudge, "acknowledgement", every_moment)
    if arm == "sol":
        monkeypatch.setattr(acknowledgements, "_SPACING_SECONDS", 0.0)
        monkeypatch.setattr(activity_feed, "_ACKNOWLEDGE", ACKNOWLEDGE_IF_AWAITED)


async def _agreed(database: Database, product_signal_id: Any) -> bool:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select 1 from public.acceptance_criteria_agreements where product_signal_id = %s",
            (product_signal_id,),
        )
        return await cur.fetchone() is not None


async def _click(
    database: Database, document: ObservedDocument, *, sequence: int, target_id: str, description: str
) -> int:
    await record_observation(
        database,
        document=document,
        export_sequence=sequence,
        observation_type="interaction.activation",
        target=RegisteredTarget(
            target_id=target_id,
            target_revision=1,
            description=description,
            placement="top right of the screen",
            role="button",
            visible=True,
        ),
        payload={"role": "button", "actor": "user", "activation_kind": "pointer"},
    )
    return sequence + 1


async def _milestone(
    database: Database, document: ObservedDocument, *, sequence: int, payload: Mapping[str, object]
) -> int:
    await record_observation(
        database,
        document=document,
        export_sequence=sequence,
        observation_type="product.milestone",
        target=None,
        payload=payload,
    )
    return sequence + 1


async def _session_records(database: Database, *, session_id: Any, product_signal_id: Any) -> dict[str, Any]:
    """What the run left in the database: criteria, validation, judgements."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select criteria_revision, ordinal, statement from public.validation_criteria "
            "where product_signal_id = %s order by criteria_revision, ordinal",
            (product_signal_id,),
        )
        criteria = [dict(row) for row in await cur.fetchall()]
        await cur.execute(
            "select id, state::text, outcome::text, created_at, completed_at "
            "from public.validation_attempts where interview_session_id = %s order by created_at",
            (session_id,),
        )
        attempts = [dict(row) for row in await cur.fetchall()]
        await cur.execute(
            "select o.result::text, o.user_confirmed, o.reason, c.statement "
            "from public.validation_outcomes o join public.validation_criteria c "
            "on c.id = o.validation_criterion_id where o.validation_attempt_id in "
            "(select id from public.validation_attempts where interview_session_id = %s)",
            (session_id,),
        )
        outcomes = [dict(row) for row in await cur.fetchall()]
        await cur.execute(
            "select created_at, purpose, accepted, answers, acceptance "
            "from public.semantic_judgements where interview_session_id = %s order by created_at",
            (session_id,),
        )
        judgements = [dict(row) for row in await cur.fetchall()]
    return {
        "criteria": criteria,
        "validation_attempts": attempts,
        "validation_outcomes": outcomes,
        "judgements": judgements,
    }


async def test_one_interview_with_or_without_jev(
    database: Database, auth_users: dict[str, SignedInUser], monkeypatch: pytest.MonkeyPatch
) -> None:
    arm = os.environ.get("UTRACE_JEV_ABLATION")
    if arm is None:
        pytest.skip("set UTRACE_JEV_ABLATION to jev, sol, or quiet to run the Jev ablation")
    if arm not in _ARMS:
        raise ValueError(f"UTRACE_JEV_ABLATION must be one of {_ARMS}")
    api_key = require_env("OPENAI_API_KEY")
    record: dict[str, Any] = {"arm": arm, "started_at": datetime.now(tz=UTC).isoformat()}
    _patch_arm(arm, monkeypatch, record)
    events: dict[str, float] = {}
    spoken: list[tuple[str, float, float]] = []
    report = harness.Report()
    async with AsyncOpenAI(api_key=api_key) as client:
        lines = [_OPENING, *(step[0] for step in _WALK), _ASK, _PURPOSE, _AGREE, _TRY, _DONE, _ANSWER]
        audio = dict(zip(lines, await asyncio.gather(*(harness.speech(client, line) for line in lines)), strict=True))

    async def say(call: harness.LiveCall, line: str) -> float:
        started, ended = await harness.say(call.microphone, audio[line])
        spoken.append((line, started, ended))
        return ended

    async with harness.live_call(database, auth_users, report=report, monkeypatch=monkeypatch) as call:
        session_id, signal_id = call.session_id, call.case.product_signal_id
        events["connected"] = call.connected_at
        base = await harness.session_allocation(database, session_id)
        await harness.required_reply(database, session_id, after=call.connected_at)

        # Discovery: the dashboard they said they would open, then a walkthrough.
        opening_end = await say(call, _OPENING)
        await harness.agent_reply(
            database, session_id=session_id, after=opening_end, deadline_seconds=harness.INTRO_REPLY_SECONDS
        )
        dashboard = await harness.open_page(
            database, call, allocation_id=base, controls=harness.FILTER_CONTROLS, binding_allocation=None
        )
        events["dashboard_loaded"] = time.time()
        await harness.agent_reply(database, session_id=session_id, after=events["dashboard_loaded"], deadline_seconds=6.0)
        await asyncio.sleep(max(0.0, events["dashboard_loaded"] + _SPACING_SECONDS - time.time()))
        events["walkthrough_started"] = time.time()
        sequence = 5
        applied = dict(_OPENED)
        for line, control, label, attribute, value in _WALK:
            await say(call, line)
            applied[attribute] = value
            sequence = await harness.apply_filter(
                database, dashboard, export_sequence=sequence, control=control, label=label, applied=applied
            )
            await asyncio.sleep(_PAUSE_SECONDS)
        after = await say(call, _ASK)
        events["asked"] = after

        # Agreement.
        for turn in range(_AGREEMENT_TURNS):
            await harness.whole_turn(database, session_id, after=after)
            if await _agreed(database, signal_id):
                break
            after = await say(call, _PURPOSE if turn == 0 else _AGREE)
            for _ in range(100):
                if await _agreed(database, signal_id):
                    break
                await asyncio.sleep(0.1)
            if await _agreed(database, signal_id):
                await harness.required_reply(database, session_id, after=after)
                break
        record["agreed"] = await _agreed(database, signal_id)
        if not record["agreed"]:
            raise AssertionError("the agreement was never recorded")
        events["agreed"] = time.time()

        # The gate approves and the new version is built, then loads and is debriefed.
        await asyncio.sleep(3.0)
        async with database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                "update public.product_signals set state = 'gate_pending' "
                "where id = %s and state = 'interviewing'",
                (signal_id,),
            )
        attempt_id = await approve_agreed_change(
            database,
            client_id=call.case.client_id,
            product_signal_id=signal_id,
            configuration_release_id=call.case.configuration_release_id,
        )
        await harness.set_progress(database, session_id, "making_changes")
        await asyncio.sleep(8.0)
        candidate = await harness.ready_candidate(database, call, attempt_id=attempt_id)
        await harness.leave_page(database, session_id)
        events["ready"] = time.time()
        await harness.set_progress(database, session_id, "ready_for_validation")
        await harness.whole_turn(database, session_id, after=events["ready"])
        page = await harness.open_page(
            database,
            call,
            allocation_id=candidate,
            controls=(*harness.FILTER_CONTROLS, *harness.NEW_CONTROLS),
            binding_allocation=candidate,
        )
        await harness.report_page(database, call, page)
        events["candidate_loaded"] = time.time()
        await harness.whole_turn(database, session_id, after=events["candidate_loaded"])
        events["debriefed"] = time.time()

        # Validation: they try it without narrating, then say how it went.
        await say(call, _TRY)
        sequence, applied = 5, dict(_OPENED)
        for control, label, attribute, value in _REBUILD:
            applied[attribute] = value
            sequence = await harness.apply_filter(
                database, page, export_sequence=sequence, control=control, label=label, applied=applied
            )
            await asyncio.sleep(0.9)
        saved_filters = dict(applied)
        sequence = await _click(
            database, page, sequence=sequence, target_id="saved_view_save_button",
            description='saved_view_save_button showing "Save view"',
        )
        sequence = await _milestone(
            database, page, sequence=sequence,
            payload={"milestone_id": "saved_view_created", "attributes": [{"name": "view_name", "value": {"value": _VIEW_NAME}}]},
        )
        events["saved"] = time.time()
        await asyncio.sleep(3.0)
        reloaded = await harness.open_page(
            database,
            call,
            allocation_id=candidate,
            controls=(*harness.FILTER_CONTROLS, *harness.NEW_CONTROLS),
            binding_allocation=candidate,
        )
        events["reloaded"] = time.time()
        await asyncio.sleep(2.5)
        sequence = await _click(
            database, reloaded, sequence=5, target_id="saved_view_selector",
            description='saved_view_selector showing "Saved views"',
        )
        for payload in (
            {"milestone_id": "saved_view_reopened", "attributes": [{"name": "view_name", "value": {"value": _VIEW_NAME}}]},
            _filters_applied(saved_filters),
            _rendered("review_list", 56),
            _rendered("conversion_trend", 30),
        ):
            sequence = await _milestone(database, reloaded, sequence=sequence, payload=payload)
        events["reopened"] = time.time()
        await asyncio.sleep(3.0)
        after = await say(call, _DONE)
        events["said_done"] = after
        questions: list[str] = []
        # Every question gets the same answer; anything else is let be.
        for _ in range(_VALIDATION_TURNS):
            heard = await harness.agent_reply(database, session_id=session_id, after=after, deadline_seconds=10.0)
            if await _validation_settled(database, session_id):
                break
            if heard is not None and heard[1].rstrip().endswith("?"):
                questions.append(heard[1].strip())
                after = await say(call, _ANSWER)
            else:
                after = time.time()
        for _ in range(120):
            if await _validation_settled(database, session_id):
                break
            await asyncio.sleep(0.25)
        events["validation_settled"] = time.time() if await _validation_settled(database, session_id) else float("nan")
        await harness.agent_reply(database, session_id=session_id, after=time.time(), deadline_seconds=8.0)
        words = await harness.words(database, session_id, after=call.connected_at, before=time.time() + 1.0)

    record.update(await _session_records(database, session_id=session_id, product_signal_id=signal_id))
    record["questions_after_done"] = questions
    record["events"] = events
    record["participant_lines"] = spoken
    record["words"] = words
    record["appends"] = report.appends
    record["character"] = report.character
    out = _results_dir()
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{arm}-{datetime.now(tz=UTC):%Y%m%dT%H%M%S}.json"
    path.write_text(json.dumps(record, default=str, indent=1), encoding="utf-8")

    start = call.connected_at
    print(f"\njev ablation, arm {arm}: written to {path}")
    print(f"  validation: {[(a['state'], a['outcome']) for a in record['validation_attempts']]}")
    print(f"  questions after they said it came back: {questions}")
    print(f"  jev calls {len(record['jev_calls'])}, llm runs {len(record['llm_usage'])}")
    for at, kind, content in report.appends:
        print(f"    +{at - start:6.2f}  {kind}: {content[:140]!r}")
    for at, speaker, text in words:
        print(f"    +{at - start:6.2f}  {speaker}: {text!r}")
