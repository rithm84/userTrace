"""The authenticated Outreach Offers read model.

The dashboard shows the authorized, reserved, remaining, and earned allowance,
each offer's disposition and issuance status, and invoice application **only**
when it has been reconciled from Stripe. An issued credit is not a consumed one,
so a successful issuance alone never renders as an applied amount.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from utrace_contracts.domain.lifecycle import OfferIssuanceState, OfferState

from app.db.pool import Database
from app.db.rows import as_int, as_optional_str, as_str, as_uuid
from app.offers.ledger import current_allowance

__all__ = ["OfferDisposition", "OffersView", "load_offer", "load_offers_view"]


class OfferDisposition(BaseModel):
    """One offer's disposition, issuance status, and reconciled application."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outreach_offer_id: UUID
    product_signal_id: UUID
    customer_account_id: UUID
    currency: str
    amount_minor_units: int
    state: OfferState
    issuance_state: OfferIssuanceState | None
    provider_transaction_ref: str | None
    issuance_blocked_reason: str | None
    invoice_application_minor_units: int | None
    """``None`` until an invoice application has been reconciled from Stripe."""


class OffersView(BaseModel):
    """The client's allowance and every offer written against it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    currency: str | None
    authorized_minor_units: int
    reserved_minor_units: int
    earned_minor_units: int
    remaining_minor_units: int
    offers: tuple[OfferDisposition, ...]


_OFFER_QUERY = """
    select offers.id, offers.product_signal_id, offers.customer_account_id,
           offers.currency, offers.amount_minor_units, offers.state,
           operations.state as issuance_state,
           operations.provider_transaction_ref,
           operations.blocked_reason as issuance_blocked_reason,
           case
             when operations.reconciled_at is null then null
             else coalesce((
               select sum(applications.applied_minor_units)
               from public.offer_invoice_applications as applications
               where applications.offer_issuance_operation_id = operations.id
                 and applications.invoice_status not in ('void', 'deleted')
             ), 0)::bigint
           end as invoice_application_minor_units
    from public.outreach_offers as offers
    left join public.offer_entitlements as entitlements
      on entitlements.outreach_offer_id = offers.id
    left join public.offer_issuance_operations as operations
      on operations.offer_entitlement_id = entitlements.id
    where offers.client_id = %s
"""


def _to_disposition(row: dict[str, object]) -> OfferDisposition:
    issuance = as_optional_str(row, "issuance_state")
    applied = row["invoice_application_minor_units"]
    return OfferDisposition(
        outreach_offer_id=as_uuid(row, "id"),
        product_signal_id=as_uuid(row, "product_signal_id"),
        customer_account_id=as_uuid(row, "customer_account_id"),
        currency=as_str(row, "currency"),
        amount_minor_units=as_int(row, "amount_minor_units"),
        state=OfferState(as_str(row, "state")),
        issuance_state=None if issuance is None else OfferIssuanceState(issuance),
        provider_transaction_ref=as_optional_str(row, "provider_transaction_ref"),
        issuance_blocked_reason=as_optional_str(row, "issuance_blocked_reason"),
        invoice_application_minor_units=None if applied is None else int(str(applied)),
    )


async def load_offers_view(
    database: Database, *, client_id: UUID, currency: str | None
) -> OffersView:
    """Return the client's allowance and the disposition of every offer."""
    async with database.connection() as conn:
        allowance = await current_allowance(
            conn, client_id=client_id, currency=currency
        )
        async with conn.cursor() as cur:
            await cur.execute(
                f"{_OFFER_QUERY} order by offers.created_at desc", (client_id,)
            )
            rows = list(await cur.fetchall())
    return OffersView(
        currency=allowance.currency,
        authorized_minor_units=allowance.authorized_minor_units,
        reserved_minor_units=allowance.reserved_minor_units,
        earned_minor_units=allowance.earned_minor_units,
        remaining_minor_units=allowance.remaining_minor_units,
        offers=tuple(_to_disposition(dict(row)) for row in rows),
    )


async def load_offer(
    database: Database, *, client_id: UUID, outreach_offer_id: UUID
) -> OfferDisposition | None:
    """Return one offer's disposition, scoped to its client."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(f"{_OFFER_QUERY} and offers.id = %s", (client_id, outreach_offer_id))
        row = await cur.fetchone()
    return None if row is None else _to_disposition(dict(row))
