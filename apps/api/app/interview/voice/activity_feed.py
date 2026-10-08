"""Telling the agent what the participant does in the preview, as they do it.

The agent knows only what the application tells it. While the observation path
is ready, this reads the session's newest activity each time new activity is
committed and adds what is new to the conversation quietly (`AgentChannel.note`),
which the agent uses in its next turn without speaking it on arrival.

Each line says what changed, the way the person would: a milestone the agent has
not heard of yet is described whole, and one it has heard of again is described
by the values that changed ("channels: all → TikTok"), because repeating the
whole filter set hides the one choice the person just made. A milestone repeated
with the same values says nothing new and is not sent, nor is a click on a control
the product does not name. During validation the same append also says which
agreed checks this activity newly showed (`ShownChecks`), so the agent hears the
activity and what it proved together and answers once.

Context appended while the agent is talking can make it break off mid-sentence,
so what the person does then is held and sent, combined, the moment the agent
stops: `agent_quiet` is set exactly while it is neither answering nor speaking.

A moment the person expects the agent to notice, such as the screen they said
they would open coming up, is sent speakably instead, asking the agent to
acknowledge it in a few words (`Acknowledgements` decides which and when).

The lines are product terms from the client's own annotations, never telemetry
internals, so the agent can talk about them without crossing the
[telemetry language boundary](../../../../../docs/PRD.md#telemetry-language-boundary).
"""

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

from app.db.pool import Database
from app.db.rows import as_bool
from app.interview.observations import ObservedActivity, describe_activity, recent_activity
from app.interview.voice.acknowledgements import Acknowledgements
from app.interview.voice.channel import AgentChannel, VoiceSubject
from app.interview.voice.checks_shown import ShownChecks
from app.interview.voice.session_changes import SessionChanges
from app.logging import get_logger

__all__ = ["feed_activity"]

_log = get_logger(__name__)

_WINDOW: Final[timedelta] = timedelta(minutes=2)
_READ_LIMIT: Final[int] = 30
#: Keeps one note short enough that the agent's next reply is not slowed by it.
_MAX_CHARACTERS: Final[int] = 1600
_PREFIX: Final[str] = "In the preview, the person just did this, oldest first: "
_ACKNOWLEDGE: Final[str] = (
    "Acknowledge it out loud now in a few words, the way someone watching would, "
    "without describing or listing what they did, and not in words you have already "
    'used to acknowledge them, such as "There it is." once and "Nice." the next time.'
)


async def feed_activity(
    database: Database,
    changes: SessionChanges,
    subject: VoiceSubject,
    agent: AgentChannel,
    agent_quiet: asyncio.Event,
    checks: ShownChecks,
    acknowledgements: Acknowledgements,
) -> None:
    """Append each new piece of the person's preview activity until cancelled.

    The first wake reads what happened before this connection, which is history
    rather than a moment to acknowledge.
    """
    loop = asyncio.get_running_loop()
    seen: set[str] = set()
    reported: Mapping[str, Mapping[str, str]] = {}
    held: list[str] = []
    held_checks: str | None = None
    history = True
    woken = changes.watch()
    while True:
        await _next_wake(
            woken, agent_quiet=agent_quiet, holding=bool(held) or held_checks is not None
        )
        woken.clear()
        first, history = history, False
        read_at = loop.time()
        moment: list[str] = []
        if await _observation_ready(database, subject=subject):
            activity = await recent_activity(
                database,
                client_id=subject.client_id,
                interview_session_id=subject.interview_session_id,
                since=datetime.now(tz=UTC) - _WINDOW,
                limit=_READ_LIMIT,
            )
            fresh = [item for item in activity if item.observation_id not in seen]
            seen.update(item.observation_id for item in fresh)
            lines, reported = _describe(fresh, reported=reported)
            moment = [] if first or held else lines
            held.extend(lines)
            if any(item.milestone is not None for item in fresh):
                report = await checks.newly_shown()
                if report is not None:
                    held_checks = report
        if (held or held_checks is not None) and agent_quiet.is_set():
            spoken = bool(moment) and await acknowledgements.worth_saying(moment, read_at=read_at)
            await _append(agent, subject=subject, lines=held, checks=held_checks, spoken=spoken)
            held = []
            held_checks = None


async def _next_wake(woken: asyncio.Event, *, agent_quiet: asyncio.Event, holding: bool) -> None:
    """Wait for new activity, or, while lines are held, for the agent to stop talking."""
    if not holding:
        await woken.wait()
        return
    waits = {
        asyncio.ensure_future(woken.wait()),
        asyncio.ensure_future(agent_quiet.wait()),
    }
    try:
        await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for wait in waits:
            wait.cancel()


def _describe(
    fresh: Sequence[ObservedActivity], *, reported: Mapping[str, Mapping[str, str]]
) -> tuple[list[str], dict[str, Mapping[str, str]]]:
    """The lines worth sending for newly read activity, and the milestones now reported."""
    latest = {item.milestone: item for item in fresh if item.milestone is not None}
    now_reported = dict(reported)
    lines: list[str] = []
    for item in fresh:
        if item.milestone is not None:
            if latest[item.milestone] is not item:
                continue
            before = now_reported.get(item.milestone)
            if before == item.milestone_attributes:
                continue
            now_reported[item.milestone] = item.milestone_attributes
            lines.append(
                describe_activity(item) if before is None else _changed(item, before=before)
            )
            continue
        if item.observation_type == "interaction.activation" and item.control is None:
            continue
        lines.append(describe_activity(item))
    return lines, now_reported


def _changed(item: ObservedActivity, *, before: Mapping[str, str]) -> str:
    """A milestone the agent has heard of before, said by the values that changed."""
    if item.milestone is None:
        raise ValueError("only a milestone has values to compare")
    changes = [
        f"{_words(name)}: {before.get(name, 'none')} → {value}"
        for name, value in item.milestone_attributes.items()
        if before.get(name) != value
    ]
    changes.extend(
        f"{_words(name)}: {value} → none"
        for name, value in before.items()
        if name not in item.milestone_attributes
    )
    return f"{_words(item.milestone)} ({', '.join(changes)})"


def _words(identifier: str) -> str:
    return identifier.replace(".", " ").replace("_", " ")


async def _append(
    agent: AgentChannel,
    *,
    subject: VoiceSubject,
    lines: Sequence[str],
    checks: str | None,
    spoken: bool,
) -> None:
    """Send the activity quietly, or, when it is acknowledged, as speakable context."""
    if spoken:
        tail = _ACKNOWLEDGE if checks is None else f"{checks} {_ACKNOWLEDGE}"
        await agent.say(_content(lines, tail=tail))
    else:
        await agent.note(_content(lines, tail=checks))
    _log.info(
        "voice_activity_fed",
        interview_session_id=str(subject.interview_session_id),
        items=len(lines),
        checks_reported=checks is not None,
        acknowledged=spoken,
    )


def _content(lines: Sequence[str], *, tail: str | None) -> str:
    """The newest lines that fit one append, in the order they happened, then ``tail``."""
    budget = _MAX_CHARACTERS - (len(tail) + 1 if tail is not None else 0)
    kept: list[str] = []
    size = len(_PREFIX)
    for line in reversed(lines):
        if size + len(line) + 2 > budget:
            break
        kept.append(line)
        size += len(line) + 2
    if not kept:
        if tail is None:
            raise ValueError("an append needs activity lines or a checks report")
        return tail
    body = _PREFIX + "; ".join(reversed(kept)) + "."
    return body if tail is None else f"{body} {tail}"


async def _observation_ready(database: Database, *, subject: VoiceSubject) -> bool:
    """Whether the complete observation path currently backs product-activity claims."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select observation_ready from public.session_readiness "
            "where interview_session_id = %s",
            (subject.interview_session_id,),
        )
        row = await cur.fetchone()
    return row is not None and as_bool(row, "observation_ready")
