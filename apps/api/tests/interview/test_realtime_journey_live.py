"""Live: one interview from greeting to a ready new version, over a real GPT-Live call.

The participant opens the call, walks through their weekly filters in the preview,
asks to save them, and agrees to the change the agent proposes. The gate then
approves it, the new version becomes ready, and the call ends. The test checks the
conversation's shape (a recap that circles what it names, no promise of a build
before the gate approves, the build announced once it does, the ready version
announced) and the run's health: no failed Job, no stale cursor target, no
connection idling inside a transaction, and bounded telemetry readiness writes.

It costs a few minutes of voice plus Sol and Jev calls, so it runs only when
`UTRACE_REALTIME_E2E=1` (see `realtime_harness`).
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping
from typing import Final
from uuid import UUID

import pytest
from openai import AsyncOpenAI

from app.db.pool import Database
from tests.conftest import require_env
from tests.factories import create_ready_version
from tests.gate.factories import approve_agreed_change
from tests.interview import realtime_harness as harness
from tests.interview.test_realtime_walkthrough_live import _ASK, _OPENING, _PAUSE_SECONDS, _STEPS
from tests.supabase_auth import SignedInUser

_AGREE = "Yes, that's exactly it. Keep all of those filters, and keep last 30 days rolling."
_DECLINE_MORE = "No, that's everything. That sounds right to me."
#: How many times the participant answers the agent before the agreement must be recorded.
_AGREEMENT_TURNS: Final[int] = 4
_CUE_DEADLINE_SECONDS: Final[float] = 30.0
_IDLE_TRANSACTION_SECONDS: Final[float] = 5.0
_BUILD_WORDS: Final[tuple[str, ...]] = ("building it", "i'm building", "i am building", "build it now")


async def _agreed(database: Database, product_signal_id: UUID) -> bool:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select 1 from public.acceptance_criteria_agreements where product_signal_id = %s",
            (product_signal_id,),
        )
        return await cur.fetchone() is not None


async def _watch_idle_transactions(maximum: list[float], database: Database) -> None:
    """Record the longest any connection sits idle inside a transaction during the call."""
    while True:
        async with database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select coalesce(max(extract(epoch from now() - state_change)), 0) as idle
                from pg_stat_activity
                where datname = current_database() and state = 'idle in transaction'
                """
            )
            row = await cur.fetchone()
        maximum[0] = max(maximum[0], float(row["idle"]) if row is not None else 0.0)
        await asyncio.sleep(0.5)


async def _health(database: Database, product_signal_id: UUID, session_id: UUID) -> Mapping[str, object]:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select
              (select count(*) from public.jobs where state in ('failed', 'expired')) as failed_jobs,
              (select string_agg(distinct job_type, ',') from public.jobs
                where state in ('failed', 'expired')) as failed_job_types,
              (select count(*) from public.telemetry_readiness_projections
                where interview_session_id = %(session)s) as readiness_projections,
              (select count(*) from public.semantic_judgements
                where interview_session_id = %(session)s) as judgements,
              (select count(*) from public.visual_command_dispatches
                where interview_session_id = %(session)s) as dispatches,
              (select count(*) from public.visual_command_dispatches
                where interview_session_id = %(session)s
                  and outcome_reason = 'stale_target') as stale_targets,
              (select string_agg(state::text, ',') from public.voice_connections
                where interview_session_id = %(session)s) as voice_connections,
              (select state::text from public.product_signals where id = %(signal)s) as signal_state
            """,
            {"session": session_id, "signal": product_signal_id},
        )
        row = await cur.fetchone()
    assert row is not None
    return dict(row)


async def test_one_interview_from_greeting_to_a_ready_version(
    database: Database, auth_users: dict[str, SignedInUser], monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.environ.get("UTRACE_REALTIME_E2E") != "1":
        pytest.skip("set UTRACE_REALTIME_E2E=1 to run the live realtime voice check")
    api_key = require_env("OPENAI_API_KEY")
    report = harness.Report()
    async with AsyncOpenAI(api_key=api_key) as client:
        opening, ask, agree, decline_more, *steps = await asyncio.gather(
            harness.speech(client, _OPENING),
            harness.speech(client, _ASK),
            harness.speech(client, _AGREE),
            harness.speech(client, _DECLINE_MORE),
            *(harness.speech(client, step[0]) for step in _STEPS),
        )

    idle = [0.0]
    async with harness.live_call(
        database, auth_users, report=report, monkeypatch=monkeypatch
    ) as call:
        watcher = asyncio.create_task(_watch_idle_transactions(idle, database))
        signal_id = call.case.product_signal_id
        started = time.time()
        try:
            await harness.required_reply(database, call.session_id, after=call.connected_at)
            _, opening_end = await harness.say(call.microphone, opening)
            await harness.agent_reply(
                database,
                session_id=call.session_id,
                after=opening_end,
                deadline_seconds=harness.INTRO_REPLY_SECONDS,
            )

            sequence = 2
            applied: dict[str, str] = {"date_range": "last_30_days"}
            for pcm, (_, control, label, attribute, value) in zip(steps, _STEPS, strict=True):
                await harness.say(call.microphone, pcm)
                applied[attribute] = value
                sequence = await harness.apply_filter(
                    database,
                    call.document,
                    export_sequence=sequence,
                    control=control,
                    label=label,
                    applied=applied,
                )
                await asyncio.sleep(_PAUSE_SECONDS)
            _, ask_end = await harness.say(call.microphone, ask)

            # The agent recaps and proposes the change; the participant agrees until
            # the application has recorded the agreement.
            after = ask_end
            agreement_answer = ""
            for turn in range(_AGREEMENT_TURNS):
                await harness.whole_turn(database, call.session_id, after=after)
                if await _agreed(database, signal_id):
                    break
                _, after = await harness.say(call.microphone, agree if turn == 0 else decline_more)
                for _ in range(100):
                    if await _agreed(database, signal_id):
                        break
                    await asyncio.sleep(0.1)
                if await _agreed(database, signal_id):
                    reply = await harness.required_reply(database, call.session_id, after=after)
                    agreement_answer = reply[1]
                    break
            assert await _agreed(database, signal_id), "the agreement was never recorded"
            recap_dispatches = await harness.dispatches(database, call.session_id)

            # The gate takes a few seconds; then it approves and the attempt is authorized.
            await asyncio.sleep(3.0)
            async with database.transaction() as conn, conn.cursor() as cur:
                await cur.execute(
                    "update public.product_signals set state = 'gate_pending' "
                    "where id = %s and state = 'interviewing'",
                    (signal_id,),
                )
            approved_at = time.time()
            await approve_agreed_change(
                database,
                client_id=call.case.client_id,
                product_signal_id=signal_id,
                configuration_release_id=call.case.configuration_release_id,
            )
            building_cue_at = None
            for _ in range(int(_CUE_DEADLINE_SECONDS * 10)):
                building_cue_at = next(
                    (
                        at
                        for at, kind, content in report.appends
                        if at >= approved_at and kind == "append_commentary" and "being built" in content
                    ),
                    None,
                )
                if building_cue_at is not None:
                    break
                await asyncio.sleep(0.1)
            building_reply = await harness.required_reply(
                database, call.session_id, after=building_cue_at or approved_at
            )

            # The new version becomes ready.
            await create_ready_version(database, case=call.case)
            ready_at = time.time()
            await harness.set_progress(database, call.session_id, "ready_for_validation")
            ready_reply = await harness.required_reply(database, call.session_id, after=ready_at)
            await asyncio.sleep(3.0)
            words = await harness.words(
                database, call.session_id, after=call.connected_at, before=time.time()
            )
        finally:
            watcher.cancel()
    health = await _health(database, signal_id, call.session_id)
    elapsed = time.time() - started

    print("\njourney report")
    print(f"  agreement answer: {agreement_answer!r}")
    print(f"  recap circles: {[(d['step'], d['targets']) for d in recap_dispatches]}")
    print(f"  building cue at +{(building_cue_at or 0) - approved_at:.2f}s: {building_reply[1]!r}")
    print(f"  ready announcement: {ready_reply[1]!r}")
    print(f"  health: {health}, longest idle transaction {idle[0]:.1f}s over {elapsed:.0f}s")
    print("  character:", [(round(at - call.connected_at, 1), state) for at, state in report.character])
    print("  timeline (seconds after connect):")
    for at, speaker, text in words:
        print(f"    +{at - call.connected_at:6.2f}  {speaker}: {text!r}")

    assert recap_dispatches, "the recap circled none of the controls it named"
    assert not any(word in agreement_answer.lower() for word in _BUILD_WORDS), (
        f"the agent promised a build before the gate approved it: {agreement_answer!r}"
    )
    assert building_cue_at is not None, "the approval never reached the agent"
    assert any(word in building_reply[1].lower() for word in ("build", "working on")), (
        f"the agent did not say it is building once approved: {building_reply[1]!r}"
    )
    assert health["failed_jobs"] == 0
    assert health["stale_targets"] == 0
    assert idle[0] < _IDLE_TRANSACTION_SECONDS, f"a transaction sat idle for {idle[0]:.1f}s"
    # Readiness is re-evaluated as telemetry arrives; a loop would write far more.
    assert int(health["readiness_projections"]) < elapsed * 2, (
        f"{health['readiness_projections']} readiness projections in {elapsed:.0f}s"
    )
