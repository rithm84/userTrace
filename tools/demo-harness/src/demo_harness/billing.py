"""The demo's Stripe sandbox side.

``docs/SCENARIO.md`` gives the harness invoice creation and finalization; uTrace's
pilot adapter only issues and reconciles credits. So nothing here issues a credit.
The harness reads the credit uTrace recorded, proves that exact Stripe object
exists with the promised identity, and then shows it applied to a service invoice.
"""

from __future__ import annotations

from typing import Final

import stripe
from pydantic import BaseModel, ConfigDict
from stripe._http_client import HTTPXClient

from demo_harness.scenario import (
    EXPECTED_INVOICE_DUE_MINOR_UNITS,
    INVOICE_MINOR_UNITS,
    REWARD_CURRENCY,
    REWARD_MINOR_UNITS,
)
from demo_harness.settings import StripeSettings

__all__ = [
    "BalanceTransaction",
    "BillingError",
    "FinalizedInvoice",
    "credit_balance_minor_units",
    "finalize_service_invoice",
    "read_balance_transaction",
    "verify_invoice_application",
    "verify_reward_identity",
]

_TIMEOUT_SECONDS: Final[float] = 30.0
_HISTORY_LIMIT: Final[int] = 100
_INVOICE_DAYS_UNTIL_DUE: Final[int] = 30
_SANDBOX_PREFIXES: Final[tuple[str, ...]] = ("sk_test_", "rk_test_")


class BillingError(RuntimeError):
    """The Stripe sandbox answered something the scenario forbids."""


class BalanceTransaction(BaseModel):
    """One customer balance transaction, as Stripe reports it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transaction_id: str
    customer_id: str
    amount_minor_units: int
    currency: str
    ending_balance_minor_units: int
    metadata: dict[str, str]


class FinalizedInvoice(BaseModel):
    """One finalized invoice and the credit it consumed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    invoice_id: str
    customer_id: str
    currency: str
    total_minor_units: int
    applied_credit_minor_units: int
    amount_due_minor_units: int
    status: str


def _metadata(raw: object) -> dict[str, str]:
    """Return a Stripe metadata bag as plain strings, or an empty one."""
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _client(settings: StripeSettings) -> stripe.StripeClient:
    """Return a sandbox-only Stripe client, refusing a live key outright."""
    if not settings.secret_key.startswith(_SANDBOX_PREFIXES):
        raise BillingError(
            "STRIPE_SANDBOX_SECRET_KEY is not a sandbox key; the demo never touches "
            "a live billing account"
        )
    return stripe.StripeClient(
        settings.secret_key,
        max_network_retries=0,
        http_client=HTTPXClient(timeout=_TIMEOUT_SECONDS, allow_sync_methods=False),
    )


async def credit_balance_minor_units(settings: StripeSettings) -> int:
    """Return the customer's current credit balance in minor units.

    Stripe keeps credit as a negative customer balance, so this returns the
    credit as a positive number: ``0`` is the scenario's required starting state.
    """
    client = _client(settings)
    customer = await client.v1.customers.retrieve_async(settings.dudulemon_customer_id)
    balance = customer.balance or 0
    if balance > 0:
        raise BillingError(
            f"the Dudulemon customer {settings.dudulemon_customer_id} owes "
            f"{balance} minor units; the fixture requires no debit balance"
        )
    return -balance


async def read_balance_transaction(
    settings: StripeSettings, *, transaction_id: str
) -> BalanceTransaction:
    """Return the balance transaction uTrace recorded, from Stripe itself.

    The identity is resolved by reading the customer's own transaction history
    rather than trusting the reference in isolation, so a reference that belongs
    to another customer cannot pass as this reward.
    """
    client = _client(settings)
    page = await client.v1.customers.balance_transactions.list_async(
        settings.dudulemon_customer_id, {"limit": _HISTORY_LIMIT}
    )
    for entry in page.data:
        if entry.id != transaction_id:
            continue
        return BalanceTransaction(
            transaction_id=entry.id,
            customer_id=str(entry.customer),
            amount_minor_units=entry.amount,
            currency=entry.currency,
            ending_balance_minor_units=entry.ending_balance,
            metadata=_metadata(entry.metadata),
        )
    raise BillingError(
        f"uTrace recorded Stripe transaction {transaction_id}, but it is not among the "
        f"last {_HISTORY_LIMIT} balance transactions of customer "
        f"{settings.dudulemon_customer_id}"
    )


def verify_reward_identity(transaction: BalanceTransaction, *, customer_id: str) -> None:
    """Raise unless one transaction is the scenario's promised reward."""
    if transaction.customer_id != customer_id:
        raise BillingError(
            f"transaction {transaction.transaction_id} credits customer "
            f"{transaction.customer_id}, not the mapped Dudulemon customer {customer_id}"
        )
    if transaction.amount_minor_units != -REWARD_MINOR_UNITS:
        raise BillingError(
            f"transaction {transaction.transaction_id} moved "
            f"{transaction.amount_minor_units} minor units, not the promised "
            f"{-REWARD_MINOR_UNITS}"
        )
    if transaction.currency.lower() != REWARD_CURRENCY:
        raise BillingError(
            f"transaction {transaction.transaction_id} is in {transaction.currency}, "
            f"not {REWARD_CURRENCY}"
        )


async def finalize_service_invoice(settings: StripeSettings) -> FinalizedInvoice:
    """Finalize the 1,000.00 USD Shopalytics service invoice.

    The draft is created first and the line item attached to it explicitly: a
    pending invoice item is only swept in under conditions the demo must not
    depend on, and an empty invoice would finalize at zero.
    """
    client = _client(settings)
    customer_id = settings.dudulemon_customer_id
    draft = await client.v1.invoices.create_async(
        {
            "customer": customer_id,
            "collection_method": "send_invoice",
            "days_until_due": _INVOICE_DAYS_UNTIL_DUE,
            "auto_advance": False,
            "description": "Shopalytics service, uTrace demo",
        }
    )
    if draft.id is None:
        raise BillingError("Stripe returned a draft invoice with no identifier")
    await client.v1.invoice_items.create_async(
        {
            "customer": customer_id,
            "invoice": draft.id,
            "amount": INVOICE_MINOR_UNITS,
            "currency": REWARD_CURRENCY,
            "description": "Shopalytics service, uTrace demo",
        }
    )
    finalized = await client.v1.invoices.finalize_invoice_async(draft.id)
    # Credit is a negative balance, so consuming it moves the balance up toward
    # zero: the amount applied is ``ending_balance - starting_balance``.
    starting = finalized.starting_balance or 0
    ending = finalized.ending_balance or 0
    return FinalizedInvoice(
        invoice_id=finalized.id or draft.id,
        customer_id=customer_id,
        currency=(finalized.currency or REWARD_CURRENCY).upper(),
        total_minor_units=finalized.total or 0,
        applied_credit_minor_units=ending - starting,
        amount_due_minor_units=finalized.amount_due or 0,
        status=str(finalized.status),
    )


def verify_invoice_application(invoice: FinalizedInvoice) -> None:
    """Raise unless the finalized invoice shows 250.00 applied and 750.00 due."""
    if invoice.total_minor_units != INVOICE_MINOR_UNITS:
        raise BillingError(
            f"invoice {invoice.invoice_id} totals {invoice.total_minor_units} minor "
            f"units, not the scenario's {INVOICE_MINOR_UNITS}"
        )
    if invoice.applied_credit_minor_units != REWARD_MINOR_UNITS:
        raise BillingError(
            f"invoice {invoice.invoice_id} applied {invoice.applied_credit_minor_units} "
            f"minor units of credit, not the earned {REWARD_MINOR_UNITS}"
        )
    if invoice.amount_due_minor_units != EXPECTED_INVOICE_DUE_MINOR_UNITS:
        raise BillingError(
            f"invoice {invoice.invoice_id} leaves {invoice.amount_due_minor_units} minor "
            f"units due, not the expected {EXPECTED_INVOICE_DUE_MINOR_UNITS}"
        )
