"""Live: whole interviews with a simulated Maya, checked for how the agent sounds.

Each run holds the scenario's interview from greeting to recorded validation over a
real call (`simulated_interview`), with Maya played by a model that hears the agent
and answers in her own words. A run fails on any failure a participant notices:
introducing itself twice, saying it is getting the change started twice,
re-listing her filters, repeating a sentence, stalling, or walking back through
the checks after she says it works, and on the judge finding repetition, a
restart, a stall, or anything false.

It costs several minutes of voice and model calls per persona, so it runs only
when `UTRACE_REALTIME_E2E=1`.
"""

from __future__ import annotations

import os
from typing import Final

import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from app.db.pool import Database
from tests.conftest import require_env
from tests.interview import realtime_harness as harness
from tests.interview import simulated_interview as simulated
from tests.interview.test_realtime_walkthrough_live import _STEPS
from tests.supabase_auth import SignedInUser

_PERSONAS: Final[dict[str, str]] = {
    "maya": simulated.PERSONA,
    "terse": simulated.PERSONA
    + "\n\nYou are busy today: you answer in as few words as you can, and you never volunteer anything.",
}
#: What the judge may find that fails a run outright.
_SERIOUS: Final[frozenset[str]] = frozenset({"repetition", "relisting", "restart", "stall", "wrong"})


@pytest.mark.parametrize("persona", sorted(_PERSONAS))
async def test_an_interview_sounds_natural_from_greeting_to_validation(
    database: Database,
    auth_users: dict[str, SignedInUser],
    monkeypatch: pytest.MonkeyPatch,
    persona: str,
) -> None:
    if os.environ.get("UTRACE_REALTIME_E2E") != "1":
        pytest.skip("set UTRACE_REALTIME_E2E=1 to run the live interview quality checks")
    report = harness.Report()
    async with (
        AsyncOpenAI(api_key=require_env("OPENAI_API_KEY")) as tts,
        AsyncAnthropic(api_key=require_env("ANTHROPIC_API_KEY")) as models,
    ):
        maya = simulated.SimulatedParticipant(models, persona=_PERSONAS[persona])
        async with harness.live_call(database, auth_users, report=report, monkeypatch=monkeypatch) as call:
            interview = await simulated.run_interview(
                database, call, report=report, tts=tts, maya=maya, steps=_STEPS
            )
        verdict = await simulated.judge(models, interview)
    score = simulated.score(interview)

    print(f"\ninterview quality report ({persona})")
    print(interview.transcript())
    print(f"  score: {score}")
    print(f"  reply seconds: {[round(seconds, 2) for seconds in interview.reply_seconds]}")
    print(f"  problems: {score.problems()}")
    print(f"  pauses inside the agent's speech: {interview.pauses(longer_than=0.9)}")
    print(f"  judge: naturalness {verdict.naturalness}, efficiency {verdict.efficiency}: {verdict.summary}")
    for issue in verdict.issues:
        print(f"    [{issue.category}] {issue.quote!r}: {issue.why}")
    for at, kind, content in report.appends:
        print(f"    append {kind} +{at - interview.utterances[0].at:6.1f}: {content[:140]!r}")

    assert interview.agreed, interview.notes
    assert interview.validated, "the validation outcome was never recorded"
    assert not score.problems(), score.problems()
    serious = [issue for issue in verdict.issues if issue.category in _SERIOUS]
    assert not serious, serious
