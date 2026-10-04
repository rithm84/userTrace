"""Live smoke: read the Stripe sandbox billing account.

Reads only. Issuing a credit is a real, irreversible billing adjustment even in a sandbox,
and the Shopalytics demo owns its own issuance fixture, so this smoke proves the grant and
the read paths without writing. Set `UTRACE_STRIPE_SMOKE_CUSTOMER_ID` to also exercise the
balance-transaction and invoice reads against a prepared customer.
"""

from __future__ import annotations

from app.connectors.stripe_credits import SANDBOX_KEY_PREFIXES, StripeCredits
from tests.connectors.conftest import optional_env, require_env


async def test_customers_are_readable_with_the_sandbox_key() -> None:
    values = require_env("STRIPE_SANDBOX_SECRET_KEY")
    assert values["STRIPE_SANDBOX_SECRET_KEY"].startswith(SANDBOX_KEY_PREFIXES)
    credits = StripeCredits(
        secret_key=values["STRIPE_SANDBOX_SECRET_KEY"], api_timeout_seconds=30.0
    )
    customer_ids = await credits.read_customer_ids(limit=5)
    assert all(customer_id.startswith("cus_") for customer_id in customer_ids)


async def test_balance_transactions_and_invoices_are_readable() -> None:
    values = require_env("STRIPE_SANDBOX_SECRET_KEY")
    customer_id = optional_env("UTRACE_STRIPE_SMOKE_CUSTOMER_ID")
    if customer_id is None:
        credits = StripeCredits(
            secret_key=values["STRIPE_SANDBOX_SECRET_KEY"], api_timeout_seconds=30.0
        )
        existing = await credits.read_customer_ids(limit=1)
        if not existing:
            import pytest

            pytest.skip(
                "the sandbox has no customers; set UTRACE_STRIPE_SMOKE_CUSTOMER_ID "
                "to name one"
            )
        customer_id = existing[0]

    credits = StripeCredits(
        secret_key=values["STRIPE_SANDBOX_SECRET_KEY"], api_timeout_seconds=30.0
    )
    transactions = await credits.list_balance_transactions(
        customer_id=customer_id, limit=5
    )
    for transaction in transactions:
        assert transaction.customer_id == customer_id
        assert transaction.currency

    applications = await credits.read_invoice_applications(
        customer_id=customer_id, limit=5
    )
    for application in applications:
        assert application.customer_id == customer_id
        assert application.invoice_id.startswith("in_")
