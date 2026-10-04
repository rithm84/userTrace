"""The identity and application checks the reward must satisfy."""

from __future__ import annotations

import pytest

from demo_harness.billing import (
    BalanceTransaction,
    BillingError,
    FinalizedInvoice,
    verify_invoice_application,
    verify_reward_identity,
)

CUSTOMER = "cus_dudulemon_demo"


def _transaction(*, customer: str, amount: int, currency: str) -> BalanceTransaction:
    return BalanceTransaction(
        transaction_id="cbtxn_demo",
        customer_id=customer,
        amount_minor_units=amount,
        currency=currency,
        ending_balance_minor_units=amount,
        metadata={"utrace_operation_id": "01a0c380-0000-7000-8000-000000000003"},
    )


def _invoice(*, total: int, applied: int, due: int) -> FinalizedInvoice:
    return FinalizedInvoice(
        invoice_id="in_demo",
        customer_id=CUSTOMER,
        currency="USD",
        total_minor_units=total,
        applied_credit_minor_units=applied,
        amount_due_minor_units=due,
        status="open",
    )


def test_the_promised_reward_passes_identity() -> None:
    verify_reward_identity(
        _transaction(customer=CUSTOMER, amount=-25_000, currency="usd"),
        customer_id=CUSTOMER,
    )


def test_a_transaction_on_another_customer_is_refused() -> None:
    with pytest.raises(BillingError, match="not the mapped Dudulemon customer"):
        verify_reward_identity(
            _transaction(customer="cus_someone_else", amount=-25_000, currency="usd"),
            customer_id=CUSTOMER,
        )


def test_a_transaction_of_the_wrong_amount_is_refused() -> None:
    with pytest.raises(BillingError, match="not the promised"):
        verify_reward_identity(
            _transaction(customer=CUSTOMER, amount=-10_000, currency="usd"),
            customer_id=CUSTOMER,
        )


def test_a_transaction_in_the_wrong_currency_is_refused() -> None:
    with pytest.raises(BillingError, match="not usd"):
        verify_reward_identity(
            _transaction(customer=CUSTOMER, amount=-25_000, currency="eur"),
            customer_id=CUSTOMER,
        )


def test_the_scenarios_invoice_passes() -> None:
    verify_invoice_application(_invoice(total=100_000, applied=25_000, due=75_000))


def test_an_invoice_that_applied_nothing_is_refused() -> None:
    with pytest.raises(BillingError, match="minor units of credit"):
        verify_invoice_application(_invoice(total=100_000, applied=0, due=100_000))


def test_an_invoice_of_the_wrong_total_is_refused() -> None:
    with pytest.raises(BillingError, match="not the scenario's"):
        verify_invoice_application(_invoice(total=50_000, applied=25_000, due=25_000))


def test_an_invoice_leaving_the_wrong_amount_due_is_refused() -> None:
    with pytest.raises(BillingError, match="not the expected"):
        verify_invoice_application(_invoice(total=100_000, applied=25_000, due=80_000))
