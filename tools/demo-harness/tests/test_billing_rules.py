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


@pytest.mark.parametrize(
    ("customer", "amount", "currency", "refusal"),
    [
        ("cus_someone_else", -25_000, "usd", "not the mapped Dudulemon customer"),
        (CUSTOMER, -10_000, "usd", "not the promised"),
        (CUSTOMER, -25_000, "eur", "not usd"),
    ],
)
def test_a_reward_off_the_promise_is_refused(
    customer: str, amount: int, currency: str, refusal: str
) -> None:
    with pytest.raises(BillingError, match=refusal):
        verify_reward_identity(
            _transaction(customer=customer, amount=amount, currency=currency),
            customer_id=CUSTOMER,
        )


def test_the_scenarios_invoice_passes() -> None:
    verify_invoice_application(_invoice(total=100_000, applied=25_000, due=75_000))


@pytest.mark.parametrize(
    ("total", "applied", "due", "refusal"),
    [
        (100_000, 0, 100_000, "minor units of credit"),
        (50_000, 25_000, 25_000, "not the scenario's"),
        (100_000, 25_000, 80_000, "not the expected"),
    ],
)
def test_an_invoice_off_the_scenario_is_refused(
    total: int, applied: int, due: int, refusal: str
) -> None:
    with pytest.raises(BillingError, match=refusal):
        verify_invoice_application(_invoice(total=total, applied=applied, due=due))
