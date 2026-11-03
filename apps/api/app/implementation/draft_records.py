"""The records an agreement reads about prepared drafts.

Contract: docs/arch-components/implementation.md#prepared-draft

An agreement names the proposal draft it agreed to and discards every other open
draft of the case, in its own transaction. These are the parts of a draft that
agreement needs, kept apart from the Job that writes drafts so the interview's
agreement code depends on nothing that writes code.
"""

from __future__ import annotations

import re
from typing import Final
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict
from utrace_contracts.canonical import payload_digest
from utrace_contracts.domain.lifecycle import CriterionObservability

from app.db.rows import as_uuid
from app.envelope import JsonValue

__all__ = [
    "OPEN_DRAFT_STATES",
    "DraftKind",
    "ProposedChange",
    "ProposedCriterion",
    "change_from_row",
    "discard_open_drafts",
    "draft_for_agreement",
]

OPEN_DRAFT_STATES: Final[tuple[str, ...]] = ("proposed", "drafting", "drafted")
_SPACE = re.compile(r"\s+")


class DraftKind:
    """Why a draft was written: from the original request, or a proposal."""

    INTAKE = "intake"
    PROPOSAL = "proposal"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProposedCriterion(_Frozen):
    """One check the voice agent proposed, as the participant will see it."""

    statement: str
    observability: CriterionObservability


class ProposedChange(_Frozen):
    """The change the voice agent proposed: what it does, the workflow, and its checks."""

    intended_outcome: str
    workflow_ref: str
    criteria: tuple[ProposedCriterion, ...]

    def digest(self) -> str:
        """The identity an agreement is compared by, ignoring case and spacing only."""
        return "sha256:" + payload_digest(
            {
                "intended_outcome": _normalized(self.intended_outcome),
                "workflow_ref": self.workflow_ref,
                "criteria": [
                    {
                        "statement": _normalized(criterion.statement),
                        "observability": criterion.observability.value,
                    }
                    for criterion in self.criteria
                ],
            }
        )

    def scope_column(self) -> dict[str, JsonValue]:
        """The agreed-scope object an agreement of exactly this change records."""
        return {"intended_outcome": self.intended_outcome, "workflow_ref": self.workflow_ref}


async def draft_for_agreement(
    conn: AsyncConnection[DictRow],
    *,
    product_signal_id: UUID,
    interview_session_id: UUID,
    change: ProposedChange | None,
) -> tuple[UUID, ProposedChange] | None:
    """The open proposal draft an agreement records, and the change it was drafted for.

    With no change given, the agreement is the last proposal as it stands. A given
    change matches only the last proposal with the same digest. An intake draft is
    never agreed to: only a change the agent proposed can be.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, proposed_scope, criteria from public.prepared_drafts
            where product_signal_id = %s and interview_session_id = %s
              and state = any(%s) and kind = %s
            order by proposed_at desc
            limit 1
            """,
            (product_signal_id, interview_session_id, list(OPEN_DRAFT_STATES), DraftKind.PROPOSAL),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    proposal = change_from_row(row)
    if change is not None and change.digest() != proposal.digest():
        return None
    return as_uuid(row, "id"), proposal


async def discard_open_drafts(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID, keep: UUID | None, reason: str
) -> int:
    """Discard every open draft of the case but `keep`.

    A draft whose Job never touched its workspace is settled at once; one still
    writing settles when its Job stops.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.prepared_drafts
            set state = 'discarded', failure_detail = %s,
                settled_at = case when state = 'proposed' then now() else settled_at end,
                updated_at = now()
            where product_signal_id = %s and state = any(%s)
              and (%s::uuid is null or id <> %s::uuid)
            """,
            (reason, product_signal_id, list(OPEN_DRAFT_STATES), keep, keep),
        )
        return cur.rowcount


def change_from_row(row: DictRow) -> ProposedChange:
    """The change one draft row was written for."""
    scope = row["proposed_scope"]
    criteria = row["criteria"]
    if not isinstance(scope, dict) or not isinstance(criteria, list):
        raise TypeError("prepared_drafts.proposed_scope or criteria is malformed")
    return ProposedChange(
        intended_outcome=str(scope["intended_outcome"]),
        workflow_ref=str(scope["workflow_ref"]),
        criteria=tuple(ProposedCriterion.model_validate(item) for item in criteria),
    )


def _normalized(text: str) -> str:
    return _SPACE.sub(" ", text).strip().casefold()
