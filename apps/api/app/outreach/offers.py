"""Outreach Offers: the authorized allowance, and reserving against it.

The owner authorizes a client and currency issuance ceiling. Reserving the full
promised credit and committing the invitation intent happen in one transaction,
so concurrent invitations cannot together exceed the remaining allowance. The
budget row is locked first, which is what serializes them.

One offer covers one case. Re-contact and revisions reuse it; they never create a
second reward. An unearned offer releases its reservation once outreach and
participation are reconciled.

Amounts are integer minor units throughout. Nothing here rounds or derives an
amount from a model output.

Contract: docs/arch-components/outreach-offers.md#budget-and-offer-reservations
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import OfferState

from app.clients.configuration import OutreachOfferPolicy
from app.db.pool import Database
from app.db.rows import as_int, as_str, as_uuid
from app.envelope import JsonValue
from app.errors import BudgetExhaustedError, GuardRejectedError
from app.ids import uuid7
from app.logging import get_logger

__all__ = [
    "Allowance",
    "ReservedOffer",
    "release_reservation",
    "remaining_allowance",
    "reserve_offer",
]

_log = get_logger(__name__)

_ENTRY_RESERVATION_CREATED = "reservation_created"
_ENTRY_RESERVATION_RELEASED = "reservation_released"


@dataclass(frozen=True, slots=True)
class Allowance:
    """The client's authorized ceiling and what it has already committed."""

    currency: str
    authorized_minor_units: int
    reserved_minor_units: int
    earned_minor_units: int

    @property
    def remaining_minor_units(self) -> int:
        """Return the face value still available for a new rewarded invitation."""
        return (
            self.authorized_minor_units
            - self.reserved_minor_units
            - self.earned_minor_units
        )


@dataclass(frozen=True, slots=True)
class ReservedOffer:
    """One offer and its open reservation."""

    outreach_offer_id: UUID
    offer_reservation_id: UUID
    currency: str
    amount_minor_units: int
    billing_customer_ref: str
    already_reserved: bool


async def _record_ledger_entry(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    currency: str,
    entry_kind: str,
    delta_minor_units: int,
    outreach_offer_id: UUID | None,
    causal_evidence: JsonValue,
) -> None:
    """Append one signed movement to the client and currency allowance record."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.offer_ledger_entries (
                id, client_id, currency, entry_kind, delta_minor_units,
                outreach_offer_id, causal_evidence
            )
            values (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                uuid7(),
                client_id,
                currency,
                entry_kind,
                delta_minor_units,
                outreach_offer_id,
                Jsonb(causal_evidence),
            ),
        )


async def _allowance_on_connection(
    conn: AsyncConnection[DictRow], *, client_id: UUID, currency: str
) -> Allowance:
    """Compute the allowance from the authorized ceiling and its commitments."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select authorized_minor_units from public.offer_budgets
            where client_id = %s and currency = %s
            order by sequence_number desc limit 1
            for update
            """,
            (client_id, currency),
        )
        budget = await cur.fetchone()
        if budget is None:
            raise GuardRejectedError(
                machine="outreach_offer",
                trigger="reserve",
                reason=(
                    f"client {client_id} has authorized no {currency} issuance budget"
                ),
            )
        await cur.execute(
            """
            select coalesce(sum(amount_minor_units), 0)::bigint as reserved
            from public.offer_reservations
            where client_id = %s and currency = %s
              and released_at is null and converted_at is null
            """,
            (client_id, currency),
        )
        reserved = await cur.fetchone()
        await cur.execute(
            """
            select coalesce(sum(amount_minor_units), 0)::bigint as earned
            from public.offer_entitlements
            where client_id = %s and currency = %s
            """,
            (client_id, currency),
        )
        earned = await cur.fetchone()
    return Allowance(
        currency=currency,
        authorized_minor_units=as_int(budget, "authorized_minor_units"),
        reserved_minor_units=as_int(reserved, "reserved") if reserved is not None else 0,
        earned_minor_units=as_int(earned, "earned") if earned is not None else 0,
    )


async def remaining_allowance(
    database: Database, *, client_id: UUID, currency: str
) -> Allowance:
    """Return the client's allowance for one currency."""
    async with database.transaction() as conn:
        return await _allowance_on_connection(
            conn, client_id=client_id, currency=currency
        )


async def reserve_offer(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    product_signal_id: UUID,
    customer_account_id: UUID,
    policy: OutreachOfferPolicy,
    billing_customer_ref: str,
    billing_mapping_revision: int,
) -> ReservedOffer:
    """Reserve the fixed offer for one case, inside the caller's transaction.

    The caller commits the invitation intent in the same transaction, so a
    reservation never exists without the outreach it was taken for. A second call
    for the same Product Signal returns the existing reservation.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select offers.id as outreach_offer_id, reservations.id as reservation_id,
                   offers.currency, offers.amount_minor_units,
                   offers.billing_customer_ref
            from public.outreach_offers as offers
            join public.offer_reservations as reservations
              on reservations.outreach_offer_id = offers.id
            where offers.product_signal_id = %s
            """,
            (product_signal_id,),
        )
        existing = await cur.fetchone()
    if existing is not None:
        return ReservedOffer(
            outreach_offer_id=as_uuid(existing, "outreach_offer_id"),
            offer_reservation_id=as_uuid(existing, "reservation_id"),
            currency=as_str(existing, "currency"),
            amount_minor_units=as_int(existing, "amount_minor_units"),
            billing_customer_ref=as_str(existing, "billing_customer_ref"),
            already_reserved=True,
        )

    allowance = await _allowance_on_connection(
        conn, client_id=client_id, currency=policy.currency
    )
    amount = policy.fixed_amount_minor_units
    if allowance.remaining_minor_units < amount:
        raise BudgetExhaustedError(
            budget=f"outreach_offer_allowance_{policy.currency}",
            consumed=allowance.authorized_minor_units - allowance.remaining_minor_units,
            allowance=allowance.authorized_minor_units,
        )

    offer_id = uuid7()
    reservation_id = uuid7()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.outreach_offers (
                id, client_id, product_signal_id, customer_account_id, state,
                currency, amount_minor_units, billing_account_ref,
                billing_customer_ref, billing_mapping_revision, completion_terms
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                offer_id,
                client_id,
                product_signal_id,
                customer_account_id,
                OfferState.RESERVED.value,
                policy.currency,
                amount,
                policy.billing_account_ref,
                billing_customer_ref,
                billing_mapping_revision,
                Jsonb(
                    {
                        "billing_use": policy.billing_use,
                        "conditions": list(policy.completion_terms),
                    }
                ),
            ),
        )
        await cur.execute(
            """
            insert into public.offer_reservations (
                id, client_id, outreach_offer_id, currency, amount_minor_units
            )
            values (%s, %s, %s, %s, %s)
            """,
            (reservation_id, client_id, offer_id, policy.currency, amount),
        )
    await _record_ledger_entry(
        conn,
        client_id=client_id,
        currency=policy.currency,
        entry_kind=_ENTRY_RESERVATION_CREATED,
        delta_minor_units=-amount,
        outreach_offer_id=offer_id,
        causal_evidence={
            "product_signal_id": str(product_signal_id),
            "customer_account_id": str(customer_account_id),
            "offer_reservation_id": str(reservation_id),
        },
    )
    _log.info(
        "outreach_offer_reserved",
        client_id=str(client_id),
        product_signal_id=str(product_signal_id),
        outreach_offer_id=str(offer_id),
        currency=policy.currency,
        amount_minor_units=amount,
        remaining_minor_units=allowance.remaining_minor_units - amount,
    )
    return ReservedOffer(
        outreach_offer_id=offer_id,
        offer_reservation_id=reservation_id,
        currency=policy.currency,
        amount_minor_units=amount,
        billing_customer_ref=billing_customer_ref,
        already_reserved=False,
    )


async def release_reservation(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    product_signal_id: UUID,
    reason: str,
) -> int:
    """Release an unearned offer's reservation and return the amount returned.

    Releasing is idempotent: an already released or converted reservation returns
    zero rather than crediting the allowance twice.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.offer_reservations as reservations
            set released_at = now(), updated_at = now()
            from public.outreach_offers as offers
            where offers.id = reservations.outreach_offer_id
              and offers.product_signal_id = %s
              and reservations.released_at is null
              and reservations.converted_at is null
            returning reservations.id, reservations.currency,
                      reservations.amount_minor_units, offers.id as offer_id
            """,
            (product_signal_id,),
        )
        released = await cur.fetchone()
        if released is None:
            return 0
        await cur.execute(
            """
            update public.outreach_offers
            set state = %s, revision = revision + 1, updated_at = now()
            where id = %s and state = %s
            """,
            (
                OfferState.RELEASED.value,
                as_uuid(released, "offer_id"),
                OfferState.RESERVED.value,
            ),
        )
    amount = as_int(released, "amount_minor_units")
    await _record_ledger_entry(
        conn,
        client_id=client_id,
        currency=as_str(released, "currency"),
        entry_kind=_ENTRY_RESERVATION_RELEASED,
        delta_minor_units=amount,
        outreach_offer_id=as_uuid(released, "offer_id"),
        causal_evidence={"product_signal_id": str(product_signal_id), "reason": reason},
    )
    _log.info(
        "outreach_offer_reservation_released",
        client_id=str(client_id),
        product_signal_id=str(product_signal_id),
        amount_minor_units=amount,
        reason=reason,
    )
    return amount
