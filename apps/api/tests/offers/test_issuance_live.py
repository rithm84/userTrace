"""Issuing one earned invoice credit against the live Stripe sandbox.

Nothing is mocked. The test creates its own sandbox customer, issues one credit
through the production connector, replays the issuance, reconciles it the way a
retry outside Stripe's idempotency retention window has to, finalizes a real
invoice, and deletes the customer afterwards.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
import stripe
from stripe._http_client import HTTPXClient
from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    OfferIssuanceState,
    ProductSignalState,
)

from app.connectors.stripe_credits import SANDBOX_KEY_PREFIXES, StripeCredits
from app.db.pool import Database
from app.db.rows import as_uuid
from app.offers.budget import authorize_allowance, reserve_offer
from app.offers.entitlement import (
    CaseStopReason,
    EntitlementDecision,
    evaluate_entitlement,
)
from app.offers.invoices import reconcile_invoice_applications
from app.offers.issuance import dispatch_issuance, load_operation
from app.offers.read_model import load_offers_view
from scripts.stripe_demo_fixture import (
    API_TIMEOUT_SECONDS,
    INVOICE_MINOR_UNITS,
    REWARD_MINOR_UNITS,
    finalize_demo_invoice,
)
from tests.supabase_auth import SignedInUser
from tests.validation.scenario import ValidationScenario, build_scenario

CURRENCY = "USD"
INVOICE_LIMIT = 10

ONE_CRITERION: tuple[tuple[str, CriterionObservability], ...] = (
    ("the saved view reopens with every filter", CriterionObservability.OBSERVED),
)


def _secret_key() -> str:
    key = os.environ.get("STRIPE_SANDBOX_SECRET_KEY")
    if not key:
        pytest.skip("STRIPE_SANDBOX_SECRET_KEY is unset; live issuance needs it")
    assert key.startswith(SANDBOX_KEY_PREFIXES)
    return key


def _raw_client(secret_key: str) -> stripe.StripeClient:
    return stripe.StripeClient(
        secret_key,
        max_network_retries=0,
        http_client=HTTPXClient(timeout=API_TIMEOUT_SECONDS, allow_sync_methods=False),
    )


@pytest_asyncio.fixture
async def sandbox_customer() -> AsyncIterator[tuple[str, str]]:
    """One throwaway sandbox customer, deleted when the test finishes."""
    secret_key = _secret_key()
    client = _raw_client(secret_key)
    # The fixture invoice is collected by sending it, which Stripe refuses for a
    # customer with no email, so the throwaway customer carries one.
    customer = await client.v1.customers.create_async(
        {
            "name": "uTrace issuance smoke",
            "email": "issuance-smoke@utrace-test.invalid",
            "description": "created by tests/offers/test_issuance_live.py",
            "metadata": {"utrace_test": "offer-issuance"},
        }
    )
    try:
        yield secret_key, customer.id
    finally:
        await client.v1.customers.delete_async(customer.id)


async def _earned_operation(
    database: Database,
    auth_users: dict[str, SignedInUser],
    *,
    customer_id: str,
) -> tuple[ValidationScenario, UUID]:
    """Reserve an offer for a real sandbox customer and earn its entitlement."""
    scenario = await build_scenario(
        database,
        name="issuance",
        contact_budget=2,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=auth_users["participant"].auth_user_id,
        criteria=ONE_CRITERION,
        signal_state=ProductSignalState.AWAITING_VALIDATION,
        candidate_ready=True,
    )
    await authorize_allowance(
        database,
        client_id=scenario.client_id,
        currency=CURRENCY,
        authorized_minor_units=REWARD_MINOR_UNITS * 2,
        authorized_by_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    await reserve_offer(
        database,
        client_id=scenario.client_id,
        product_signal_id=scenario.product_signal_id,
        customer_account_id=scenario.customer_account_id,
        currency=CURRENCY,
        amount_minor_units=REWARD_MINOR_UNITS,
        billing_account_ref="acct_utrace_sandbox",
        billing_customer_ref=customer_id,
        billing_mapping_revision=1,
        completion_terms={"amount_minor_units": REWARD_MINOR_UNITS},
    )
    entitlement = await evaluate_entitlement(
        database,
        product_signal_id=scenario.product_signal_id,
        stop_reason=CaseStopReason.CLIENT_STOP,
    )
    assert entitlement is not None
    assert entitlement.decision is EntitlementDecision.EARNED
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select id from public.offer_issuance_operations where client_id = %s",
            (scenario.client_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    return scenario, as_uuid(row, "id")


async def test_one_credit_per_offer_across_replays_and_reconciliation(
    database: Database,
    auth_users: dict[str, SignedInUser],
    sandbox_customer: tuple[str, str],
) -> None:
    """One adjustment survives a replayed dispatch and a metadata reconciliation."""
    secret_key, customer_id = sandbox_customer
    credits = StripeCredits(
        secret_key=secret_key, api_timeout_seconds=API_TIMEOUT_SECONDS
    )
    scenario, operation_id = await _earned_operation(
        database, auth_users, customer_id=customer_id
    )

    issued = await dispatch_issuance(database, credits, operation_id=operation_id)
    assert issued.state is OfferIssuanceState.ISSUED
    assert issued.created_adjustment is True
    assert issued.provider_transaction_ref is not None

    transactions = await credits.list_balance_transactions(
        customer_id=customer_id, limit=10
    )
    assert len(transactions) == 1
    assert transactions[0].amount_minor_units == -REWARD_MINOR_UNITS
    assert transactions[0].metadata["utrace_operation_id"] == str(operation_id)

    # A replayed dispatch reuses the recorded outcome rather than issuing again.
    replay = await dispatch_issuance(database, credits, operation_id=operation_id)
    assert replay.state is OfferIssuanceState.ISSUED
    assert replay.created_adjustment is False
    assert (
        len(await credits.list_balance_transactions(customer_id=customer_id, limit=10))
        == 1
    )

    # Outside Stripe's idempotency retention window the key alone cannot
    # deduplicate, so an uncertain operation reconciles by its metadata instead.
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.offer_issuance_operations
            set state = 'blocked', blocked_reason = 'uncertain outcome', verified_at = null,
                provider_transaction_ref = null, revision = revision + 1
            where id = %s
            """,
            (operation_id,),
        )
    reconciled = await dispatch_issuance(database, credits, operation_id=operation_id)
    assert reconciled.state is OfferIssuanceState.ISSUED
    assert reconciled.created_adjustment is False
    assert reconciled.provider_transaction_ref == issued.provider_transaction_ref
    assert (
        len(await credits.list_balance_transactions(customer_id=customer_id, limit=10))
        == 1
    )

    # Issued is not consumed: the read model shows no application until Stripe says so.
    before = await load_offers_view(
        database, client_id=scenario.client_id, currency=CURRENCY
    )
    assert before.offers[0].issuance_state is OfferIssuanceState.ISSUED
    assert before.offers[0].invoice_application_minor_units is None

    application = await finalize_demo_invoice(secret_key, customer_id=customer_id)
    assert application.applied_balance_minor_units == REWARD_MINOR_UNITS
    assert (
        application.amount_due_minor_units == INVOICE_MINOR_UNITS - REWARD_MINOR_UNITS
    )

    recorded = await reconcile_invoice_applications(
        database,
        credits,
        offer_issuance_operation_id=operation_id,
        limit=INVOICE_LIMIT,
    )
    assert len(recorded) == 1
    assert recorded[0].applied_minor_units == REWARD_MINOR_UNITS

    after = await load_offers_view(
        database, client_id=scenario.client_id, currency=CURRENCY
    )
    assert after.offers[0].invoice_application_minor_units == REWARD_MINOR_UNITS

    async with database.connection() as conn:
        operation = await load_operation(conn, operation_id=operation_id)
    assert operation.state is OfferIssuanceState.ISSUED
    assert operation.dispatch_count == 1
