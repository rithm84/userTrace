"""Live: the agent listens while the participant tries the new version, then answers them.

People talk as they work. Trying a new version, a participant says "Sure." and
"Let me try.", then a few words as they set their filters, save the view, refresh,
and reopen it. None of that needs an answer: in a recorded call, "Sure." and "Let
me try." each got their own "Mm-hmm.", which sounded random. Over a real voice call
(see `realtime_harness`), the agent must stay quiet through the try, say a
backchannel only now and then, never two in a row or within a few seconds of each
other, never narrate the steps, and still answer once they say how it went.

It reports every agent utterance during the try and the words per spoken step. It
costs about two minutes of voice plus a few Jev calls, so it runs only when
`UTRACE_REALTIME_E2E=1`.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from itertools import pairwise
from typing import Final

import pytest
from openai import AsyncOpenAI

from app.db.pool import Database
from tests.conftest import require_env
from tests.interview import realtime_harness as harness
from tests.interview.fixtures import ObservedDocument, record_observation
from tests.interview.test_realtime_acknowledgement_live import _appended, _ready_candidate
from tests.supabase_auth import SignedInUser

_SAVED_VIEW: Final[str] = "tiktok sizing drop-off"
_RESTORED: Final[dict[str, str]] = {
    "date_range": "last_30_days",
    "channels": "TikTok",
    "segments": "Women 25-34",
    "review_topics": "Sizing",
    "sentiments": "negative",
}
_REPORT = "Okay, that all came back, the filters and the chart."
#: A backchannel is a word or a few; anything longer during the try is narration.
_BACKCHANNEL_WORDS: Final[int] = 4
#: Two backchannels closer than this sound like a reflex rather than listening.
_BACKCHANNEL_SPACING_SECONDS: Final[float] = 8.0
_UTTERANCE_GAP_SECONDS: Final[float] = 1.0
_ANSWER_DEADLINE_SECONDS: Final[float] = 8.0
_DEBRIEF: Final[str] = "The new version has loaded"
_READY: Final[str] = "The new version is ready"


async def test_the_agent_listens_while_the_new_version_is_tried_and_answers_the_report(
    database: Database, auth_users: dict[str, SignedInUser], monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.environ.get("UTRACE_REALTIME_E2E") != "1":
        pytest.skip("set UTRACE_REALTIME_E2E=1 to run the live realtime voice check")
    api_key = require_env("OPENAI_API_KEY")
    report = harness.Report()
    lines = (
        "Sure.",
        "Let me try.",
        "Okay, TikTok first,",
        "then women twenty five to thirty four,",
        "and sizing, negative.",
        "Now I'll save it as TikTok sizing drop-off.",
        "Refreshing the page.",
        "And reopening it.",
    )
    async with AsyncOpenAI(api_key=api_key) as client:
        answer, *spoken = await asyncio.gather(
            harness.speech(client, _REPORT), *(harness.speech(client, line) for line in lines)
        )

    async with harness.live_call(
        database, auth_users, report=report, monkeypatch=monkeypatch
    ) as call:
        session_id = call.session_id
        await harness.required_reply(database, session_id, after=call.connected_at)

        # The new version is ready, loads, and is debriefed.
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
        _, debrief = await harness.whole_turn(database, session_id, after=debrief_at)

        # They try it, talking as they go.
        reloaded: list[ObservedDocument] = []
        applied: dict[str, str] = {"date_range": "last_30_days"}

        async def set_filter(control: str, label: str, attribute: str, value: str) -> None:
            applied[attribute] = value
            await harness.apply_filter(
                database,
                page,
                export_sequence=5 + 3 * (len(applied) - 2),
                control=control,
                label=label,
                applied=applied,
            )

        async def set_two() -> None:
            await set_filter("review_topic", "Sizing", "review_topics", "Sizing")
            await set_filter("sentiment", "Negative", "sentiments", "negative")

        async def save() -> None:
            await _milestone(
                database, page, sequence=40, milestone="saved_view_created", view=_SAVED_VIEW
            )

        async def refresh() -> None:
            reloaded.append(
                await harness.open_page(
                    database,
                    call,
                    allocation_id=candidate,
                    controls=(*harness.FILTER_CONTROLS, *harness.NEW_CONTROLS),
                    binding_allocation=candidate,
                )
            )

        async def reopen() -> None:
            await _milestone(
                database, reloaded[0], sequence=5, milestone="saved_view_reopened", view=_SAVED_VIEW
            )
            await record_observation(
                database,
                document=reloaded[0],
                export_sequence=6,
                observation_type="product.milestone",
                target=None,
                payload={
                    "milestone_id": "conversion_filters_applied",
                    "attributes": [
                        {"name": name, "value": {"value": value}}
                        for name, value in _RESTORED.items()
                    ],
                },
            )

        steps: Sequence[tuple[bytes, Callable[[], Awaitable[None]] | None, float]] = (
            (spoken[0], None, 1.5),
            (spoken[1], None, 2.0),
            (spoken[2], lambda: set_filter("channel", "TikTok", "channels", "TikTok"), 2.0),
            (
                spoken[3],
                lambda: set_filter("segment", "Women 25-34", "segments", "Women 25-34"),
                2.0,
            ),
            (spoken[4], set_two, 2.0),
            (spoken[5], save, 2.5),
            (spoken[6], refresh, 2.0),
            (spoken[7], reopen, 3.0),
        )
        try_start: float | None = None
        for pcm, act, pause in steps:
            started, _ = await harness.say(call.microphone, pcm)
            try_start = started if try_start is None else try_start
            if act is not None:
                await act()
            await asyncio.sleep(pause)
        report_start, report_end = await harness.say(call.microphone, answer)
        reply = await harness.agent_reply(
            database,
            session_id=session_id,
            after=report_end,
            deadline_seconds=_ANSWER_DEADLINE_SECONDS,
        )
        await asyncio.sleep(3.0)
        words = await harness.words(
            database, session_id, after=call.connected_at, before=time.time()
        )

    if try_start is None:
        raise AssertionError("the try spoke no lines")
    during = _utterances(words, after=try_start, before=report_start)
    short = [(at, said) for at, said in during if len(said.split()) <= _BACKCHANNEL_WORDS]
    gaps = [later - earlier for (earlier, _), (later, _) in pairwise(short)]
    spoken_words = sum(len(said.split()) for _, said in during)

    print("\nlistening report")
    print(f"  debrief: {debrief!r}")
    print(f"  agent during the try ({len(during)} utterances): {[said for _, said in during]!r}")
    print(f"  agent words per spoken step: {spoken_words / len(lines):.2f}")
    print(f"  gaps between backchannels: {[round(gap, 1) for gap in gaps]}s")
    if reply is None:
        print("  no answer to the report")
    else:
        print(f"  answer {reply[0] - report_end:.2f}s after the report: {reply[1]!r}")
    print("  timeline (seconds after connect):")
    for at, speaker, text in words:
        print(f"    +{at - call.connected_at:6.2f}  {speaker}: {text!r}")
    for at, kind, content in report.appends:
        print(f"    +{at - call.connected_at:6.2f}  {kind}: {content[:120]!r}")

    assert all(len(said.split()) <= _BACKCHANNEL_WORDS for _, said in during), (
        f"the agent narrated the try: {[said for _, said in during]!r}"
    )
    assert len(short) <= 1 or min(gaps) >= _BACKCHANNEL_SPACING_SECONDS, (
        f"the agent backchanneled {[said for _, said in short]!r} within "
        f"{min(gaps):.1f}s of each other"
    )
    assert reply is not None, "the agent never answered once they said how it went"


async def _milestone(
    database: Database, document: ObservedDocument, *, sequence: int, milestone: str, view: str
) -> None:
    await record_observation(
        database,
        document=document,
        export_sequence=sequence,
        observation_type="product.milestone",
        target=None,
        payload={
            "milestone_id": milestone,
            "attributes": [{"name": "view_name", "value": {"value": view}}],
        },
    )


def _utterances(
    words: Sequence[tuple[float, str, str]], *, after: float, before: float
) -> list[tuple[float, str]]:
    """The agent's separate utterances between two moments, with when each began."""
    utterances: list[list[tuple[float, str]]] = []
    for at, speaker, text in words:
        if speaker != "agent" or not after <= at < before:
            continue
        if utterances and at - utterances[-1][-1][0] < _UTTERANCE_GAP_SECONDS:
            utterances[-1].append((at, text))
        else:
            utterances.append([(at, text)])
    return [
        (utterance[0][0], "".join(text for _, text in utterance).strip())
        for utterance in utterances
    ]
