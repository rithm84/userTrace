"""Circling what the agent names, as it names it, at the two moments that need it.

The agent cursor follows the agent's words at two moments only, and circles each
control it names there:

- **The recap.** Once the participant has explained what they want, the agent
  plays the request back to them, naming the controls it covers. Whether a turn
  is the recap is a fact about the whole turn, and its first clauses rarely show
  it, so the controls a turn names are held until any clause of it is judged to
  be playing the request back; the held ones are then circled together, in the
  order they were said, and later ones as they are said. The first turn that
  circles anything is the recap; when that turn ends, the moment is spent, so a
  second restatement of the same request is not circled again.
- **The debrief.** When a new version has loaded, the agent walks the participant
  through what changed (`announce_ready_preview`), and the controls that version
  added or moved are circled as the agent names them. The moment ends with the
  turn that circled them.

Elsewhere the cursor stays still unless the participant asks to be shown
something, which goes through delegation. GPT-Live's transcript of its own speech
arrives about a second ahead of the audio the person hears, so each clause is
judged as soon as it is transcribed: Jev decides whether the clause is about a
control on screen, which registered control that is, and, for the recap, whether
the agent's turn so far is playing the request back and whether the participant
had finished explaining (`DelegationRunner.recap_gesture`, `debrief_gesture`). A
confident answer is shown at once and lands while the person hears the clause.
Nothing in the clause is matched against any control: what is meant is Jev's
judgement.

Clauses are judged concurrently, because a list ("your channel, segment, and
topic") arrives faster than one judgement takes, and shown in the order they
were spoken: a judgement that finishes after a later clause's gesture is dropped,
since the person has already heard past it. A sentence that runs on without a
pause is judged every few words rather than once at its end, so each control it
names is shown as it is said rather than one, late, for the whole sentence.
"""

import asyncio
import re
from dataclasses import dataclass
from typing import Final

from app.interview.ports import RegisteredTarget
from app.interview.voice.delegation import DelegationRunner, DelegationSubject
from app.logging import get_logger

__all__ = ["GestureMoments", "SpokenClauses", "show_what_is_said"]

_log = get_logger(__name__)

#: Punctuation that ends a clause in the agent's transcript.
_CLAUSE_END: Final[frozenset[str]] = frozenset(",.;:?!")
#: The most words judged together when the agent does not pause: about two seconds
#: of speech, so a control named mid-sentence is shown while it is still heard.
_MAX_WORDS: Final[int] = 8
_WORD: Final[re.Pattern[str]] = re.compile(r"\S+")


class SpokenClauses:
    """Joins the agent's transcript deltas into clauses as each one ends."""

    def __init__(self) -> None:
        self._pending = ""

    def add(self, delta: str) -> tuple[str, ...]:
        """Take one transcript delta and return the clauses it completed, in order."""
        self._pending += delta
        clauses: list[str] = []
        start = 0
        for index, character in enumerate(self._pending):
            if character in _CLAUSE_END:
                clause = self._pending[start : index + 1].strip()
                start = index + 1
                if clause.rstrip("".join(_CLAUSE_END)).strip():
                    clauses.append(clause)
        self._pending = self._pending[start:]
        while (cut := _after_words(self._pending, _MAX_WORDS)) is not None:
            clauses.append(self._pending[:cut].strip())
            self._pending = self._pending[cut:]
        return tuple(clauses)


def _after_words(text: str, count: int) -> int | None:
    """Where `count` complete words end in `text`, or `None` before that many have.

    The last word counts as complete only once whitespace follows it, since a later
    delta may still extend it.
    """
    ends = [match.end() for match in _WORD.finditer(text)]
    complete = ends if text[-1:].isspace() else ends[:-1]
    return complete[count - 1] if len(complete) >= count else None


@dataclass(frozen=True, slots=True)
class _Recap:
    """The agent may be playing the participant's request back to them."""


@dataclass(frozen=True, slots=True)
class _Debrief:
    """The agent is walking the participant through a new version's controls."""

    controls: frozenset[str]


class GestureMoments:
    """Which moment, if any, the agent's words are spoken in, from the turns around them.

    A moment is spent by the first agent turn that circles something in it, when
    that turn ends; a turn that circles nothing, such as "It's loading now" before a
    debrief, leaves it open. The recap is open from the start. A debrief opens just
    before its cue is sent and also closes the recap, since the request has been
    built by then.

    It also knows when the application's own cues are announcing a new version: from
    the cue that says it is loading until the agent has finished speaking its
    debrief. That announcement is the new version's acknowledgement, so nothing else
    acknowledges what the participant does meanwhile.
    """

    def __init__(self) -> None:
        self._recap_open = True
        self._debrief: _Debrief | None = None
        self._circled = False
        self._announcing = False
        self._debrief_cued = False
        self._turn = 0

    @property
    def turn(self) -> int:
        """Which of the agent's turns a clause transcribed now belongs to."""
        return self._turn

    @property
    def announcing(self) -> bool:
        """Whether the application's cues are announcing a new version right now."""
        return self._announcing

    def announce(self) -> None:
        """The application is about to say a new version is loading."""
        self._announcing = True

    def current(self) -> _Recap | _Debrief | None:
        """The moment a clause transcribed now belongs to."""
        if self._debrief is not None:
            return self._debrief
        return _Recap() if self._recap_open else None

    def open_debrief(self, controls: frozenset[str]) -> None:
        """The agent is about to walk the participant through these new controls."""
        self._recap_open = False
        self._debrief = _Debrief(controls=controls)
        self._circled = False
        self._debrief_cued = True

    def circled(self) -> None:
        """Something was circled in the current moment."""
        self._circled = True

    def agent_stopped(self) -> None:
        """The agent finished a turn, which spends the moment if it circled something.

        The turn after a debrief was cued is the debrief, so it also ends the
        announcement.
        """
        self._turn += 1
        if self._debrief_cued:
            self._debrief_cued = False
            self._announcing = False
        if not self._circled:
            return
        self._circled = False
        if self._debrief is not None:
            self._debrief = None
        else:
            self._recap_open = False


async def show_what_is_said(
    clauses: "asyncio.Queue[str]",
    *,
    delegations: DelegationRunner,
    subject: DelegationSubject,
    moments: GestureMoments,
) -> None:
    """Judge each clause spoken in a recap or debrief and circle what it names, in order."""
    shown_through = 0
    # By turn: whether it is known to be the recap, and what it named before that.
    restated: set[int] = set()
    held: dict[int, list[tuple[int, RegisteredTarget]]] = {}

    async def show(order: int, targets: tuple[RegisteredTarget, ...], cause: str) -> None:
        nonlocal shown_through
        shown_through = order
        moments.circled()
        await delegations.show_targets(
            subject=subject, targets=targets, circle=True, cause=cause
        )

    async def judge_debrief(order: int, clause: str, moment: _Debrief) -> None:
        target = await delegations.debrief_gesture(
            subject=subject, saying=clause, controls=moment.controls
        )
        _log.info(
            "voice_spoken_gesture_judged",
            interview_session_id=str(subject.interview_session_id),
            moment="debrief",
            clause=clause,
            target_id=None if target is None else target.target_id,
        )
        if target is None or order < shown_through:
            return
        await show(order, (target,), "debrief")

    async def judge_recap(order: int, turn: int, clause: str) -> None:
        named = await delegations.recap_gesture(subject=subject, saying=clause)
        _log.info(
            "voice_spoken_gesture_judged",
            interview_session_id=str(subject.interview_session_id),
            moment="recap",
            clause=clause,
            target_id=None if named.target is None else named.target.target_id,
            restating=named.restating,
        )
        if named.restating:
            restated.add(turn)
        if named.target is not None:
            held.setdefault(turn, []).append((order, named.target))
        if turn not in restated:
            return
        due = sorted(
            (item for item in held.pop(turn, []) if item[0] > shown_through),
            key=lambda item: item[0],
        )
        if due:
            unique = {target.target_id: target for _, target in due}
            await show(due[-1][0], tuple(unique.values()), "recap")

    async with asyncio.TaskGroup() as group:
        order = 0
        while True:
            clause = await clauses.get()
            moment = moments.current()
            if moment is None:
                continue
            order += 1
            if isinstance(moment, _Debrief):
                group.create_task(judge_debrief(order, clause, moment))
            else:
                group.create_task(judge_recap(order, moments.turn, clause))
