"""Live: the agent acknowledges what the participant was waiting on, once, and nothing else.

A real voice call (see `realtime_harness`) runs three moments of the Saved
Conversion Views interview:

1. The participant says they will pull up their conversion dashboard, and it
   loads. Jev should judge that worth acknowledging, and the agent should say so in
   a few words within about two seconds.
2. They set their filters one at a time while naming them. No step is
   acknowledged, and the agent at most backchannels.
3. A new version is ready: the agent says it is loading, and once it has loaded the
   debrief walks them through it. That debrief is the load's only acknowledgement.

It reports each delay, from the committed activity to the speakable append and to
the agent's first words. It costs about two minutes of voice plus a few Jev
calls, so it runs only when `UTRACE_REALTIME_E2E=1`.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Sequence
from typing import Final
from uuid import UUID

import pytest
from openai import AsyncOpenAI
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import ImplementationAttemptState

from app.db.pool import Database
from app.ids import uuid7
from tests.conftest import require_env
from tests.factories import create_gate_decision
from tests.interview import realtime_harness as harness
from tests.supabase_auth import SignedInUser

_OPENING = (
    "Hi! So every Monday I rebuild the same conversion filters, which is a pain. "
    "Let me pull up my conversion dashboard real quick."
)
#: Each step: what they say, the control they set while pausing, its label, and the
#: milestone attribute it adds.
_STEPS: Final[tuple[tuple[str, str, str, str, str], ...]] = (
    ("Okay. So first, TikTok for the channel,", "channel", "TikTok", "channels", "TikTok"),
    ("then women twenty five to thirty four for the segment,", "segment", "Women 25-34", "segments", "Women 25-34"),
    ("and, uh, sizing for the review topic.", "review_topic", "Sizing", "review_topics", "Sizing"),
)
_PAUSE_SECONDS: Final[float] = 1.6
_ASK = (
    "So that's it, every Monday. It would be great to save it as a view and reopen it "
    "in one click."
)
_CRITERIA: Final[tuple[str, ...]] = (
    "Saving the current filters under a name keeps them as a saved view.",
    "After a refresh, reopening the saved view restores every saved filter.",
)
_BACKCHANNEL_WORDS: Final[int] = 3
_UTTERANCE_GAP_SECONDS: Final[float] = 1.0
_ACKNOWLEDGED: Final[str] = "Acknowledge it out loud"
_DEBRIEF: Final[str] = "The new version has loaded"
_READY: Final[str] = "The new version is ready"
_CUE_DEADLINE_SECONDS: Final[float] = 15.0
_SETTLE_SECONDS: Final[float] = 8.0
#: Acknowledgements are spaced apart; the walkthrough starts after that spacing, so
#: its steps are left to Jev's judgement rather than to the spacing.
_SPACING_SECONDS: Final[float] = 15.0


async def test_the_agent_acknowledges_what_was_awaited_once_and_narrates_nothing_else(
    database: Database, auth_users: dict[str, SignedInUser], monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.environ.get("UTRACE_REALTIME_E2E") != "1":
        pytest.skip("set UTRACE_REALTIME_E2E=1 to run the live realtime voice check")
    api_key = require_env("OPENAI_API_KEY")
    report = harness.Report()
    async with AsyncOpenAI(api_key=api_key) as client:
        opening, ask, *steps = await asyncio.gather(
            harness.speech(client, _OPENING),
            harness.speech(client, _ASK),
            *(harness.speech(client, step[0]) for step in _STEPS),
        )

    async with harness.live_call(
        database, auth_users, report=report, monkeypatch=monkeypatch
    ) as call:
        session_id = call.session_id
        base = await harness.session_allocation(database, session_id)
        await harness.required_reply(database, session_id, after=call.connected_at)

        # 1. The dashboard they said they would pull up comes up.
        _, opening_end = await harness.say(call.microphone, opening)
        await harness.agent_reply(
            database,
            session_id=session_id,
            after=opening_end,
            deadline_seconds=harness.INTRO_REPLY_SECONDS,
        )
        dashboard = await harness.open_page(
            database, call, allocation_id=base, controls=harness.FILTER_CONTROLS, binding_allocation=None
        )
        dashboard_at = time.time()
        dashboard_reply = await harness.agent_reply(
            database, session_id=session_id, after=dashboard_at, deadline_seconds=6.0
        )

        # 2. They set their filters one at a time, naming each.
        await asyncio.sleep(max(0.0, dashboard_at + _SPACING_SECONDS - time.time()))
        walk_start = time.time()
        sequence = 5
        applied: dict[str, str] = {"date_range": "last_30_days"}
        for pcm, (_, control, label, attribute, value) in zip(steps, _STEPS, strict=True):
            await harness.say(call.microphone, pcm)
            applied[attribute] = value
            sequence = await harness.apply_filter(
                database,
                dashboard,
                export_sequence=sequence,
                control=control,
                label=label,
                applied=applied,
            )
            await asyncio.sleep(_PAUSE_SECONDS)
        ask_start, ask_end = await harness.say(call.microphone, ask)
        await harness.required_reply(database, session_id, after=ask_end)
        while await harness.agent_reply(
            database, session_id=session_id, after=time.time(), deadline_seconds=_SETTLE_SECONDS
        ):
            pass

        # 3. A new version is ready, loads, and is debriefed.
        candidate = await _ready_candidate(database, call)
        await harness.leave_page(database, session_id)
        ready_at = time.time()
        await harness.set_progress(database, session_id, "ready_for_validation")
        ready_cue_at = await _appended(report, prefix=_READY, after=ready_at)
        loading_reply = await harness.required_reply(database, session_id, after=ready_cue_at)
        page = await harness.open_page(
            database,
            call,
            allocation_id=candidate,
            controls=(*harness.FILTER_CONTROLS, *harness.NEW_CONTROLS),
            binding_allocation=candidate,
        )
        await harness.report_page(database, call, page)
        loaded_at = time.time()
        debrief_at = await _appended(report, prefix=_DEBRIEF, after=loaded_at)
        debrief_reply = await harness.required_reply(database, session_id, after=debrief_at)
        await asyncio.sleep(_SETTLE_SECONDS)
        end = time.time()
        words = await harness.words(database, session_id, after=call.connected_at, before=end)

    acknowledgements = [
        (at, content)
        for at, kind, content in report.appends
        if kind == "say" and _ACKNOWLEDGED in content
    ]
    dashboard_acks = [at for at, _ in acknowledgements if dashboard_at <= at < walk_start]
    walk_acks = [at for at, _ in acknowledgements if walk_start <= at < ask_end]
    version_acks = [at for at, _ in acknowledgements if ready_at <= at]
    debriefs = [
        at
        for at, kind, content in report.appends
        if kind == "say" and content.startswith(_DEBRIEF) and at >= loaded_at
    ]
    walk_said = _utterances(words, after=walk_start, before=ask_start)
    speaking_after_dashboard = report.first_character_after("speaking", dashboard_at)

    print("\nacknowledgement report")
    print(f"  dashboard acknowledgements appended: {[round(at - dashboard_at, 2) for at in dashboard_acks]}s after it loaded")
    for at, content in acknowledgements:
        print(f"    +{at - call.connected_at:6.2f}  {content!r}")
    if dashboard_reply is None:
        print("  agent stayed silent after the dashboard loaded")
    else:
        print(f"  agent's first words {dashboard_reply[0] - dashboard_at:.2f}s after the dashboard loaded: {dashboard_reply[1]!r}")
    print(f"  agent audio began {None if speaking_after_dashboard is None else round(speaking_after_dashboard - dashboard_at, 2)}s after the dashboard loaded")
    print(f"  acknowledgements during the walkthrough: {len(walk_acks)}; agent said: {walk_said!r}")
    print(f"  new version: ready cue {ready_cue_at - ready_at:.2f}s after ready, then: {loading_reply[1]!r}")
    print(f"  debrief cue {debrief_at - loaded_at:.2f}s and words {debrief_reply[0] - loaded_at:.2f}s after it loaded: {debrief_reply[1]!r}")
    print(f"  acknowledgements while the new version was announced: {len(version_acks)}")
    print("  timeline (seconds after connect):")
    for at, speaker, text in words:
        print(f"    +{at - call.connected_at:6.2f}  {speaker}: {text!r}")
    for at, kind, content in report.appends:
        print(f"    +{at - call.connected_at:6.2f}  {kind}: {content[:120]!r}")

    assert len(dashboard_acks) == 1, "the dashboard they said they would open was not acknowledged once"
    assert dashboard_acks[0] - dashboard_at < 2.0
    assert dashboard_reply is not None and dashboard_reply[0] - dashboard_at < 3.0
    assert not walk_acks, "a walkthrough step was acknowledged"
    assert all(len(said.split()) <= _BACKCHANNEL_WORDS for said in walk_said), (
        f"the agent talked over the walkthrough: {walk_said!r}"
    )
    assert len(debriefs) == 1, "the new version's load was not debriefed once"
    assert not version_acks, "the new version's load was acknowledged besides its debrief"


async def _appended(report: harness.Report, *, prefix: str, after: float) -> float:
    """When the application appended speakable context starting with ``prefix``."""
    deadline = time.time() + _CUE_DEADLINE_SECONDS
    while time.time() < deadline:
        at = next(
            (
                at
                for at, kind, content in report.appends
                if kind == "say" and content.startswith(prefix) and at >= after
            ),
            None,
        )
        if at is not None:
            return at
        await asyncio.sleep(0.05)
    raise AssertionError(f"the application never appended {prefix!r}")


async def _ready_candidate(database: Database, call: harness.LiveCall) -> UUID:
    """Commit what agreement, the gate, and a finished attempt leave: a ready candidate.

    Returns the candidate's allocation, which the session's preview now serves.
    """
    case = call.case
    gate_id = await create_gate_decision(
        database, case=case, decision="approved_live", classification_revision=1, blocker_reason=None
    )
    attempt_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.implementation_attempts (
                id, client_id, product_signal_id, gate_decision_id, state,
                repository_ref, base_revision, consumed_budgets
            )
            values (%s, %s, %s, %s, %s, 'ShopalyticsStore/shopalytics', 'base', %s)
            """,
            (
                attempt_id,
                case.client_id,
                case.product_signal_id,
                gate_id,
                ImplementationAttemptState.AUTHORIZED.value,
                Jsonb({}),
            ),
        )
    allocation_id = await harness.ready_candidate(database, call, attempt_id=attempt_id)
    await harness.record_agreement(
        database,
        case=case,
        session_id=call.session_id,
        outcome="Save the current conversion filters as a named view and reopen it in one click.",
        criteria=_CRITERIA,
    )
    return allocation_id


def _utterances(
    words: Sequence[tuple[float, str, str]], *, after: float, before: float
) -> list[str]:
    """The agent's separate utterances between two moments."""
    utterances: list[list[tuple[float, str]]] = []
    for at, speaker, text in words:
        if speaker != "agent" or not after <= at < before:
            continue
        if utterances and at - utterances[-1][-1][0] < _UTTERANCE_GAP_SECONDS:
            utterances[-1].append((at, text))
        else:
            utterances.append([(at, text)])
    return ["".join(text for _, text in utterance).strip() for utterance in utterances]
