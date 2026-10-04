"""Per-person and per-client private-contact budgets.

Invitations and nudges follow a policy-pinned contact budget. Consumption is the
count of Outreach Attempts this client actually dispatched, so a scheduled
attempt that never reached the provider consumes nothing and a replacement link
consumes the allowance exactly once.

Contract: docs/arch-components/signal-and-outreach.md#originating-user-and-invitation-lifecycle
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.clients.configuration import ContactBudget
from app.db.rows import as_int

__all__ = ["ContactAllowance", "contact_allowance"]


@dataclass(frozen=True, slots=True)
class ContactAllowance:
    """What the pinned contact budget leaves for one more private message."""

    per_person_remaining: int
    per_client_remaining: int

    @property
    def remaining(self) -> int:
        """Return the binding allowance: the smaller of the two budgets."""
        return min(self.per_person_remaining, self.per_client_remaining)

    def exhausted(self) -> bool:
        """Return whether no private contact may be sent."""
        return self.remaining <= 0


async def contact_allowance(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    client_user_id: UUID,
    budget: ContactBudget,
) -> ContactAllowance:
    """Return the remaining private-contact allowance under the pinned budget."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select
              count(*) filter (where client_user_id = %s) as for_person,
              count(*) as for_client
            from public.outreach_attempts
            where client_id = %s and dispatched_at is not null
            """,
            (client_user_id, client_id),
        )
        row = await cur.fetchone()
    for_person = as_int(row, "for_person") if row is not None else 0
    for_client = as_int(row, "for_client") if row is not None else 0
    return ContactAllowance(
        per_person_remaining=budget.per_person - for_person,
        per_client_remaining=budget.per_client - for_client,
    )
