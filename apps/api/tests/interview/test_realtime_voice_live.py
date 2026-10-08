"""Live: the agent hears what the participant does in the preview, in real time.

A real GPT-Live call over WebRTC runs through the production path: the voice
connection route, the sideband Job with its activity feed, GPT-6.1 Sol delegations,
Jev target selection, and visual-command dispatch, against the test database.
`aiortc` plays the session tab and speaks lines synthesized with OpenAI's
text-to-speech. Between lines the test commits the browser activity a participant
applying the demo's filters produces, then asks the agent what it saw, to circle a
control, and how the change is going, and marks the new version ready. It reports
how long each hop took: when each delegation arrived against the words around it,
which path answered it, when the gesture was dispatched, and when the agent was
told the new version is ready.

It costs about two minutes of voice plus a few Sol and Jev calls, so it runs only
when `UTRACE_REALTIME_E2E=1`.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest
from openai import AsyncOpenAI

from app.db.pool import Database
from tests.conftest import require_env
from tests.factories import create_ready_version
from tests.interview import realtime_harness as harness
from tests.supabase_auth import SignedInUser

_INTRO = "Hi. Every Monday I rebuild the same conversion filters. Let me show you."
_ASK_SEEN = "Okay, I just set my filters. Can you see which filters I applied?"
_ASK_CIRCLE = "Can you circle the channel filter for me?"
_ASK_UNSURE = "Sorry, I'm not sure which one is the segment filter."
_ASK_PROGRESS = (
    "Okay. Earlier we agreed your team would build saved views for me. "
    "How's that change coming along?"
)


async def test_the_agent_sees_preview_activity_and_answers_in_realtime(
    database: Database, auth_users: dict[str, SignedInUser], monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.environ.get("UTRACE_REALTIME_E2E") != "1":
        pytest.skip("set UTRACE_REALTIME_E2E=1 to run the live realtime voice check")
    api_key = require_env("OPENAI_API_KEY")
    report = harness.Report()
    async with AsyncOpenAI(api_key=api_key) as client:
        intro_line, ask_seen, ask_circle, ask_unsure, ask_progress = await asyncio.gather(
            harness.speech(client, _INTRO),
            harness.speech(client, _ASK_SEEN),
            harness.speech(client, _ASK_CIRCLE),
            harness.speech(client, _ASK_UNSURE),
            harness.speech(client, _ASK_PROGRESS),
        )

    async with harness.live_call(
        database, auth_users, report=report, monkeypatch=monkeypatch
    ) as call:
        session_id, document, microphone = call.session_id, call.document, call.microphone
        connected_at = call.connected_at

        greeting_at, greeting = await harness.required_reply(database, session_id, after=connected_at)

        microphone.say(intro_line)
        await asyncio.sleep(0.2)
        report.lines["intro_start"] = microphone.started_at
        await microphone.finished.wait()
        report.lines["intro_end"] = time.time()
        # "Let me show you" may rightly be met with silence while they show it.
        intro = await harness.agent_reply(
            database,
            session_id=session_id,
            after=report.lines["intro_end"],
            deadline_seconds=harness.INTRO_REPLY_SECONDS,
        )

        filters_at = time.time()
        await harness.apply_filters(database, document)
        fed_at = report.first_activity_after(filters_at)
        for _ in range(40):
            if fed_at is not None:
                break
            await asyncio.sleep(0.1)
            fed_at = report.first_activity_after(filters_at)

        microphone.say(ask_seen)
        await asyncio.sleep(0.2)
        seen_start = microphone.started_at
        await microphone.finished.wait()
        seen_end = time.time()
        seen_reply_at, seen_reply = await harness.whole_turn(database, session_id, after=seen_end)

        microphone.say(ask_circle)
        await asyncio.sleep(0.2)
        circle_start = microphone.started_at
        await microphone.finished.wait()
        circle_end = time.time()
        circle_reply_at, circle_reply = await harness.required_reply(
            database, session_id, after=circle_end
        )
        for _ in range(100):
            if await harness.dispatches(database, session_id):
                break
            await asyncio.sleep(0.1)

        microphone.say(ask_unsure)
        await asyncio.sleep(0.2)
        unsure_start = microphone.started_at
        await microphone.finished.wait()
        unsure_end = time.time()
        unsure_reply = await harness.agent_reply(
            database,
            session_id=session_id,
            after=unsure_end,
            deadline_seconds=harness.REPLY_DEADLINE_SECONDS,
        )
        await asyncio.sleep(2.0)

        await harness.set_progress(database, session_id, "making_changes")
        microphone.say(ask_progress)
        await asyncio.sleep(0.2)
        progress_start = microphone.started_at
        await microphone.finished.wait()
        progress_end = time.time()
        progress_reply_at, progress_reply = await harness.required_reply(
            database, session_id, after=progress_end
        )
        # The status answers the delegation after the agent's lead-in, and the
        # ready cue waits for the agent to finish saying it.
        settled_at = None
        for _ in range(100):
            settled_at = next(
                (
                    d["settled_at"].timestamp()  # type: ignore[union-attr]
                    for d in await harness.delegations(database, session_id)
                    if d["settled_at"] is not None
                    and str(d["finding"]).startswith("reported the change's progress")
                ),
                None,
            )
            if settled_at is not None:
                break
            await asyncio.sleep(0.1)
        if settled_at is not None:
            await harness.agent_reply(
                database,
                session_id=session_id,
                after=settled_at,
                deadline_seconds=harness.INTRO_REPLY_SECONDS,
            )

        await create_ready_version(database, case=call.case)
        ready_at = time.time()
        await harness.set_progress(database, session_id, "ready_for_validation")
        ready_cue_at = None
        for _ in range(100):
            ready_cue_at = next(
                (
                    at
                    for at, kind, content in report.appends
                    if kind == "append_commentary"
                    and at >= ready_at
                    and content.startswith("The new version is ready")
                ),
                None,
            )
            if ready_cue_at is not None:
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(2.0)
        dispatches = await harness.dispatches(database, session_id)
        delegations = await harness.delegations(database, session_id)
        words = await harness.words(database, session_id, after=seen_start, before=time.time())


    listening_seen = report.first_character_after("listening", seen_start)
    listening_circle = report.first_character_after("listening", circle_start)
    circles = [d for d in dispatches if d["step"] == "circle" and circle_end <= d["created_at"].timestamp() < unsure_start]  # type: ignore[union-attr]
    unsure_gestures = [d for d in dispatches if d["created_at"].timestamp() >= unsure_start]  # type: ignore[union-attr]
    dispatch_at = circles[0]["created_at"].timestamp() if circles else None  # type: ignore[union-attr]
    print("\nrealtime voice report")
    print(f"  greeting began {greeting_at - connected_at:.2f}s after connect: {greeting[:80]!r}")
    if intro is None:
        print("  intro met with silence while they show the workflow")
    else:
        print(f"  intro reply began {intro[0] - report.lines['intro_end']:.2f}s after the line: {intro[1][:100]!r}")
    print(f"  activity fed {None if fed_at is None else round(fed_at - filters_at, 2)}s after the filters committed")
    print(f"  listening shown {None if listening_seen is None else round(listening_seen - seen_start, 2)}s after the question began")
    print(f"  'what did I apply' answered {seen_reply_at - seen_end:.2f}s after it ended: {seen_reply!r}")
    print(f"  listening shown {None if listening_circle is None else round(listening_circle - circle_start, 2)}s after the circle ask began")
    print(f"  'circle' answered {circle_reply_at - circle_end:.2f}s after it ended: {circle_reply!r}")
    print(f"  circle dispatched {None if dispatch_at is None else round(dispatch_at - circle_end, 2)}s after the ask ended")
    print(f"  'not sure which is the segment' answered: {None if unsure_reply is None else unsure_reply[1]!r}")
    print(f"  gestures the agent chose after that: {[(d['step'], d['targets']) for d in unsure_gestures]}")
    progress_answers = [
        d
        for d in delegations
        if d["created_at"].timestamp() >= progress_start  # type: ignore[union-attr]
        and str(d["finding"]).startswith("reported the change's progress")
    ]
    print(f"  'how is the change going' answered {progress_reply_at - progress_end:.2f}s after it ended: {progress_reply!r}")
    for delegation in progress_answers:
        print(f"  progress delegation settled in {delegation['settled_at'].timestamp() - delegation['created_at'].timestamp():.2f}s")  # type: ignore[union-attr]
    print(f"  ready cue appended {None if ready_cue_at is None else round(ready_cue_at - ready_at, 2)}s after the new version was marked ready")
    for delegation in delegations:
        settled = delegation["settled_at"]
        took = None if settled is None else round(settled.timestamp() - delegation["created_at"].timestamp(), 2)  # type: ignore[union-attr]
        print(f"  delegation at +{delegation['created_at'].timestamp() - connected_at:.2f}s settled in {took}s: {delegation['state']} {delegation['finding']!r}")  # type: ignore[union-attr]
    for at, kind, content in report.appends:
        print(f"  append {kind} at +{at - connected_at:.2f}s: {content[:140]!r}")
    print("  timeline (seconds after connect):")
    events = [(at, f"character {state}") for at, state in report.character]
    events += [(at, f"line {name}") for name, at in report.lines.items()]
    events += [(at, f"{speaker} said {text!r}") for at, speaker, text in words]
    events += [
        (d["created_at"].timestamp(), f"delegation arrived ({d['state']}: {d['finding']!r})")  # type: ignore[union-attr]
        for d in delegations
    ]
    events += [
        (d["created_at"].timestamp(), f"dispatched {d['step']} at {d['targets']}")  # type: ignore[union-attr]
        for d in dispatches
    ]
    events += [
        (seen_start, "question 'what did I apply' starts"),
        (seen_end, "question 'what did I apply' ends"),
        (seen_reply_at, "answer starts"),
        (circle_start, "ask 'circle' starts"),
        (circle_end, "ask 'circle' ends"),
        (circle_reply_at, "circle reply starts"),
        (unsure_start, "line 'not sure which is the segment' starts"),
        (unsure_end, "line 'not sure which is the segment' ends"),
        (progress_start, "line 'how is the change going' starts"),
        (progress_end, "line 'how is the change going' ends"),
        (ready_at, "new version marked ready"),
    ]
    for at, label in sorted(events):
        print(f"    +{at - connected_at:6.2f}  {label}")

    assert fed_at is not None and fed_at - filters_at < 2.5
    assert "tiktok" in seen_reply.lower()
    assert circles, "no circle was dispatched for the circle request"
    assert progress_answers, "the progress question was not answered from recorded state"
    assert ready_cue_at is not None and ready_cue_at - ready_at < 3.0
