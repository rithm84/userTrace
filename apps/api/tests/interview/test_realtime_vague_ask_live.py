"""Live: a request that names its controls only in general, over a real voice call.

The participant sets their filters while describing them generically ("channel,
segment, review topic, and sentiment") and asks to save the setup, so the agent
has to work out from the preview what to restate. The agent must restate the
request naming what is on screen, at once or after one question about the job
behind it, so the cursor circles it; never claim the
product can already do what is asked; and, once the participant answers, carry
on with its next question or proposal rather than stopping at an acknowledgement.

It costs a few minutes of voice plus a few Jev calls, so it runs only when
`UTRACE_REALTIME_E2E=1` (see `realtime_harness`).
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Final

import pytest
from openai import AsyncOpenAI

from app.db.pool import Database
from tests.conftest import require_env
from tests.interview import realtime_harness as harness
from tests.interview.test_realtime_walkthrough_live import _STEPS
from tests.supabase_auth import SignedInUser

_OPENING = "So, I'll walk you through my issue."
_ASK = (
    "So, every Monday, I chain together a bunch of filters, like channel, segment, "
    "review topic, and sentiment, and many more. And my issue is that I have to keep "
    "chaining these together again and again, when it'd be nice if I could just, um, "
    "you know, like save a setup."
)
_ANSWER = "Yes, that's right. And the last 30 days should move with the date."
#: Seconds into the ask at which each filter is set, while they are still talking.
_FILTER_AT_SECONDS: Final[tuple[float, ...]] = (2.0, 3.5, 5.0, 6.5)
_ALREADY: Final[re.Pattern[str]] = re.compile(
    r"\b(can|could) already\b|\balready (lets|let you|saves?)\b"
)
#: More words than an acknowledgement such as "Got it."
_ACKNOWLEDGEMENT_WORDS: Final[int] = 4


async def test_a_vague_ask_is_restated_from_the_screen_and_the_agent_carries_on(
    database: Database, auth_users: dict[str, SignedInUser], monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.environ.get("UTRACE_REALTIME_E2E") != "1":
        pytest.skip("set UTRACE_REALTIME_E2E=1 to run the live realtime voice check")
    api_key = require_env("OPENAI_API_KEY")
    report = harness.Report()
    async with AsyncOpenAI(api_key=api_key) as client:
        opening, ask, answer = await asyncio.gather(
            harness.speech(client, _OPENING),
            harness.speech(client, _ASK),
            harness.speech(client, _ANSWER),
        )

    async with harness.live_call(
        database, auth_users, report=report, monkeypatch=monkeypatch
    ) as call:
        await harness.required_reply(database, call.session_id, after=call.connected_at)
        await harness.say(call.microphone, opening)
        await asyncio.sleep(2.0)

        # They set the filters while asking, naming none of the values.
        call.microphone.say(ask)
        await asyncio.sleep(0.2)
        ask_start = call.microphone.started_at
        sequence = 2
        applied: dict[str, str] = {"date_range": "last_30_days"}
        for at, (_, control, label, attribute, value) in zip(_FILTER_AT_SECONDS, _STEPS, strict=True):
            await asyncio.sleep(max(0.0, ask_start + at - time.time()))
            applied[attribute] = value
            sequence = await harness.apply_filter(
                database,
                call.document,
                export_sequence=sequence,
                control=control,
                label=label,
                applied=applied,
            )
        await call.microphone.finished.wait()
        ask_end = time.time()

        # The agent may first ask about the job behind the request, then recap.
        _, recap = await harness.whole_turn(database, call.session_id, after=ask_end)
        _, answer_end = await harness.say(call.microphone, answer)
        reply = await harness.agent_reply(
            database, session_id=call.session_id, after=answer_end, deadline_seconds=12.0
        )
        next_turn = "" if reply is None else (
            await harness.whole_turn(database, call.session_id, after=answer_end)
        )[1]
        circles = [
            d
            for d in await harness.dispatches(database, call.session_id)
            if d["created_at"].timestamp() >= ask_end  # type: ignore[union-attr]
        ]
        words = await harness.words(
            database, call.session_id, after=call.connected_at, before=time.time()
        )

    print("\nvague ask report")
    print(f"  turn after the ask: {recap!r}")
    print(f"  circles after the ask: {[(d['step'], d['targets']) for d in circles]}")
    print(f"  next turn after the answer: {next_turn!r}")
    print("  timeline (seconds after connect):")
    for at, speaker, text in words:
        print(f"    +{at - call.connected_at:6.2f}  {speaker}: {text!r}")

    spoken = " ".join(text for _, speaker, text in words if speaker == "agent").lower()
    assert not _ALREADY.search(spoken), "the agent claimed the product already does it"
    assert circles, f"the recap circled nothing: {recap!r} then {next_turn!r}"
    assert len(next_turn.split()) > _ACKNOWLEDGEMENT_WORDS, (
        f"the agent stopped after the answer: {next_turn!r}"
    )
