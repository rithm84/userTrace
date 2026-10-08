"""Live: the agent lets the participant walk through their workflow, then recaps it.

A participant showing their weekly filters names one filter at a time, pausing
while they set each one in the preview. The agent must not talk over that
walkthrough or gesture at each filter as it is set; once the participant has
finished and asked for what they want, the agent restates the request in one
pass, with the cursor showing the controls it names. This replays that moment
from the 2026-10-03 demo session, where the agent echoed every filter mid-way and
circled it, over a real voice call (see `realtime_harness`).

It costs about a minute of voice plus a few Jev calls, so it runs only
when `UTRACE_REALTIME_E2E=1`.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping
from typing import Final

import pytest
from openai import AsyncOpenAI

from app.db.pool import Database
from tests.conftest import require_env
from tests.interview import realtime_harness as harness
from tests.supabase_auth import SignedInUser

_OPENING = "Hey, sure, let me open the preview real quick while I explain."
#: Each step: what they say, the control they set while pausing, its label, and the
#: milestone attribute it adds.
_STEPS: Final[tuple[tuple[str, str, str, str, str], ...]] = (
    (
        (
            "So every Monday I chain together like seven or eight filters. "
            "For example, TikTok for the channel,"
        ),
        "channel",
        "TikTok",
        "channels",
        "TikTok",
    ),
    ("um, women twenty five to thirty four for the segment,", "segment", "Women 25-34", "segments", "Women 25-34"),
    ("sizing for the review topic,", "review_topic", "Sizing", "review_topics", "Sizing"),
    ("and, uh, negative for the sentiment.", "sentiment", "Negative", "sentiments", "negative"),
)
_PAUSE_SECONDS: Final[float] = 1.6
_ASK = (
    "So that's about it. I rebuild all of this every single Monday, which is super "
    "annoying. It would be great to save it as a view and reopen it in one click."
)
#: A backchannel such as "mm-hmm" in a pause is welcome while they walk through;
#: anything longer talks over them.
_BACKCHANNEL_WORDS: Final[int] = 3
#: Agent words this far apart belong to separate utterances.
_UTTERANCE_GAP_SECONDS: Final[float] = 1.0
_RECAP_DEADLINE_SECONDS: Final[float] = 30.0
_RECAP_SETTLE_SECONDS: Final[float] = 8.0


async def test_the_agent_listens_through_a_walkthrough_and_recaps_it_after(
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
        await harness.required_reply(database, call.session_id, after=call.connected_at)
        _, opening_end = await harness.say(call.microphone, opening)
        await harness.agent_reply(
            database,
            session_id=call.session_id,
            after=opening_end,
            deadline_seconds=harness.INTRO_REPLY_SECONDS,
        )

        walk_start: float | None = None
        sequence = 2
        applied: dict[str, str] = {"date_range": "last_30_days"}
        for pcm, (_, control, label, attribute, value) in zip(steps, _STEPS, strict=True):
            started, _ = await harness.say(call.microphone, pcm)
            walk_start = started if walk_start is None else walk_start
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
        ask_start, ask_end = await harness.say(call.microphone, ask)
        recap = await harness.required_reply(database, call.session_id, after=ask_end)
        deadline = time.time() + _RECAP_DEADLINE_SECONDS
        while time.time() < deadline and not [
            d for d in await harness.dispatches(database, call.session_id) if _at(d) >= ask_end
        ]:
            await asyncio.sleep(0.2)
        await asyncio.sleep(_RECAP_SETTLE_SECONDS)

        if walk_start is None:
            raise AssertionError("the walkthrough spoke no steps")
        dispatches = await harness.dispatches(database, call.session_id)
        words = await harness.words(
            database, call.session_id, after=call.connected_at, before=time.time()
        )

    during = [d for d in dispatches if walk_start <= _at(d) < ask_end]
    after = [d for d in dispatches if _at(d) >= ask_end]
    utterances: list[list[tuple[float, str]]] = []
    for at, speaker, text in words:
        if speaker != "agent" or not walk_start <= at < ask_start:
            continue
        if utterances and at - utterances[-1][-1][0] < _UTTERANCE_GAP_SECONDS:
            utterances[-1].append((at, text))
        else:
            utterances.append([(at, text)])
    said = ["".join(text for _, text in utterance).strip() for utterance in utterances]
    spoken_during = " | ".join(said)
    print("\nwalkthrough report")
    print(f"  agent said during the walkthrough: {spoken_during.strip()!r}")
    print(f"  gestures during the walkthrough: {[(d['step'], d['targets']) for d in during]}")
    print(f"  recap began {recap[0] - ask_end:.2f}s after the ask: {recap[1]!r}")
    print(f"  gestures after the ask: {[(d['step'], d['targets']) for d in after]}")
    print("  timeline (seconds after connect):")
    for at, speaker, text in words:
        print(f"    +{at - call.connected_at:6.2f}  {speaker}: {text!r}")
    for d in dispatches:
        print(f"    +{_at(d) - call.connected_at:6.2f}  dispatched {d['step']} at {d['targets']}")

    assert not during, "the agent gestured at filters while the participant was still setting them"
    assert all(len(utterance.split()) <= _BACKCHANNEL_WORDS for utterance in said), (
        f"the agent talked over the walkthrough: {spoken_during!r}"
    )
    assert after, "the agent's recap showed none of the controls it named"


def _at(dispatch: Mapping[str, object]) -> float:
    created = dispatch["created_at"]
    if not hasattr(created, "timestamp"):
        raise TypeError("a dispatch carries no creation time")
    return created.timestamp()  # type: ignore[union-attr]
