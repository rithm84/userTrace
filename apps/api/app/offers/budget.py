"""Issuance-allowance authorization and offer reservations.

The owner authorizes a client/currency ceiling on the face value of credits
uTrace may issue. No money is deposited with uTrace and no balance is
transferred: the ceiling limits promises, and existing rewards and reservations
count against it.

Reserving an offer and committing the invitation intent happen in one
transaction, under a lock on the client's authorization row, so concurrent
invitations cannot both fit into the same remaining allowance.
"""

from __future__ import annotations

from uuid import UUID

from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import OfferLedgerEntryKind, OfferState

from app.db.pool import Database
from app.envelope import JsonValue
from app.errors import BudgetExhaustedError, GuardRejectedError
from app.ids import uuid7
from app.logging import get_logger
from app.offers.ledger import Allowance, current_allowance, lock_allowance, record_entry

__all__ = ["authorize_allowance", "read_allowance", "reserve_offer"]

_log = get_logger(__name__)


async def read_allowance(
    database: Database, *, client_id: UUID, currency: str | None
) -> Allowance:
    """Return the client's current issuance allowance."""
    async with database.connection() as conn:
        return await current_allowance(conn, client_id=client_id, currency=currency)


async def authorize_allowance(
    database: Database,
    *,
    client_id: UUID,
    currency: str,
    authorized_minor_units: int,
    authorized_by_auth_user_id: UUID,
) -> Allowance:
    """Record one owner-authorized issuance ceiling for a client and currency.

    A ceiling below what is already reserved or earned is refused: a budget edit
    cannot cancel outstanding promises or silently reset consumed allowance.
    """
    if currency != currency.upper() or len(currency) != 3:
        raise GuardRejectedError(
            machine="outreach_offer",
            trigger="authorize_budget",
            reason=f"'{currency}' is not an upper-case ISO currency code",
        )
    if authorized_minor_units < 0:
        raise GuardRejectedError(
            machine="outreach_offer",
            trigger="authorize_budget",
            reason="an issuance ceiling cannot be negative",
        )
    async with database.transaction() as conn:
        allowance = await lock_allowance(conn, client_id=client_id, currency=currency)
        consumed = allowance.reserved_minor_units + allowance.earned_minor_units
        if authorized_minor_units < consumed:
            raise GuardRejectedError(
                machine="outreach_offer",
                trigger="authorize_budget",
                reason=(
                    f"the requested ceiling {authorized_minor_units} is below the "
                    f"{consumed} minor units already reserved or earned"
                ),
            )
        sequence_number = allowance.sequence_number + 1
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.offer_budgets (
                    id, client_id, currency, sequence_number, authorized_minor_units,
                    authorized_by_auth_user_id
                )
                values (%s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid7(),
                    client_id,
                    currency,
                    sequence_number,
                    authorized_minor_units,
                    authorized_by_auth_user_id,
                ),
            )
        await record_entry(
            conn,
            client_id=client_id,
            currency=currency,
            entry_kind=OfferLedgerEntryKind.BUDGET_AUTHORIZED,
            delta_minor_units=authorized_minor_units - allowance.authorized_minor_units,
            outreach_offer_id=None,
            offer_entitlement_id=None,
            offer_issuance_operation_id=None,
            causal_evidence={
                "sequence_number": sequence_number,
                "previous_authorized_minor_units": allowance.authorized_minor_units,
                "authorized_by_auth_user_id": str(authorized_by_auth_user_id),
            },
        )
        updated = await lock_allowance(conn, client_id=client_id, currency=currency)
    _log.info(
        "offer_allowance_authorized",
        client_id=str(client_id),
        currency=currency,
        authorized_minor_units=authorized_minor_units,
        remaining_minor_units=updated.remaining_minor_units,
    )
    return updated


async def reserve_offer(
    database: Database,
    *,
    client_id: UUID,
    product_signal_id: UUID,
    customer_account_id: UUID,
    currency: str,
    amount_minor_units: int,
    billing_account_ref: str,
    billing_customer_ref: str,
    billing_mapping_revision: int,
    completion_terms: dict[str, JsonValue],
) -> UUID:
    """Reserve one offer's full face value against the client's allowance."""
    offer_id = uuid7()
    async with database.transaction() as conn:
        allowance = await lock_allowance(conn, client_id=client_id, currency=currency)
        if amount_minor_units > allowance.remaining_minor_units:
            raise BudgetExhaustedError(
                budget=f"outreach_offer_allowance:{currency}",
                consumed=allowance.reserved_minor_units + allowance.earned_minor_units,
                allowance=allowance.authorized_minor_units,
            )
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
                    currency,
                    amount_minor_units,
                    billing_account_ref,
                    billing_customer_ref,
                    billing_mapping_revision,
                    Jsonb(completion_terms),
                ),
            )
            await cur.execute(
                """
                insert into public.offer_reservations (
                    id, client_id, outreach_offer_id, currency, amount_minor_units
                )
                values (%s, %s, %s, %s, %s)
                """,
                (uuid7(), client_id, offer_id, currency, amount_minor_units),
            )
        await record_entry(
            conn,
            client_id=client_id,
            currency=currency,
            entry_kind=OfferLedgerEntryKind.RESERVATION_CREATED,
            delta_minor_units=-amount_minor_units,
            outreach_offer_id=offer_id,
            offer_entitlement_id=None,
            offer_issuance_operation_id=None,
            causal_evidence={
                "product_signal_id": str(product_signal_id),
                "customer_account_id": str(customer_account_id),
                "billing_customer_ref": billing_customer_ref,
            },
        )
    _log.info(
        "outreach_offer_reserved",
        client_id=str(client_id),
        product_signal_id=str(product_signal_id),
        outreach_offer_id=str(offer_id),
        currency=currency,
        amount_minor_units=amount_minor_units,
        remaining_minor_units=allowance.remaining_minor_units - amount_minor_units,
    )
    return offer_id
