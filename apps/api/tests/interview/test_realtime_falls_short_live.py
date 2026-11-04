"""Live: a new version that falls short leads to a change offered, not a closed case.

Partially validated, not validated, and unclear are why a participant asks for a
change, so while another version can still be made the agent offers one before it
records anything. Over a real voice call (see `realtime_harness`), the participant
tries the new version, finds the date range came back fixed instead of rolling, and
says it is not what they need: the agent must ask whether they would like it
changed, and record nothing yet. When they decline, the result stands: the agent
asks about any check their report left open, and then records the result as not
fully validated.

It costs about two minutes of voice, so it runs only when `UTRACE_REALTIME_E2E=1`.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Final
from uuid import UUID

import pytest
from openai import AsyncOpenAI

from app.db.pool import Database
from tests.conftest import require_env
from tests.interview import realtime_harness as harness
from tests.interview.fixtures import record_observation
from tests.interview.test_realtime_acknowledgement_live import _appended, _ready_candidate
from tests.interview.test_realtime_listening_live import _milestone
from tests.supabase_auth import SignedInUser

_SAVED_VIEW: Final[str] = "tiktok sizing drop-off"
#: What the reopened view restored: every filter, but the range pinned to fixed dates.
_RESTORED: Final[dict[str, str]] = {
    "date_range": "custom_2026-10-04_2026-11-03",
    "channels": "TikTok",
    "segments": "Women 25-34",
    "review_topics": "Sizing",
    "sentiments": "negative",
}
_REPORT = (
    "Hmm, the filters came back, but the date range is stuck on fixed dates now, it "
    "isn't the last thirty days anymore. That's not really what I need."
)
_DECLINE = "No, that's okay, I'll leave it as it is."
#: The answer to a check their report left open, such as whether saving kept the name.
_FOLLOW_UP = "Yes, it saved under the name I gave it."
#: How many open checks the agent may still ask about before it records.
_FOLLOW_UPS: Final[int] = 2
_CHANGE_WORDS: Final[tuple[str, ...]] = ("change", "fix", "adjust", "different", "update")
_ANSWER_DEADLINE_SECONDS: Final[float] = 10.0
_RECORD_DEADLINE_SECONDS: Final[float] = 40.0
_DEBRIEF: Final[str] = "The new version has loaded"
_READY: Final[str] = "The new version is ready"


async def test_a_version_that_falls_short_gets_a_change_offered_before_any_result(
    database: Database, auth_users: dict[str, SignedInUser], monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.environ.get("UTRACE_REALTIME_E2E") != "1":
        pytest.skip("set UTRACE_REALTIME_E2E=1 to run the live realtime voice check")
    api_key = require_env("OPENAI_API_KEY")
    report = harness.Report()
    lines = (
        "Let me try.",
        "Saving it as TikTok sizing drop-off.",
        "Refreshing.",
        "And reopening it.",
    )
    async with AsyncOpenAI(api_key=api_key) as client:
        answer, decline, follow_up, *spoken = await asyncio.gather(
            harness.speech(client, _REPORT),
            harness.speech(client, _DECLINE),
            harness.speech(client, _FOLLOW_UP),
            *(harness.speech(client, line) for line in lines),
        )

    async with harness.live_call(
        database, auth_users, report=report, monkeypatch=monkeypatch
    ) as call:
        session_id = call.session_id
        await harness.required_reply(database, session_id, after=call.connected_at)

        candidate = await _ready_candidate(database, call)
        await harness.leave_page(database, session_id)
        ready_at = time.time()
        await harness.set_progress(database, session_id, "ready_for_validation")
        ready_cue_at = await _appended(report, prefix=_READY, after=ready_at)
        await harness.required_reply(database, session_id, after=ready_cue_at)
        page = await harness.open_page(
            database,
            call,
            allocation_id=candidate,
            controls=(*harness.FILTER_CONTROLS, *harness.NEW_CONTROLS),
            binding_allocation=candidate,
        )
        await harness.report_page(database, call, page)
        debrief_at = await _appended(report, prefix=_DEBRIEF, after=time.time() - 1.0)
        await harness.whole_turn(database, session_id, after=debrief_at)

        # They try it: save, refresh, and reopen, which restores fixed dates.
        await harness.say(call.microphone, spoken[0])
        await asyncio.sleep(1.5)
        await harness.say(call.microphone, spoken[1])
        await _milestone(database, page, sequence=40, milestone="saved_view_created", view=_SAVED_VIEW)
        await asyncio.sleep(2.0)
        await harness.say(call.microphone, spoken[2])
        reloaded = await harness.open_page(
            database,
            call,
            allocation_id=candidate,
            controls=(*harness.FILTER_CONTROLS, *harness.NEW_CONTROLS),
            binding_allocation=candidate,
        )
        await asyncio.sleep(2.0)
        await harness.say(call.microphone, spoken[3])
        await _milestone(
            database, reloaded, sequence=5, milestone="saved_view_reopened", view=_SAVED_VIEW
        )
        await record_observation(
            database,
            document=reloaded,
            export_sequence=6,
            observation_type="product.milestone",
            target=None,
            payload={
                "milestone_id": "conversion_filters_applied",
                "attributes": [
                    {"name": name, "value": {"value": value}} for name, value in _RESTORED.items()
                ],
            },
        )
        await asyncio.sleep(3.0)

        # They say it falls short: the agent offers a change and records nothing.
        _, report_end = await harness.say(call.microphone, answer)
        offer = await harness.agent_reply(
            database,
            session_id=session_id,
            after=report_end,
            deadline_seconds=_ANSWER_DEADLINE_SECONDS,
        )
        await asyncio.sleep(4.0)
        recorded_before_decline = await _recorded_outcome(database, call.case.product_signal_id)

        # They want no change: the result stands, recorded as not fully validated.
        # The agent may first ask about a check their report left open; they answer.
        _, said_end = await harness.say(call.microphone, decline)
        recorded = None
        for _ in range(_FOLLOW_UPS + 1):
            question = await harness.agent_reply(
                database,
                session_id=session_id,
                after=said_end,
                deadline_seconds=_ANSWER_DEADLINE_SECONDS,
            )
            await asyncio.sleep(2.0)
            recorded = await _recorded_outcome(database, call.case.product_signal_id)
            if recorded is not None or question is None or "?" not in question[1]:
                break
            _, said_end = await harness.say(call.microphone, follow_up)
        deadline = time.monotonic() + _RECORD_DEADLINE_SECONDS
        while recorded is None and time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            recorded = await _recorded_outcome(database, call.case.product_signal_id)
        words = await harness.words(database, session_id, after=call.connected_at, before=time.time())

    print("\nfalls-short report")
    print(f"  offer: {offer!r}")
    print(f"  recorded before the decline: {recorded_before_decline!r}")
    print(f"  recorded after the decline: {recorded!r}")
    print("  timeline (seconds after connect):")
    for at, speaker, text in words:
        print(f"    +{at - call.connected_at:6.2f}  {speaker}: {text!r}")

    assert offer is not None, "the agent never answered once they said it fell short"
    offered = offer[1].lower()
    assert "?" in offered and any(word in offered for word in _CHANGE_WORDS), (
        f"the agent did not offer a change: {offer[1]!r}"
    )
    assert recorded_before_decline is None, (
        f"the agent recorded {recorded_before_decline!r} before asking about a change"
    )
    assert recorded is not None, "declining a change never recorded the result"
    assert recorded != "validated", f"a version that fell short was recorded as {recorded!r}"


async def _recorded_outcome(database: Database, product_signal_id: UUID) -> str | None:
    """The outcome of the case's completed Validation Attempt, if one completed."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select frozen_result ->> 'outcome' as outcome from public.validation_attempts
            where product_signal_id = %s and completed_at is not null
            order by completed_at desc
            limit 1
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    return None if row is None else str(row["outcome"])
