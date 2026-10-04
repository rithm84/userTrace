"""Tracking how much of an issued credit an invoice actually applied.

A verified adjustment means the credit is issued, not consumed. Stripe applies
the customer's aggregate credit balance as invoices finalize, so application is
read from each invoice's own ``starting_balance`` and ``ending_balance`` and
recorded against the operation that issued the credit. The current balance alone
never says which offer an invoice consumed, and a void or a partial use
reconciles here rather than by reissuing a reward.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.connectors.stripe_credits import AppliedInvoiceCredit
from app.db.pool import Database
from app.db.rows import as_datetime, as_int, as_str, as_uuid
from app.ids import uuid7
from app.logging import get_logger
from app.offers.issuance import load_operation

__all__ = [
    "InvoiceApplication",
    "InvoiceReaderPort",
    "applied_credit_minor_units",
    "load_invoice_applications",
    "reconcile_invoice_applications",
]

_log = get_logger(__name__)

_VOID_STATUSES = frozenset({"void", "deleted"})


def applied_credit_minor_units(application: AppliedInvoiceCredit) -> int:
    """Return the credit one invoice consumed, as a positive number of minor units.

    ``StripeCredits`` reports ``starting_balance - ending_balance``. Stripe keeps
    a customer credit balance as a negative number, so consuming credit moves the
    balance up toward zero and that difference comes back negative; a positive
    difference means a debit balance was added to the invoice instead, which is
    not a reward being applied.
    """
    return max(-application.applied_balance_minor_units, 0)


@runtime_checkable
class InvoiceReaderPort(Protocol):
    """The one invoice read reconciliation performs."""

    async def read_invoice_applications(
        self, *, customer_id: str, limit: int
    ) -> tuple[AppliedInvoiceCredit, ...]:
        """Read the customer's invoices and how much credit each one applied."""
        ...


class InvoiceApplication(BaseModel):
    """One reconciled invoice application of an issued credit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    offer_issuance_operation_id: UUID
    invoice_ref: str
    currency: str
    invoice_total_minor_units: int
    applied_minor_units: int
    amount_due_minor_units: int
    invoice_status: str
    reconciled_at: datetime

    def counts_toward_application(self) -> bool:
        """Whether this row still represents credit the customer consumed."""
        return (
            self.applied_minor_units > 0 and self.invoice_status not in _VOID_STATUSES
        )


async def reconcile_invoice_applications(
    database: Database,
    invoices: InvoiceReaderPort,
    *,
    offer_issuance_operation_id: UUID,
    limit: int,
) -> tuple[InvoiceApplication, ...]:
    """Record how much credit the customer's invoices applied, from Stripe.

    Only an invoice whose own balances moved is recorded. Re-running is safe: the
    ``(operation, invoice)`` pair is unique, so a second reconciliation of the
    same invoice writes nothing new.
    """
    async with database.connection() as conn:
        operation = await load_operation(conn, operation_id=offer_issuance_operation_id)
    applications = await invoices.read_invoice_applications(
        customer_id=operation.billing_customer_ref, limit=limit
    )
    recorded = 0
    async with database.transaction() as conn, conn.cursor() as cur:
        for application in applications:
            applied = applied_credit_minor_units(application)
            if applied <= 0:
                continue
            await cur.execute(
                """
                insert into public.offer_invoice_applications (
                    id, client_id, offer_issuance_operation_id, billing_customer_ref,
                    invoice_ref, currency, invoice_total_minor_units,
                    applied_minor_units, amount_due_minor_units, invoice_status
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (offer_issuance_operation_id, invoice_ref) do nothing
                """,
                (
                    uuid7(),
                    operation.client_id,
                    operation.id,
                    operation.billing_customer_ref,
                    application.invoice_id,
                    application.currency.upper(),
                    application.total_minor_units,
                    applied,
                    application.amount_due_minor_units,
                    application.status,
                ),
            )
            recorded += cur.rowcount
        await cur.execute(
            """
            update public.offer_issuance_operations
            set reconciled_at = now(), revision = revision + 1, updated_at = now()
            where id = %s
            """,
            (operation.id,),
        )
    _log.info(
        "offer_invoice_applications_reconciled",
        client_id=str(operation.client_id),
        offer_issuance_operation_id=str(operation.id),
        billing_customer_ref=operation.billing_customer_ref,
        invoices_read=len(applications),
        applications_recorded=recorded,
    )
    return await load_invoice_applications(
        database, offer_issuance_operation_id=operation.id
    )


async def load_invoice_applications(
    database: Database, *, offer_issuance_operation_id: UUID
) -> tuple[InvoiceApplication, ...]:
    """Return the reconciled invoice applications of one issuance operation."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, offer_issuance_operation_id, invoice_ref, currency,
                   invoice_total_minor_units, applied_minor_units,
                   amount_due_minor_units, invoice_status, reconciled_at
            from public.offer_invoice_applications
            where offer_issuance_operation_id = %s
            order by reconciled_at, invoice_ref
            """,
            (offer_issuance_operation_id,),
        )
        rows = list(await cur.fetchall())
    return tuple(
        InvoiceApplication(
            id=as_uuid(row, "id"),
            offer_issuance_operation_id=as_uuid(row, "offer_issuance_operation_id"),
            invoice_ref=as_str(row, "invoice_ref"),
            currency=as_str(row, "currency"),
            invoice_total_minor_units=as_int(row, "invoice_total_minor_units"),
            applied_minor_units=as_int(row, "applied_minor_units"),
            amount_due_minor_units=as_int(row, "amount_due_minor_units"),
            invoice_status=as_str(row, "invoice_status"),
            reconciled_at=as_datetime(row, "reconciled_at"),
        )
        for row in rows
    )
