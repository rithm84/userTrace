"""The versioned concern one Product Signal investigates.

Revision 0 is routing's source-linked inference from the originating user's own
wording. The interview appends a later revision when it establishes the pain
point and validation criterion.

Contract: docs/arch-components/signal-and-outreach.md#product-signal-evidence-and-presentation
"""

from __future__ import annotations

from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from app.envelope import JsonValue
from app.ids import uuid7

__all__ = ["insert_concern"]


async def insert_concern(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    product_signal_id: UUID,
    concern_revision: int,
    origin: str,
    pain_point: str,
    validation_criterion: str,
    user_wording: JsonValue,
    unknowns: JsonValue,
) -> UUID:
    """Record one concern revision: routing's inference or the interview's finding."""
    concern_id = uuid7()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.product_signal_concerns (
                id, client_id, product_signal_id, concern_revision, origin,
                pain_point, validation_criterion, user_wording, unknowns
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                concern_id,
                client_id,
                product_signal_id,
                concern_revision,
                origin,
                pain_point,
                validation_criterion,
                Jsonb(user_wording),
                Jsonb(unknowns),
            ),
        )
    return concern_id
