"""The append-only client/currency allowance ledger.

Every movement is one immutable row in signed integer minor units: a reservation
and an issued credit consume allowance, a release returns it. The current
allowance is read by locking the client's authorization row, so two concurrent
invitations cannot both fit into the same remaining amount.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import OfferLedgerEntryKind

from app.db.rows import as_int, as_str
from app.envelope import JsonValue
from app.ids import uuid7

__all__ = ["Allowance", "current_allowance", "lock_allowance", "record_entry"]


@dataclass(frozen=True, slots=True)
class Allowance:
    """One client/currency issuance allowance and what has consumed it."""

    currency: str | None
    sequence_number: int
    authorized_minor_units: int
    reserved_minor_units: int
    earned_minor_units: int

    @property
    def remaining_minor_units(self) -> int:
        """Allowance still available for a new rewarded invitation."""
        return (
            self.authorized_minor_units
            - self.reserved_minor_units
            - self.earned_minor_units
        )


async def _totals(
    conn: AsyncConnection[DictRow], *, client_id: UUID, currency: str
) -> tuple[int, int]:
    """Return the open reservations and the earned obligations, in minor units."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            -- `sum` over bigint returns numeric, so each total is cast back to an
            -- integer: minor units are integers, never a scaled decimal.
            select coalesce((
                     select sum(amount_minor_units) from public.offer_reservations
                     where client_id = %s and currency = %s
                       and released_at is null and converted_at is null
                   ), 0)::bigint as reserved,
                   coalesce((
                     select sum(amount_minor_units) from public.offer_entitlements
                     where client_id = %s and currency = %s
                   ), 0)::bigint as earned
            """,
            (client_id, currency, client_id, currency),
        )
        row = await cur.fetchone()
    if row is None:
        return 0, 0
    return as_int(row, "reserved"), as_int(row, "earned")


async def _authorization(
    conn: AsyncConnection[DictRow], *, client_id: UUID, currency: str | None, lock: bool
) -> tuple[str, int, int] | None:
    """Return the current authorization row for a client, optionally locked."""
    suffix = " for update" if lock else ""
    async with conn.cursor() as cur:
        if currency is None:
            await cur.execute(
                "select currency, sequence_number, authorized_minor_units "
                "from public.offer_budgets where client_id = %s "
                f"order by sequence_number desc limit 1{suffix}",
                (client_id,),
            )
        else:
            await cur.execute(
                "select currency, sequence_number, authorized_minor_units "
                "from public.offer_budgets where client_id = %s and currency = %s "
                f"order by sequence_number desc limit 1{suffix}",
                (client_id, currency),
            )
        row = await cur.fetchone()
    if row is None:
        return None
    return (
        as_str(row, "currency"),
        as_int(row, "sequence_number"),
        as_int(row, "authorized_minor_units"),
    )


async def current_allowance(
    conn: AsyncConnection[DictRow], *, client_id: UUID, currency: str | None
) -> Allowance:
    """Read the client's authorized, reserved, earned, and remaining allowance."""
    authorization = await _authorization(
        conn, client_id=client_id, currency=currency, lock=False
    )
    if authorization is None:
        return Allowance(
            currency=currency,
            sequence_number=0,
            authorized_minor_units=0,
            reserved_minor_units=0,
            earned_minor_units=0,
        )
    resolved, sequence_number, authorized = authorization
    reserved, earned = await _totals(conn, client_id=client_id, currency=resolved)
    return Allowance(
        currency=resolved,
        sequence_number=sequence_number,
        authorized_minor_units=authorized,
        reserved_minor_units=reserved,
        earned_minor_units=earned,
    )


async def lock_allowance(
    conn: AsyncConnection[DictRow], *, client_id: UUID, currency: str
) -> Allowance:
    """Lock the client's authorization row, then read the allowance under it.

    Serializing on the authorization row is what makes concurrent invitations
    unable to exceed the remaining allowance: the second transaction waits, then
    sees the first reservation.
    """
    authorization = await _authorization(
        conn, client_id=client_id, currency=currency, lock=True
    )
    if authorization is None:
        return Allowance(
            currency=currency,
            sequence_number=0,
            authorized_minor_units=0,
            reserved_minor_units=0,
            earned_minor_units=0,
        )
    resolved, sequence_number, authorized = authorization
    reserved, earned = await _totals(conn, client_id=client_id, currency=resolved)
    return Allowance(
        currency=resolved,
        sequence_number=sequence_number,
        authorized_minor_units=authorized,
        reserved_minor_units=reserved,
        earned_minor_units=earned,
    )


async def record_entry(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    currency: str,
    entry_kind: OfferLedgerEntryKind,
    delta_minor_units: int,
    outreach_offer_id: UUID | None,
    offer_entitlement_id: UUID | None,
    offer_issuance_operation_id: UUID | None,
    causal_evidence: dict[str, JsonValue],
) -> UUID:
    """Append one immutable movement to the client's allowance ledger."""
    entry_id = uuid7()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.offer_ledger_entries (
                id, client_id, currency, entry_kind, delta_minor_units,
                outreach_offer_id, offer_entitlement_id, offer_issuance_operation_id,
                causal_evidence
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                entry_id,
                client_id,
                currency,
                entry_kind.value,
                delta_minor_units,
                outreach_offer_id,
                offer_entitlement_id,
                offer_issuance_operation_id,
                Jsonb(causal_evidence),
            ),
        )
    return entry_id
