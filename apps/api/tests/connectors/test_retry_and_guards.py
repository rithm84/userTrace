"""Pure tests for the retry rule and the connector configuration guards.

`retry_safe_call` is the only place a connector repeats a provider call, so its refusal
rules matter as much as its success path. The guards cover configuration mistakes that
would otherwise reach a real provider: a live Stripe key and a sandbox header injection
for a domain the egress policy does not allow.
"""

from __future__ import annotations

import pytest

from app.connectors.failures import (
    ConnectorFailure,
    EffectState,
    FailureDetail,
    RetryAdvice,
    TechnicalCategory,
)
from app.connectors.github_delivery import idempotency_marker
from app.connectors.retry import RetryPolicy, retry_safe_call
from app.connectors.stripe_credits import StripeCredits, reconcile_issued_credit
from app.connectors.vercel_sandboxes import EgressPolicy, HeaderInjection

_FAST = RetryPolicy(
    max_attempts=3,
    initial_delay_seconds=0.0,
    backoff_multiplier=1.0,
    safety_justification="read-only test call",
)


def _failure(
    *, category: TechnicalCategory, advice: RetryAdvice, effect: EffectState
) -> ConnectorFailure:
    return ConnectorFailure(
        FailureDetail(
            provider="test",
            operation="call",
            category=category,
            retry_advice=advice,
            effect_state=effect,
            message="synthetic",
        )
    )


async def test_a_transient_absent_failure_is_retried_then_succeeds() -> None:
    attempts = 0

    async def call() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _failure(
                category=TechnicalCategory.RATE_LIMITED,
                advice=RetryAdvice.AFTER_DELAY,
                effect=EffectState.ABSENT,
            )
        return "ok"

    assert await retry_safe_call(operation="call", policy=_FAST, call=call) == "ok"
    assert attempts == 3


async def test_the_last_failure_is_raised_once_attempts_are_exhausted() -> None:
    attempts = 0

    async def call() -> str:
        nonlocal attempts
        attempts += 1
        raise _failure(
            category=TechnicalCategory.PROVIDER_UNAVAILABLE,
            advice=RetryAdvice.AFTER_DELAY,
            effect=EffectState.ABSENT,
        )

    with pytest.raises(ConnectorFailure) as raised:
        await retry_safe_call(operation="call", policy=_FAST, call=call)
    assert raised.value.category is TechnicalCategory.PROVIDER_UNAVAILABLE
    assert attempts == 3


async def test_an_indeterminate_effect_is_never_retried() -> None:
    """A call that may have half-applied is reconciled by its owner, never repeated."""
    attempts = 0

    async def call() -> str:
        nonlocal attempts
        attempts += 1
        raise _failure(
            category=TechnicalCategory.PROVIDER_UNAVAILABLE,
            advice=RetryAdvice.AFTER_DELAY,
            effect=EffectState.INDETERMINATE,
        )

    with pytest.raises(ConnectorFailure):
        await retry_safe_call(operation="call", policy=_FAST, call=call)
    assert attempts == 1


async def test_a_permanent_failure_is_never_retried() -> None:
    attempts = 0

    async def call() -> str:
        nonlocal attempts
        attempts += 1
        raise _failure(
            category=TechnicalCategory.PROVIDER_REJECTED,
            advice=RetryAdvice.NEVER,
            effect=EffectState.ABSENT,
        )

    with pytest.raises(ConnectorFailure):
        await retry_safe_call(operation="call", policy=_FAST, call=call)
    assert attempts == 1


def test_stripe_refuses_a_key_without_a_sandbox_prefix() -> None:
    with pytest.raises(ConnectorFailure) as raised:
        StripeCredits(secret_key="sk_live_not_a_sandbox", api_timeout_seconds=5.0)
    assert raised.value.category is TechnicalCategory.CONFIGURATION
    assert "sk_live_not_a_sandbox" not in str(raised.value)


def test_an_egress_injection_must_name_an_allowed_domain() -> None:
    """The firewall transforms only traffic it permits, so an orphan injection is refused."""
    policy = EgressPolicy(
        allowed_domains=("registry.npmjs.org",),
        injections=(HeaderInjection(domain="api.openai.com", headers={"authorization": "k"}),),
    )
    with pytest.raises(ValueError, match="api.openai.com"):
        policy.to_network_policy()


def test_the_delivery_marker_is_stable_per_command() -> None:
    first = idempotency_marker(command_id="cmd_abc")
    assert first == idempotency_marker(command_id="cmd_abc")
    assert first != idempotency_marker(command_id="cmd_def")
    assert first.startswith("<!--") and first.endswith("-->")


def test_credit_reconciliation_matches_on_the_operation_id_only() -> None:
    from app.connectors.stripe_credits import CreditAdjustment

    transactions = [
        CreditAdjustment(
            transaction_id="cbtxn_1",
            customer_id="cus_1",
            amount_minor_units=-25000,
            currency="usd",
            ending_balance_minor_units=-25000,
            metadata={"utrace_operation_id": "op_a"},
            created_epoch_seconds=1,
        ),
        CreditAdjustment(
            transaction_id="cbtxn_2",
            customer_id="cus_1",
            amount_minor_units=-25000,
            currency="usd",
            ending_balance_minor_units=-50000,
            metadata={"utrace_operation_id": "op_b"},
            created_epoch_seconds=2,
        ),
    ]
    matched = reconcile_issued_credit(transactions=transactions, operation_id="op_b")
    assert matched is not None
    assert matched.transaction_id == "cbtxn_2"
    assert reconcile_issued_credit(transactions=transactions, operation_id="op_c") is None
