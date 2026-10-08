"""A whole interview with a simulated participant, scored for how the agent sounds.

Maya, the scenario's originating user, is played by a language model that hears
what the agent actually said (its played words, as recorded) and answers in her
own words through synthesized speech on the live call (`realtime_harness`). A
director drives everything else the way the product would: the filters she sets in
the preview, the gate approving the agreed change, the new version becoming
ready, and her trying it. The run is scored two ways: deterministic checks for the
failures people notice (repeated sentences, re-listed filters, restarted
greetings, stalls, over-confirmation) and a judge model reading the transcript
against a rubric.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Final, Literal
from uuid import UUID

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.db.pool import Database
from app.db.rows import as_uuid
from tests.gate.factories import approve_agreed_change
from tests.interview import realtime_harness as harness
from tests.interview.fixtures import ObservedDocument, record_observation

__all__ = ["Interview", "Score", "Verdict", "judge", "run_interview", "score"]

_PARTICIPANT_MODEL: Final = "claude-sonnet-5-5"
_JUDGE_MODEL: Final = "claude-opus-5-5"
#: The agent's turn is over once it has stopped speaking for this long.
_TURN_QUIET_SECONDS: Final[float] = 1.0
#: A reply the participant is owed that has not begun by now is a stall.
_STALL_SECONDS: Final[float] = 10.0
_DISCUSSION_TURNS: Final[int] = 8
_VALIDATION_TURNS: Final[int] = 6

PERSONA: Final[str] = """You are Maya, a growth lead at Dudulemon, an apparel brand, and a customer of Shopalytics, an e-commerce analytics product. You are on a voice call with Wren, a product agent working for the Shopalytics team, about a feature request you posted.

Your facts, which you share only when they come up:
- Every Monday, and a few more times during the week, you rebuild the same conversion dashboard filters: date range last 30 days, channel TikTok, segment women 25 to 34, review topic sizing, sentiment negative, product Aero High-Rise Legging. The conversion state stays on purchased.
- Rebuilding it takes about ten minutes each time. Today you keep the settings in a note and click through them by hand.
- You use that set about five times a week to watch whether sizing complaints are hurting TikTok conversion.
- You want to save the whole set under a name and reopen it later in one click. Last 30 days should keep rolling rather than freeze on the day you saved.
- You do not need sharing, renaming, or a dashboard redesign.

You talk like a real person on a call: short and natural, usually one or two sentences, sometimes casual. You never read out stage directions, lists, or markup."""

Speaker = Literal["agent", "participant"]


@dataclass(frozen=True, slots=True)
class Utterance:
    """One turn of the conversation: who spoke, when it began, and what was said."""

    at: float
    speaker: Speaker
    text: str


@dataclass
class Interview:
    """Everything one simulated interview produced."""

    utterances: list[Utterance] = field(default_factory=list)
    stalls: list[str] = field(default_factory=list)
    reply_seconds: list[float] = field(default_factory=list)
    praised_at: float | None = None
    validated: bool = False
    agreed: bool = False
    notes: list[str] = field(default_factory=list)
    #: Every word either side said, as recorded, with when it was played or heard.
    words: list[tuple[float, str, str]] = field(default_factory=list)

    def pauses(self, *, longer_than: float) -> list[str]:
        """Pauses inside the agent's speech: where its words stopped mid-turn."""
        found = []
        for (at, speaker, _), (next_at, next_speaker, text) in zip(
            self.words, self.words[1:], strict=False
        ):
            gap = next_at - at
            if speaker == next_speaker == "agent" and longer_than < gap < 4.0:
                found.append(f"{gap:.1f}s before {text.strip()!r}")
        return found

    def transcript(self) -> str:
        start = self.utterances[0].at if self.utterances else 0.0
        return "\n".join(
            f"[{u.at - start:6.1f}s] {'Wren' if u.speaker == 'agent' else 'Maya'}: {u.text}"
            for u in self.utterances
        )


class SimulatedParticipant:
    """Maya, played by a model that hears the agent's words."""

    def __init__(self, client: AsyncAnthropic, *, persona: str) -> None:
        self._client = client
        self._persona = persona

    async def line(self, interview: Interview, *, situation: str, must_answer: bool) -> str:
        """What Maya says next in this situation; empty when she would stay quiet.

        When she `must_answer`, staying quiet is not offered: a person asked a direct
        question answers it, however briefly.
        """
        heard = "\n".join(
            f"{'Wren' if u.speaker == 'agent' else 'You'}: {u.text}" for u in interview.utterances[-14:]
        )
        ending = (
            "Reply with only the words you say out loud."
            if must_answer
            else "Reply with only the words you say out loud, or with nothing at all if you "
            "would stay quiet."
        )
        for attempt in range(2):
            response = await self._client.messages.create(
                model=_PARTICIPANT_MODEL,
                max_tokens=400,
                # She answers at once rather than thinking first, as a person on a call does.
                thinking={"type": "between_tools"},
                system=self._persona,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"The call so far:\n{heard or '(nothing yet)'}\n\n"
                            f"Right now: {situation}\n\n{ending}"
                            + ("\n\nYou must say something." if attempt else "")
                        ),
                    }
                ],
            )
            text = "".join(block.text for block in response.content if block.type == "text").strip()
            # A stage direction such as "(stays quiet)" is silence, not speech.
            said = re.sub(r"\([^)]*\)", "", text).strip().strip('"')
            if said or not must_answer:
                return said
        raise AssertionError(f"the simulated participant gave no answer: {text!r}")


class _Call:
    """The live call as the director drives it: Maya's voice and the agent's turns."""

    def __init__(
        self,
        database: Database,
        call: harness.LiveCall,
        *,
        report: harness.Report,
        tts: AsyncOpenAI,
        maya: SimulatedParticipant,
        interview: Interview,
    ) -> None:
        self.database = database
        self.call = call
        self._report = report
        self._tts = tts
        self.maya = maya
        self.interview = interview
        # The agent's words are read once each: a turn starts after the last one read.
        self._heard_until = call.connected_at

    async def say(self, text: str) -> float:
        """Maya says `text`; returns when she finished."""
        if not text:
            return time.time()
        pcm = await harness.speech(self._tts, text)
        started, ended = await harness.say(self.call.microphone, pcm)
        self.interview.utterances.append(Utterance(at=started, speaker="participant", text=text))
        return ended

    async def respond(self, situation: str, *, must_answer: bool) -> float | None:
        """Maya answers in her own words; returns when she finished, or None if she stayed quiet."""
        text = await self.maya.line(self.interview, situation=situation, must_answer=must_answer)
        if not text:
            return None
        return await self.say(text)

    async def agent_turn(self, *, after: float, deadline_seconds: float) -> Utterance | None:
        """The agent's next whole turn after a moment: from its first word until it has
        stopped speaking, as the participant hears it."""
        after = max(after, self._heard_until)
        deadline = time.time() + deadline_seconds
        while not await self._agent_words(after=after):
            if time.time() > deadline:
                return None
            await asyncio.sleep(0.2)
        quiet_since: float | None = None
        while quiet_since is None or time.time() - quiet_since < _TURN_QUIET_SECONDS:
            speaking = bool(self._report.character) and self._report.character[-1][1] == "speaking"
            quiet_since = None if speaking else (quiet_since or time.time())
            await asyncio.sleep(0.1)
        words = await self._agent_words(after=after)
        self._heard_until = time.time()
        turn = Utterance(at=words[0][0], speaker="agent", text="".join(t for _, t in words).strip())
        self.interview.utterances.append(turn)
        return turn

    async def _agent_words(self, *, after: float) -> list[tuple[float, str]]:
        return [
            (at, text)
            for at, speaker, text in await harness.words(
                self.database, self.call.session_id, after=after, before=time.time() + 1.0
            )
            if speaker == "agent"
        ]

    async def answer_owed(self, *, after: float, label: str) -> Utterance | None:
        """The agent's reply to something that needs one; a late one is recorded as a stall."""
        turn = await self.agent_turn(after=after, deadline_seconds=_STALL_SECONDS)
        if turn is None:
            self.interview.stalls.append(label)
            nudge = await self.say("Hello? Are you still there?")
            return await self.agent_turn(after=nudge, deadline_seconds=_STALL_SECONDS)
        self.interview.reply_seconds.append(turn.at - after)
        return turn


async def _agreed(database: Database, product_signal_id: UUID) -> bool:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select 1 from public.acceptance_criteria_agreements where product_signal_id = %s",
            (product_signal_id,),
        )
        return await cur.fetchone() is not None


async def _attempt(database: Database, product_signal_id: UUID) -> UUID:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select id from public.implementation_attempts where product_signal_id = %s "
            "order by created_at desc limit 1",
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AssertionError("the gate's approval left no Implementation Attempt")
    return as_uuid(row, "id")


async def _validation_settled(database: Database, session_id: UUID) -> bool:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select 1 from public.validation_attempts where interview_session_id = %s "
            "and state <> 'in_progress'",
            (session_id,),
        )
        return await cur.fetchone() is not None


_SAVED_VIEW: Final = "TikTok sizing drop-off"
_RESTORED: Final[Mapping[str, str]] = {
    "date_range": "last_30_days",
    "channels": "TikTok",
    "segments": "Women 25-34",
    "review_topics": "Sizing",
    "sentiments": "negative",
    "products": "Aero High-Rise Legging",
    "conversion_states": "Purchased",
}


async def _milestones(
    database: Database, document: ObservedDocument, *, start: int, payloads: Sequence[Mapping[str, object]]
) -> int:
    for offset, payload in enumerate(payloads):
        await record_observation(
            database,
            document=document,
            export_sequence=start + offset,
            observation_type="product.milestone",
            target=None,
            payload=payload,
        )
    return start + len(payloads)


def _filters_applied(values: Mapping[str, str]) -> Mapping[str, object]:
    return {
        "milestone_id": "conversion_filters_applied",
        "attributes": [{"name": name, "value": {"value": value}} for name, value in values.items()],
    }


def _rendered(surface: str, rows: int) -> Mapping[str, object]:
    return {
        "milestone_id": "conversion_render_completed",
        "attributes": [
            {"name": "surface", "value": {"value": surface}},
            {"name": "status", "value": {"value": "rendered"}},
            {"name": "row_count", "value": {"value": str(rows)}},
        ],
    }


async def run_interview(
    database: Database,
    call: harness.LiveCall,
    *,
    report: harness.Report,
    tts: AsyncOpenAI,
    maya: SimulatedParticipant,
    steps: Sequence[tuple[str, str, str, str, str]],
) -> Interview:
    """Hold one whole interview with Maya: discovery, agreement, build, and validation."""
    interview = Interview()
    live = _Call(database, call, report=report, tts=tts, maya=maya, interview=interview)
    signal_id = call.case.product_signal_id

    # 1. The greeting, then Maya says what she needs and starts showing it.
    await live.answer_owed(after=call.connected_at, label="greeting")
    after = await live.respond(
        "Wren just greeted you. Tell Wren in a sentence or two what you need, and that you "
        "will show the filters you rebuild every Monday.",
        must_answer=True,
    )
    await live.agent_turn(after=after or time.time(), deadline_seconds=4.0)

    # 2. The walkthrough: she narrates each filter as she sets it in the preview.
    sequence = 2
    applied: dict[str, str] = {"date_range": "last_30_days"}
    for line, control, label, attribute, value in steps:
        await live.say(line)
        applied[attribute] = value
        sequence = await harness.apply_filter(
            database, call.document, export_sequence=sequence, control=control, label=label, applied=applied
        )
        await asyncio.sleep(1.6)
    after = await live.respond(
        "You have finished setting the filters in the preview. Say you wish you could save "
        "this whole set as a view and reopen it in one click.",
        must_answer=True,
    )

    # 3. Discussion until the agreement is recorded.
    for turn in range(_DISCUSSION_TURNS):
        heard = await live.answer_owed(after=after or time.time(), label=f"discussion {turn}")
        if await _agreed(database, signal_id):
            break
        if heard is None:
            continue
        asked = heard.text.rstrip().endswith("?")
        after = await live.respond(
            "Wren just asked you something: answer it in your own words, briefly, from your "
            "facts. If Wren proposed a change and checks that fit what you need, agree clearly."
            if asked
            else "Wren said this without asking you anything. Reply only if a person naturally "
            "would; otherwise stay quiet.",
            must_answer=asked,
        )
        if after is None:
            for _ in range(60):
                if await _agreed(database, signal_id):
                    break
                await asyncio.sleep(0.25)
            if await _agreed(database, signal_id):
                break
            after = time.time()
    interview.agreed = await _agreed(database, signal_id)
    if not interview.agreed:
        interview.notes.append("the agreement was never recorded")
        return interview

    # 4. The gate approves; the build is announced; later she asks how it is going.
    await live.agent_turn(after=time.time(), deadline_seconds=4.0)
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
    await live.answer_owed(after=approved_at, label="building announcement")
    await harness.set_progress(database, call.session_id, "making_changes")
    await asyncio.sleep(6.0)
    after = await live.respond(
        "A little time has passed. Ask Wren how the change is going.", must_answer=True
    )
    await live.answer_owed(after=after or time.time(), label="progress question")

    # 5. The new version is ready and loads in her preview.
    candidate = await harness.ready_candidate(database, call, attempt_id=await _attempt(database, signal_id))
    await harness.observe_browser(database, call.session_id, ready=False)
    ready_at = time.time()
    await harness.set_progress(database, call.session_id, "ready_for_validation")
    await live.answer_owed(after=ready_at, label="ready announcement")
    page = await harness.open_page(
        database,
        call,
        allocation_id=candidate,
        controls=(*harness.FILTER_CONTROLS, *harness.NEW_CONTROLS),
        binding_allocation=candidate,
    )
    await harness.observe_browser(database, call.session_id, ready=True)
    await live.answer_owed(after=time.time(), label="debrief")

    # 6. She tries it: saves the view, refreshes, reopens it, and says it works.
    await live.say(f"Okay. I'm setting my filters and saving it as {_SAVED_VIEW}.")
    sequence = await _milestones(
        database,
        page,
        start=5,
        payloads=(
            _filters_applied(_RESTORED),
            _rendered("review_list", 30),
            _rendered("product_breakdown", 4),
            {
                "milestone_id": "saved_view_created",
                "attributes": [{"name": "view_name", "value": {"value": _SAVED_VIEW.lower()}}],
            },
        ),
    )
    await live.agent_turn(after=time.time(), deadline_seconds=4.0)
    await live.say("Now I'll refresh the page.")
    reloaded = await harness.open_page(
        database,
        call,
        allocation_id=candidate,
        controls=(*harness.FILTER_CONTROLS, *harness.NEW_CONTROLS),
        binding_allocation=candidate,
    )
    await asyncio.sleep(1.0)
    await live.say("And reopening the saved view.")
    await _milestones(
        database,
        reloaded,
        start=5,
        payloads=(
            {
                "milestone_id": "saved_view_reopened",
                "attributes": [{"name": "view_name", "value": {"value": _SAVED_VIEW.lower()}}],
            },
            _filters_applied(_RESTORED),
            _rendered("review_list", 30),
            _rendered("product_breakdown", 4),
            _rendered("conversion_trend", 30),
        ),
    )
    await asyncio.sleep(2.5)
    after = await live.respond(
        "You just reopened the saved view. Every filter came back, the results match, and the "
        "date range still shows the last 30 days, ending on the preview's current date. React "
        "the way you would, in a few words, such as saying it works perfectly.",
        must_answer=True,
    )
    interview.praised_at = after

    # 7. Wren confirms what it still needs and records the outcome.
    for turn in range(_VALIDATION_TURNS):
        heard = await live.answer_owed(after=after or time.time(), label=f"validation {turn}")
        if await _validation_settled(database, call.session_id):
            break
        if heard is None:
            continue
        facts = (
            "Everything worked: saving under a name worked, after the refresh reopening the "
            "view restored every filter and the results, and the date range was still the "
            "last 30 days, ending on the date the preview shows, so whatever date Wren names "
            "for that end is right."
        )
        asked = heard.text.rstrip().endswith("?")
        after = await live.respond(
            f"Wren just asked you something: answer it truthfully and briefly. {facts}"
            if asked
            else "Wren said this without asking you anything. Reply only if a person naturally "
            f"would; otherwise stay quiet. {facts}",
            must_answer=asked,
        )
        if after is None:
            after = time.time()
    for _ in range(80):
        if await _validation_settled(database, call.session_id):
            break
        await asyncio.sleep(0.25)
    interview.validated = await _validation_settled(database, call.session_id)
    await live.agent_turn(after=time.time(), deadline_seconds=8.0)
    interview.words = await harness.words(
        database, call.session_id, after=call.connected_at, before=time.time() + 1.0
    )
    return interview


# --- Scoring -------------------------------------------------------------------

_FILTER_VALUES: Final[tuple[str, ...]] = (
    "tiktok", "25-34", "25 to 34", "sizing", "negative", "30 days", "aero",
)
_STARTED: Final = re.compile(r"get(?:ting)? (?:it|that|this) started|getting started on")
_INTRODUCTIONS: Final = re.compile(r"\bi'?m wren\b|\bi am wren\b|\bthis is wren\b")


@dataclass(frozen=True, slots=True)
class Score:
    """The deterministic checks: each failure a person notices, counted."""

    introductions: int
    started_mentions: int
    relists: int
    repeated_sentences: tuple[str, ...]
    one_moments: int
    stalls: tuple[str, ...]
    questions_after_praise: int
    reply_p50: float
    reply_p90: float
    longest_turn_words: int

    def problems(self) -> list[str]:
        found = []
        if self.introductions != 1:
            found.append(f"introduced itself {self.introductions} times")
        if self.started_mentions > 1:
            found.append(f"said it is getting the change started {self.started_mentions} times")
        if self.relists > 2:
            found.append(f"listed the filters {self.relists} times")
        if self.repeated_sentences:
            found.append(f"repeated itself: {list(self.repeated_sentences)}")
        if self.one_moments > 1:
            found.append(f"said 'one moment' {self.one_moments} times")
        if self.stalls:
            found.append(f"stalled at: {list(self.stalls)}")
        if self.questions_after_praise > 2:
            found.append(f"asked {self.questions_after_praise} questions after hearing it works")
        return found


def _sentences(text: str) -> list[str]:
    return [s.strip().lower() for s in re.split(r"(?<=[.?!])\s+", text) if len(s.split()) >= 4]


def score(interview: Interview) -> Score:
    agent = [u for u in interview.utterances if u.speaker == "agent"]
    text = " ".join(u.text.lower() for u in agent)
    seen: list[str] = []
    repeated: list[str] = []
    for turn in agent:
        for sentence in _sentences(turn.text):
            if any(SequenceMatcher(None, sentence, earlier).ratio() >= 0.85 for earlier in seen):
                repeated.append(sentence)
        seen.extend(_sentences(turn.text))
    relists = sum(
        1 for u in agent if sum(value in u.text.lower() for value in _FILTER_VALUES) >= 4
    )
    after_praise = [
        u for u in agent if interview.praised_at is not None and u.at >= interview.praised_at
    ]
    replies = sorted(interview.reply_seconds) or [0.0]
    return Score(
        introductions=len(_INTRODUCTIONS.findall(text)),
        started_mentions=len(_STARTED.findall(text)),
        relists=relists,
        repeated_sentences=tuple(repeated),
        one_moments=text.count("one moment"),
        stalls=tuple(interview.stalls),
        questions_after_praise=sum(u.text.count("?") for u in after_praise),
        reply_p50=replies[len(replies) // 2],
        reply_p90=replies[min(len(replies) - 1, int(len(replies) * 0.9))],
        longest_turn_words=max((len(u.text.split()) for u in agent), default=0),
    )


class Issue(BaseModel):
    category: str
    quote: str
    why: str


class Verdict(BaseModel):
    """The judge's reading of the transcript."""

    issues: list[Issue]
    naturalness: int
    efficiency: int
    summary: str


_RUBRIC: Final[str] = """You review transcripts of voice interviews between Wren, an AI product agent, and Maya, a customer. Wren's job: understand Maya's need, agree one bounded change with a few checks, stay quiet while it is built, then have Maya validate the new version and record her answers. Maya's lines come from a simulated participant; judge only Wren.

Report every instance of these problems, quoting Wren's words:
- repetition: Wren says the same thing, or nearly the same sentence, more than once without need.
- relisting: Wren lists all of Maya's filters or values again after they were already confirmed, instead of referring to them as a whole.
- restart: Wren starts a turn over or introduces itself again.
- over_confirmation: after Maya says it works, Wren walks back through checks one by one instead of confirming what remains in one short question, or re-asks something Maya already confirmed.
- verbosity: a turn far longer than a spoken answer needs, or more than one question in a turn.
- stall: Wren leaves Maya waiting with no reply when one is owed (shown by Maya saying "Hello?").
- filler: needless "one moment", "let me check", narration of what Wren is doing, or empty acknowledgements.
- wrong: Wren says something false, contradicts itself, claims the product already does the change, or exposes internal details.
- awkward: phrasing a person would find robotic or confusing.

Maya's own words are her answers, as the product requires: when she says everything came back or that it all works, that confirms every check it covers, and asking about any of them again is over_confirmation. Wren also hears what the new version showed, so closing on her word is not wrong.

Then rate naturalness (1-10: how much it sounds like a skilled human interviewer) and efficiency (1-10: how directly it gets to a recorded agreement and validation without wasted turns), and summarize in two sentences.

Reply with only a JSON object: {"issues": [{"category": ..., "quote": ..., "why": ...}], "naturalness": int, "efficiency": int, "summary": str}"""


async def judge(client: AsyncAnthropic, interview: Interview) -> Verdict:
    response = await client.messages.create(
        model=_JUDGE_MODEL,
        max_tokens=6000,
        system=_RUBRIC,
        messages=[{"role": "user", "content": interview.transcript()}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        raise AssertionError(f"the judge returned no JSON: {text[:500]}")
    return Verdict.model_validate(json.loads(match.group(0)))
