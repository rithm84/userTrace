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
arrives a fraction of a second ahead of the audio the person hears, so every
transcript fragment is judged as soon as it arrives, over the agent's last few
words: Jev decides whether those words are about a control on screen, which
registered control that is, and, for the recap, whether the agent's turn so far is
playing the request back and whether the participant had finished explaining
(`DelegationRunner.recap_gesture`, `debrief_gesture`). A confident answer is shown
at once and lands while the person hears the words. Nothing in them is matched
against any control: what is meant is Jev's judgement.

Judging each fragment rather than each finished clause is what keeps the circle
on time: a clause is complete only after its last word, and on a list ("your
channel, segment, and topic") that is too late for its first. Each window
overlaps the last, so every control named gets several chances to be recognised
while it is still being heard, and is circled once per moment. Fragments are
judged concurrently and shown in the order spoken: a judgement that finishes
after a later fragment's gesture is dropped, since the person has already heard
past it.
"""

import asyncio
from dataclasses import dataclass
from typing import Final

from app.interview.ports import RegisteredTarget
from app.interview.voice.delegation import DelegationRunner, DelegationSubject
from app.logging import get_logger

__all__ = ["GestureMoments", "SpokenWindow", "show_what_is_said"]

_log = get_logger(__name__)

#: The agent's words each judgement reads: about a second and a half of speech,
#: enough to name a control and its value, short enough to be about what is heard
#: now. Measured on recorded GPT-Live recaps, judging this window on every
#: fragment put the cursor on the named control in time far more often than
#: judging finished clauses.
_WINDOW_WORDS: Final[int] = 6


class SpokenWindow:
    """The agent's latest words, as each transcript fragment of its turn arrives."""

    def __init__(self) -> None:
        self._tail = ""

    def add(self, delta: str) -> str | None:
        """Take one transcript delta and return the window it ends, or `None` if it adds no word.

        A delta may end inside a word that the next one completes, so only the
        window's words are kept, joined as they arrived at the end.
        """
        self._tail += delta
        if not delta.strip():
            return None
        window = " ".join(self._tail.split()[-_WINDOW_WORDS:])
        self._tail = window + (" " if self._tail[-1:].isspace() else "")
        return window

    def end_turn(self) -> None:
        """The agent finished a turn; the next one starts with no earlier words."""
        self._tail = ""


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
        self._circled: set[str] = set()
        self._announcing = False
        self._debrief_cued = False
        self._turn = 0

    @property
    def turn(self) -> int:
        """Which of the agent's turns words transcribed now belong to."""
        return self._turn

    @property
    def announcing(self) -> bool:
        """Whether the application's cues are announcing a new version right now."""
        return self._announcing

    def announce(self) -> None:
        """The application is about to say a new version is loading."""
        self._announcing = True

    def current(self) -> _Recap | _Debrief | None:
        """The moment words transcribed now belong to."""
        if self._debrief is not None:
            return self._debrief
        return _Recap() if self._recap_open else None

    def open_debrief(self, controls: frozenset[str]) -> None:
        """The agent is about to walk the participant through these new controls."""
        self._recap_open = False
        self._debrief = _Debrief(controls=controls)
        self._circled = set()
        self._debrief_cued = True

    def circled(self, target_ids: frozenset[str]) -> None:
        """These targets were circled in the current moment."""
        self._circled |= target_ids

    def uncircled(self, target_ids: frozenset[str]) -> frozenset[str]:
        """Which of these targets the current moment has not circled yet."""
        return target_ids - self._circled

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
        self._circled = set()
        if self._debrief is not None:
            self._debrief = None
        else:
            self._recap_open = False


async def show_what_is_said(
    windows: "asyncio.Queue[str]",
    *,
    delegations: DelegationRunner,
    subject: DelegationSubject,
    moments: GestureMoments,
) -> None:
    """Judge the agent's latest words on every fragment of a recap or debrief, and circle
    each control they name once, in the order spoken."""
    shown_through = 0
    # By turn: whether it is known to be the recap, and what it named before that.
    restated: set[int] = set()
    held: dict[int, list[tuple[int, RegisteredTarget]]] = {}

    async def show(order: int, targets: tuple[RegisteredTarget, ...], cause: str) -> None:
        nonlocal shown_through
        fresh = moments.uncircled(frozenset(target.target_id for target in targets))
        if not fresh:
            return
        shown_through = order
        moments.circled(fresh)
        await delegations.show_targets(
            subject=subject,
            targets=tuple(target for target in targets if target.target_id in fresh),
            circle=True,
            cause=cause,
        )

    async def judge_debrief(order: int, window: str, moment: _Debrief) -> None:
        target = await delegations.debrief_gesture(
            subject=subject, saying=window, controls=moment.controls
        )
        _log.info(
            "voice_spoken_gesture_judged",
            interview_session_id=str(subject.interview_session_id),
            moment="debrief",
            window=window,
            target_id=None if target is None else target.target_id,
        )
        if target is None or order < shown_through:
            return
        await show(order, (target,), "debrief")

    async def judge_recap(order: int, turn: int, window: str) -> None:
        named = await delegations.recap_gesture(subject=subject, saying=window)
        _log.info(
            "voice_spoken_gesture_judged",
            interview_session_id=str(subject.interview_session_id),
            moment="recap",
            window=window,
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
            window = await windows.get()
            moment = moments.current()
            if moment is None:
                continue
            order += 1
            if isinstance(moment, _Debrief):
                group.create_task(judge_debrief(order, window, moment))
            else:
                group.create_task(judge_recap(order, moments.turn, window))
