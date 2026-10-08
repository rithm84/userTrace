"""Knowing which agreed checks the new version already showed.

People try the new version before the agent asks: they save, reload, and reopen
in one go. Left alone, the agent walks the agreed checks in order and asks them to
repeat what they just did. While a Validation Attempt is in progress, each time new
product milestones arrive, Jev judges per agreed check whether the person's
activity since the attempt opened shows the product doing it
(`DelegationRunner.checks_shown`). A check newly judged shown, or contradicted,
becomes one line the activity feed sends with the activity that showed it, so the
agent hears both together and asks the person to confirm what they saw instead of
asking them to do it again.

This informs the conversation and records nothing: a check still needs the
person's own confirmation, and the validation outcome is recorded with the
observations cited, as before, when the agent delegates their answers.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from app.db.pool import Database
from app.db.rows import as_datetime, as_int, as_uuid
from app.interview.observations import ObservedActivity, describe_activity, recent_activity
from app.interview.voice.delegation import DelegationRunner, DelegationSubject
from app.logging import get_logger
from app.validation.records import CriterionRow, load_criteria_on_connection

__all__ = ["ShownChecks"]

_log = get_logger(__name__)

#: Enough to cover a whole validation pass, filter changes and reloads included.
_READ_LIMIT: Final[int] = 120


@dataclass(frozen=True, slots=True)
class _Attempt:
    """The session's Validation Attempt in progress."""

    validation_attempt_id: UUID
    opened_at: datetime
    criteria_revision: int


class ShownChecks:
    """The agreed checks of the current Validation Attempt, reported once each."""

    def __init__(
        self,
        database: Database,
        *,
        subject: DelegationSubject,
        delegations: DelegationRunner,
    ) -> None:
        self._database = database
        self._subject = subject
        self._delegations = delegations
        self._attempt: UUID | None = None
        self._reported: set[str] = set()

    async def newly_shown(self) -> str | None:
        """The line telling the agent which checks the activity has newly shown, if any."""
        attempt = await _attempt_in_progress(
            self._database, interview_session_id=self._subject.interview_session_id
        )
        if attempt is None:
            return None
        if attempt.validation_attempt_id != self._attempt:
            # A new candidate is validated afresh: what the last one showed is not
            # evidence for this one.
            self._attempt = attempt.validation_attempt_id
            self._reported = set()
        activity = await recent_activity(
            self._database,
            client_id=self._subject.client_id,
            interview_session_id=self._subject.interview_session_id,
            since=attempt.opened_at,
            limit=_READ_LIMIT,
        )
        if not activity:
            return None
        criteria = await _criteria(
            self._database,
            product_signal_id=self._subject.product_signal_id,
            criteria_revision=attempt.criteria_revision,
        )
        pending = {
            str(criterion.id): criterion.statement
            for criterion in criteria
            if str(criterion.id) not in self._reported
        }
        if not pending:
            return None
        verdict = await self._delegations.checks_shown(
            subject=self._subject, checks=pending, activity=_lines(activity)
        )
        if verdict is None or not (verdict.shown or verdict.contradicted):
            return None
        self._reported.update(verdict.shown | verdict.contradicted)
        remaining = [
            criterion.statement
            for criterion in criteria
            if str(criterion.id) not in self._reported
        ]
        _log.info(
            "voice_checks_shown_reported",
            interview_session_id=str(self._subject.interview_session_id),
            shown=len(verdict.shown),
            contradicted=len(verdict.contradicted),
            remaining=len(remaining),
        )
        return _report(
            shown=[pending[check] for check in pending if check in verdict.shown],
            contradicted=[pending[check] for check in pending if check in verdict.contradicted],
            remaining=remaining,
        )


def _lines(activity: Sequence[ObservedActivity]) -> list[str]:
    """The activity as product-term lines, a line repeated back to back kept once."""
    lines: list[str] = []
    for item in activity:
        line = describe_activity(item)
        if not lines or lines[-1] != line:
            lines.append(line)
    return lines


def _report(*, shown: Sequence[str], contradicted: Sequence[str], remaining: Sequence[str]) -> str:
    """What the agent should know about the agreed checks after this activity."""
    parts: list[str] = []
    if shown:
        parts.append(
            "With that, the new version showed these agreed checks working: "
            f"{'; '.join(shown)}. Do not ask them to do these again; ask them only to "
            "confirm what they saw."
        )
    if contradicted:
        parts.append(
            "The product did something different from these agreed checks: "
            f"{'; '.join(contradicted)}. Ask them what they saw."
        )
    if remaining:
        parts.append(f"Still to try: {'; '.join(remaining)}.")
    else:
        parts.append(
            "Every agreed check has now been tried. Once they have confirmed each, "
            "delegate once so the application records their answers."
        )
    return " ".join(parts)


async def _attempt_in_progress(
    database: Database, *, interview_session_id: UUID
) -> _Attempt | None:
    """The session's in-progress Validation Attempt: when it opened, its criteria revision."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, created_at, acceptance_criteria_revision
            from public.validation_attempts
            where interview_session_id = %s and state = 'in_progress'
            order by created_at desc
            limit 1
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return _Attempt(
        validation_attempt_id=as_uuid(row, "id"),
        opened_at=as_datetime(row, "created_at"),
        criteria_revision=as_int(row, "acceptance_criteria_revision"),
    )


async def _criteria(
    database: Database, *, product_signal_id: UUID, criteria_revision: int
) -> tuple[CriterionRow, ...]:
    async with database.connection() as conn:
        return await load_criteria_on_connection(
            conn, product_signal_id=product_signal_id, criteria_revision=criteria_revision
        )
