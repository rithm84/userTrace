"""The Shopalytics demo's billing harness, runnable on its own.

[SCENARIO.md](../../../docs/SCENARIO.md#outreach-offers-in-the-demo) owns this
fixture: a uTrace-controlled Stripe sandbox stands in for Shopalytics' billing
account, with one existing Stripe Customer mapped to Dudulemon, a zero starting
credit balance, and USD billing.

The harness owns customer preparation and invoice creation and finalization.
uTrace's pilot adapter only issues and reconciles credits, so credit issuance
here runs through the same ``StripeCredits`` connector the product uses, with the
same idempotency key and the same verification.

No money moves: a sandbox invoice is finalized to show the credit applied, and no
charge, payout, usage meter, Credit Grant, or Connect account is involved.

Commands::

    uv run --project apps/api python apps/api/scripts/stripe_demo_fixture.py prepare-customer
    uv run --project apps/api python apps/api/scripts/stripe_demo_fixture.py issue-credit
    uv run --project apps/api python apps/api/scripts/stripe_demo_fixture.py finalize-invoice
    uv run --project apps/api python apps/api/scripts/stripe_demo_fixture.py run-demo
    uv run --project apps/api python apps/api/scripts/stripe_demo_fixture.py authorize-allowance \
        --client-id <uuid> --owner-auth-user-id <uuid>

``run-demo`` prepares the customer, issues one 25,000-minor-unit credit, and
finalizes a 1,000.00 USD invoice, then verifies that 250.00 was applied and
750.00 remains due.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID, uuid4

import stripe
from stripe._http_client import HTTPXClient

from app.connectors.stripe_credits import (
    AppliedInvoiceCredit,
    CreditAdjustment,
    StripeCredits,
)

DUDULEMON_EMAIL = "dudulemon-billing@utrace-demo.invalid"
DUDULEMON_NAME = "Dudulemon"
CURRENCY = "usd"
REWARD_MINOR_UNITS = 25_000
"""The demo's fixed reward: 250.00 USD."""
INVOICE_MINOR_UNITS = 100_000
"""The demo's service invoice: 1,000.00 USD."""
EXPECTED_DUE_MINOR_UNITS = INVOICE_MINOR_UNITS - REWARD_MINOR_UNITS
API_TIMEOUT_SECONDS = 30.0
INVOICE_DAYS_UNTIL_DUE = 30


def _secret_key() -> str:
    key = os.environ.get("STRIPE_SANDBOX_SECRET_KEY")
    if not key:
        raise SystemExit(
            "STRIPE_SANDBOX_SECRET_KEY is not set; source the repository .env.local"
        )
    return key


def _client(secret_key: str) -> stripe.StripeClient:
    return stripe.StripeClient(
        secret_key,
        max_network_retries=0,
        http_client=HTTPXClient(timeout=API_TIMEOUT_SECONDS, allow_sync_methods=False),
    )


async def prepare_customer(secret_key: str) -> str:
    """Return the Dudulemon Stripe Customer, creating it when it is absent.

    The customer is found by its fixture email rather than by search, because
    Stripe's search index lags newly created objects and this harness may run
    immediately after creating one.
    """
    configured = os.environ.get("UTRACE_STRIPE_DUDULEMON_CUSTOMER_ID")
    client = _client(secret_key)
    if configured:
        customer = await client.v1.customers.retrieve_async(configured)
        return customer.id
    existing = await client.v1.customers.list_async(
        {"email": DUDULEMON_EMAIL, "limit": 1}
    )
    if existing.data:
        return existing.data[0].id
    created = await client.v1.customers.create_async(
        {
            "name": DUDULEMON_NAME,
            "email": DUDULEMON_EMAIL,
            "description": "uTrace Shopalytics demo: the Dudulemon customer organization",
            "metadata": {"utrace_demo_account": "dudulemon"},
        }
    )
    return created.id


async def issue_demo_credit(
    secret_key: str, *, customer_id: str, operation_id: UUID
) -> CreditAdjustment:
    """Issue the demo's one earned credit through the product's own connector."""
    credits = StripeCredits(
        secret_key=secret_key, api_timeout_seconds=API_TIMEOUT_SECONDS
    )
    adjustment = await credits.issue_credit(
        customer_id=customer_id,
        amount_minor_units=REWARD_MINOR_UNITS,
        currency=CURRENCY,
        idempotency_key=f"utrace-offer-issuance-{operation_id}",
        metadata={
            "utrace_operation_id": str(operation_id),
            "utrace_demo": "shopalytics-saved-conversion-views",
        },
    )
    if adjustment.amount_minor_units != -REWARD_MINOR_UNITS:
        raise SystemExit(
            f"Stripe returned {adjustment.amount_minor_units} minor units, not the "
            f"promised credit of {-REWARD_MINOR_UNITS}"
        )
    return adjustment


async def finalize_demo_invoice(
    secret_key: str, *, customer_id: str
) -> AppliedInvoiceCredit:
    """Finalize the 1,000.00 USD service invoice and verify what it applied."""
    client = _client(secret_key)
    # The draft is created first and the line item is attached to it explicitly.
    # A pending invoice item is only swept into an invoice under conditions this
    # fixture should not depend on, and an empty invoice would finalize at zero.
    draft = await client.v1.invoices.create_async(
        {
            "customer": customer_id,
            "collection_method": "send_invoice",
            "days_until_due": INVOICE_DAYS_UNTIL_DUE,
            "auto_advance": False,
            "description": "uTrace Shopalytics demo fixture invoice",
        }
    )
    if draft.id is None:
        raise SystemExit("Stripe returned a draft invoice with no identifier")
    await client.v1.invoice_items.create_async(
        {
            "customer": customer_id,
            "invoice": draft.id,
            "amount": INVOICE_MINOR_UNITS,
            "currency": CURRENCY,
            "description": "Shopalytics service, uTrace demo fixture",
        }
    )
    finalized = await client.v1.invoices.finalize_invoice_async(draft.id)
    # Stripe keeps a customer credit balance as a negative number, so consuming
    # credit moves the balance up toward zero: the amount applied is
    # ``ending_balance - starting_balance``.
    starting = finalized.starting_balance or 0
    ending = finalized.ending_balance or 0
    applied = ending - starting
    application = AppliedInvoiceCredit(
        invoice_id=finalized.id or draft.id,
        customer_id=customer_id,
        currency=(finalized.currency or CURRENCY).upper(),
        total_minor_units=finalized.total or 0,
        applied_balance_minor_units=applied,
        amount_due_minor_units=finalized.amount_due or 0,
        status=str(finalized.status),
    )
    if application.applied_balance_minor_units != REWARD_MINOR_UNITS:
        raise SystemExit(
            f"the invoice applied {application.applied_balance_minor_units} minor "
            f"units of credit, not the expected {REWARD_MINOR_UNITS}"
        )
    if application.amount_due_minor_units != EXPECTED_DUE_MINOR_UNITS:
        raise SystemExit(
            f"the invoice leaves {application.amount_due_minor_units} minor units "
            f"due, not the expected {EXPECTED_DUE_MINOR_UNITS}"
        )
    return application


async def authorize_demo_allowance(
    *, client_id: UUID, owner_auth_user_id: UUID, authorized_minor_units: int
) -> None:
    """Record Shopalytics' issuance allowance in the control plane."""
    from app.db.pool import Database
    from app.offers.budget import authorize_allowance

    database_url = os.environ.get("UTRACE_DATABASE_URL")
    if not database_url:
        raise SystemExit(
            "UTRACE_DATABASE_URL is not set; the allowance lives in the control plane"
        )
    database = Database(dsn=database_url, min_size=1, max_size=2)
    await database.open()
    try:
        allowance = await authorize_allowance(
            database,
            client_id=client_id,
            currency=CURRENCY.upper(),
            authorized_minor_units=authorized_minor_units,
            authorized_by_auth_user_id=owner_auth_user_id,
        )
    finally:
        await database.close()
    sys.stdout.write(
        f"authorized {allowance.authorized_minor_units} minor units in "
        f"{allowance.currency}; {allowance.remaining_minor_units} remaining\n"
    )


async def _run(arguments: argparse.Namespace) -> int:
    if arguments.command == "authorize-allowance":
        await authorize_demo_allowance(
            client_id=UUID(arguments.client_id),
            owner_auth_user_id=UUID(arguments.owner_auth_user_id),
            authorized_minor_units=arguments.authorized_minor_units,
        )
        return 0

    secret_key = _secret_key()
    customer_id = await prepare_customer(secret_key)
    sys.stdout.write(f"UTRACE_STRIPE_DUDULEMON_CUSTOMER_ID={customer_id}\n")
    if arguments.command == "prepare-customer":
        return 0

    if arguments.command in {"issue-credit", "run-demo"}:
        operation_id = (
            UUID(arguments.operation_id) if arguments.operation_id else uuid4()
        )
        adjustment = await issue_demo_credit(
            secret_key, customer_id=customer_id, operation_id=operation_id
        )
        sys.stdout.write(
            f"issued {abs(adjustment.amount_minor_units)} minor units as "
            f"{adjustment.transaction_id}; balance is now "
            f"{adjustment.ending_balance_minor_units}\n"
        )
    if arguments.command in {"finalize-invoice", "run-demo"}:
        application = await finalize_demo_invoice(secret_key, customer_id=customer_id)
        sys.stdout.write(
            f"invoice {application.invoice_id} totals "
            f"{application.total_minor_units}, applied "
            f"{application.applied_balance_minor_units} of credit, leaving "
            f"{application.amount_due_minor_units} due ({application.status})\n"
        )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "prepare-customer",
            "issue-credit",
            "finalize-invoice",
            "run-demo",
            "authorize-allowance",
        ],
    )
    parser.add_argument("--operation-id", dest="operation_id", default="")
    parser.add_argument("--client-id", dest="client_id", default="")
    parser.add_argument("--owner-auth-user-id", dest="owner_auth_user_id", default="")
    parser.add_argument(
        "--authorized-minor-units",
        dest="authorized_minor_units",
        type=int,
        default=REWARD_MINOR_UNITS,
    )
    return parser


def main() -> int:
    """Run one harness command against the Stripe sandbox."""
    arguments = _parser().parse_args()
    if arguments.command == "authorize-allowance" and not (
        arguments.client_id and arguments.owner_auth_user_id
    ):
        raise SystemExit(
            "authorize-allowance needs --client-id and --owner-auth-user-id"
        )
    return asyncio.run(_run(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
