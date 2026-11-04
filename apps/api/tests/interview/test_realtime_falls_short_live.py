"""Live: a new version that falls short leads to a change offered, not a closed case.

Partially validated, not validated, and unclear are why a participant asks for a
change, so while another version can still be made the agent offers one before it
records anything. Over a real voice call (see `realtime_harness`), the participant
tries the new version, finds the date range came back fixed instead of rolling, and
says it is not what they need: the agent must ask whether they would like it
changed, and record nothing yet. When they decline, the result stands: the agent
asks about any check their report left open, and then records the result as not
fully validated.

When they want it changed instead, the agent proposes the change, and its
proposal mark records the change's draft while it speaks: the agreement that
follows must adopt that draft, and the mark is never spoken.

Each costs about two minutes of voice, so they run only when `UTRACE_REALTIME_E2E=1`.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Final
from uuid import UUID

import pytest
from openai import AsyncOpenAI

from app.db.pool import Database
from app.interview.voice import tools as voice_tools
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
_ACCEPT = "Yes please, it should keep the last thirty days rolling."
#: A change to the page itself: only the person can judge it, so its check must be
#: subjective, or validation could never find the observation an observed check needs.
_ACCEPT_DROPDOWN = (
    "Yes please, keep the last thirty days rolling, and keep the saved views "
    "dropdown disabled until there is a saved view."
)
_UI_STATE_WORDS: Final[tuple[str, ...]] = ("disabled", "enabled", "disable", "enable")
_AGREE = "Yes, that works."
#: How long the agent has to propose the change once they ask for it.
_PROPOSE_DEADLINE_SECONDS: Final[float] = 20.0
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
        await _try_the_new_version(database, call, report, spoken)

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


@pytest.mark.parametrize(
    "asked", [_ACCEPT, _ACCEPT_DROPDOWN], ids=["filter_behavior", "control_state"]
)
async def test_a_change_asked_for_in_the_new_version_is_drafted_from_the_agents_proposal(
    database: Database,
    auth_users: dict[str, SignedInUser],
    monkeypatch: pytest.MonkeyPatch,
    asked: str,
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
    # The harness case's release pins no implementation policy; recording a change
    # asked for in the new version reads only the case's iteration budget from it.
    async def _policy(_database: Database, *, configuration_release_id: UUID) -> object:
        return SimpleNamespace(implementation_limit=3)

    monkeypatch.setattr(voice_tools, "load_implementation_policy", _policy)
    async with AsyncOpenAI(api_key=api_key) as client:
        answer, accept, agree, *spoken = await asyncio.gather(
            harness.speech(client, _REPORT),
            harness.speech(client, asked),
            harness.speech(client, _AGREE),
            *(harness.speech(client, line) for line in lines),
        )

    async with harness.live_call(
        database, auth_users, report=report, monkeypatch=monkeypatch
    ) as call:
        session_id = call.session_id
        signal_id = call.case.product_signal_id
        await _try_the_new_version(database, call, report, spoken)

        # It falls short; they want it changed, and the agent proposes the change.
        _, report_end = await harness.say(call.microphone, answer)
        offer = await harness.agent_reply(
            database,
            session_id=session_id,
            after=report_end,
            deadline_seconds=_ANSWER_DEADLINE_SECONDS,
        )
        await asyncio.sleep(2.0)
        _, accepted_at = await harness.say(call.microphone, accept)
        proposal_turn = None
        drafted = None
        for _ in range(2):
            proposal_turn = await harness.agent_reply(
                database,
                session_id=session_id,
                after=accepted_at,
                deadline_seconds=_PROPOSE_DEADLINE_SECONDS,
            )
            drafted = await _proposal_draft(database, signal_id, since=report_end)
            if drafted is not None or proposal_turn is None:
                break
            # A clarifying question first: they answer it as asked.
            _, accepted_at = await harness.say(call.microphone, accept)
        await asyncio.sleep(2.0)
        await harness.say(call.microphone, agree)
        deadline = time.monotonic() + _RECORD_DEADLINE_SECONDS
        requested = None
        while requested is None and time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            requested = await _requested_change(database, signal_id)
        words = await harness.words(database, session_id, after=call.connected_at, before=time.time())
        checks = await _agreed_checks(database, signal_id)

    print("\nchange-in-validation report")
    print(f"  offer: {offer!r}")
    print(f"  proposal turn: {proposal_turn!r}")
    print(f"  proposal draft: {drafted!r}")
    print(f"  requested change: {requested!r}")
    print(f"  agreed checks: {checks!r}")
    print("  timeline (seconds after connect):")
    for at, speaker, text in words:
        print(f"    +{at - call.connected_at:6.2f}  {speaker}: {text!r}")

    assert offer is not None, "the agent never answered once they said it fell short"
    assert proposal_turn is not None, "the agent never proposed the change they asked for"
    assert drafted is not None, "the agent's proposal mark recorded no draft"
    assert requested is not None, "agreeing to the change never recorded it"
    # The agreement adopts the draft the proposal started, recorded before it.
    assert requested["prepared_draft_id"] == drafted["id"], (requested, drafted)
    assert float(str(requested["lead_seconds"])) > 0, requested
    leaked = [
        text
        for _, speaker, text in words
        if speaker == "agent"
        and any(part in text for part in ("<proposal", "proposed_outcome", "{", "</"))
    ]
    assert not leaked, f"the agent spoke its proposal mark: {leaked!r}"
    # A check on what the product does and shows is observed; one on whether a
    # control is enabled is about the page itself, which only the person can judge.
    rolling = [
        statement
        for statement, _ in checks
        if "rolling" in statement.lower() or "relative" in statement.lower()
    ]
    assert rolling, f"no agreed check covers the rolling date range: {checks!r}"
    assert all(
        observability == "observed"
        for statement, observability in checks
        if statement in rolling and not any(word in statement.lower() for word in _UI_STATE_WORDS)
    ), checks
    if asked == _ACCEPT_DROPDOWN:
        state_checks = [
            (statement, observability)
            for statement, observability in checks
            if any(word in statement.lower() for word in _UI_STATE_WORDS)
        ]
        assert state_checks, f"no agreed check covers the dropdown's state: {checks!r}"
        assert all(observability == "subjective" for _, observability in state_checks), (
            f"a check on a control's state was frozen as observed: {state_checks!r}"
        )


async def _agreed_checks(database: Database, product_signal_id: UUID) -> list[tuple[str, str]]:
    """The newest agreed checks, each with how it is validated."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select statement, observability::text as observability
            from public.validation_criteria
            where product_signal_id = %s
              and criteria_revision = (
                select max(criteria_revision) from public.validation_criteria
                where product_signal_id = %s
              )
            order by ordinal
            """,
            (product_signal_id, product_signal_id),
        )
        return [(str(row["statement"]), str(row["observability"])) for row in await cur.fetchall()]


async def _proposal_draft(
    database: Database, product_signal_id: UUID, *, since: float
) -> dict[str, object] | None:
    """The newest proposal draft recorded after `since`, if any."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, proposed_scope ->> 'intended_outcome' as outcome
            from public.prepared_drafts
            where product_signal_id = %s and kind = 'proposal' and proposed_at > %s
            order by proposed_at desc
            limit 1
            """,
            (product_signal_id, datetime.fromtimestamp(since, tz=UTC)),
        )
        row = await cur.fetchone()
    return None if row is None else dict(row)


async def _requested_change(
    database: Database, product_signal_id: UUID
) -> dict[str, object] | None:
    """The agreement a change asked for in the new version recorded, if one was."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select agreements.prepared_draft_id,
                   extract(epoch from agreements.recorded_at - drafts.proposed_at)::float
                     as lead_seconds
            from public.acceptance_criteria_agreements as agreements
            left join public.prepared_drafts as drafts
              on drafts.id = agreements.prepared_draft_id
            where agreements.product_signal_id = %s
              and agreements.acceptance_criteria_revision > 1
            order by agreements.acceptance_criteria_revision desc
            limit 1
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    return None if row is None else dict(row)

async def _try_the_new_version(
    database: Database,
    call: harness.LiveCall,
    report: harness.Report,
    spoken: Sequence[bytes],
) -> None:
    """Up to the moment they say how it went: the new version loads, and they save,
    refresh, and reopen a view, which comes back with fixed dates."""
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
