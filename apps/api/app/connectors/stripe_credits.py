"""Outreach Offers invoice credits in the client's Stripe billing account.

Implements the adapter half of
[Issuance and reconciliation](../../../../docs/arch-components/outreach-offers.md#issuance-and-reconciliation):

- Earned credit is issued by creating a **negative** customer balance transaction in the
  agreed currency. The adjustment is added to the existing balance; the total is never
  overwritten, because it may already hold other credits or debits.
- Amounts are integer minor units throughout. Nothing here rounds, converts, or derives an
  amount from a model output.
- Retries reuse the same operation's idempotency key. Stripe may prune keys after about
  24 hours, so the key alone cannot guarantee permanent deduplication: an ambiguous
  outcome is reconciled against the customer's transaction history and the operation
  metadata before any redispatch. This connector therefore never retries issuance itself.
- Invoice application is read from invoices and their linked balance transactions, never
  inferred from the current balance alone.

The adapter creates no charges, finalizes no invoices, issues no refunds, and moves no
funds. Those capabilities are outside the uTrace authority even when the installed Stripe
permission set is broader.

Sandbox guard: the pilot and the Shopalytics demo run against a Stripe sandbox. This
connector refuses any key that is not a test or restricted-test key, so a live secret key
cannot reach it by configuration mistake.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Final

import stripe
import structlog
from pydantic import BaseModel, ConfigDict
from stripe._http_client import HTTPXClient
from stripe._request_options import RequestOptions
from stripe._stripe_object import StripeObject

from app.connectors.failures import (
    ConnectorFailure,
    EffectState,
    FailureDetail,
    RetryAdvice,
    TechnicalCategory,
    default_retry_advice,
    redacted_parameters,
    technical_category_for_status,
)

__all__ = [
    "SANDBOX_KEY_PREFIXES",
    "AppliedInvoiceCredit",
    "CreditAdjustment",
    "StripeCredits",
    "stripe_credits_from_environment",
]

_logger = structlog.get_logger(__name__)
_PROVIDER: Final[str] = "stripe"

SANDBOX_KEY_PREFIXES: Final[tuple[str, ...]] = ("sk_test_", "rk_test_")
"""Only sandbox and test-mode keys are accepted; a live key is refused at construction."""


class CreditAdjustment(BaseModel):
    """One issued customer balance transaction.

    `amount_minor_units` is negative for an issued credit, matching Stripe's sign
    convention where a negative balance is credit owed to the customer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    transaction_id: str
    customer_id: str
    amount_minor_units: int
    currency: str
    ending_balance_minor_units: int
    """The customer's balance after this adjustment, as Stripe reported it."""
    metadata: Mapping[str, str]
    created_epoch_seconds: int


class AppliedInvoiceCredit(BaseModel):
    """How much of a customer's credit balance one invoice consumed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    invoice_id: str
    customer_id: str
    currency: str
    total_minor_units: int
    applied_balance_minor_units: int
    """Credit Stripe applied to this invoice, as a positive number of minor units."""
    amount_due_minor_units: int
    status: str


class StripeCredits:
    """Connector for issuing and reconciling Outreach Offers invoice credits."""

    def __init__(self, *, secret_key: str, api_timeout_seconds: float) -> None:
        if not secret_key.startswith(SANDBOX_KEY_PREFIXES):
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="configure",
                    category=TechnicalCategory.CONFIGURATION,
                    retry_advice=RetryAdvice.AFTER_CONDITION_CHANGE,
                    effect_state=EffectState.ABSENT,
                    message=(
                        "refusing a Stripe key without a sandbox or test prefix; expected "
                        f"one of {', '.join(SANDBOX_KEY_PREFIXES)}"
                    ),
                    request_parameters=redacted_parameters({"secret_key": secret_key}),
                )
            )
        # `max_network_retries=0`: retry policy belongs to the caller, which knows whether
        # the operation left an effect. The async HTTP client carries the request timeout,
        # because Stripe's per-request options do not expose one.
        self._client = stripe.StripeClient(
            secret_key,
            max_network_retries=0,
            http_client=HTTPXClient(timeout=api_timeout_seconds, allow_sync_methods=False),
        )

    async def issue_credit(
        self,
        *,
        customer_id: str,
        amount_minor_units: int,
        currency: str,
        idempotency_key: str,
        metadata: Mapping[str, str],
    ) -> CreditAdjustment:
        """Create one negative customer balance transaction for an earned reward.

        `amount_minor_units` is the positive face value of the reward; this method applies
        the negative sign, so a caller cannot accidentally debit a customer.

        Never retried here. Stripe's key expiry makes a blind repeat unsafe outside the
        deduplication window, so an ambiguous outcome is reconciled by the caller through
        `list_balance_transactions` and the operation metadata.
        """
        if amount_minor_units <= 0:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="issue_credit",
                    category=TechnicalCategory.PROVIDER_REJECTED,
                    retry_advice=RetryAdvice.NEVER,
                    effect_state=EffectState.ABSENT,
                    message="reward face value must be a positive number of minor units",
                    request_parameters=redacted_parameters(
                        {"customer_id": customer_id, "amount": amount_minor_units}
                    ),
                )
            )
        try:
            transaction = await self._client.v1.customers.balance_transactions.create_async(
                customer_id,
                {
                    "amount": -amount_minor_units,
                    "currency": currency,
                    "description": "uTrace Outreach Offer invoice credit",
                    "metadata": dict(metadata),
                },
                RequestOptions(idempotency_key=idempotency_key),
            )
        except stripe.StripeError as error:
            raise self._map_error(
                operation="issue_credit",
                error=error,
                effect=EffectState.INDETERMINATE,
                parameters={
                    "customer_id": customer_id,
                    "amount": -amount_minor_units,
                    "currency": currency,
                },
            ) from error
        adjustment = CreditAdjustment(
            transaction_id=transaction.id,
            customer_id=customer_id,
            amount_minor_units=transaction.amount,
            currency=transaction.currency,
            ending_balance_minor_units=transaction.ending_balance,
            metadata=_string_metadata(transaction.metadata),
            created_epoch_seconds=transaction.created,
        )
        _logger.info(
            "stripe.credit.issued",
            customer_id=customer_id,
            transaction_id=adjustment.transaction_id,
            amount_minor_units=adjustment.amount_minor_units,
            currency=adjustment.currency,
            ending_balance_minor_units=adjustment.ending_balance_minor_units,
        )
        return adjustment

    async def list_balance_transactions(
        self, *, customer_id: str, limit: int
    ) -> tuple[CreditAdjustment, ...]:
        """Read a customer's balance transactions, newest first.

        This is the reconciliation read: an uncertain issuance is resolved by finding the
        operation's metadata here rather than by creating a second adjustment.
        """
        try:
            page = await self._client.v1.customers.balance_transactions.list_async(
                customer_id, {"limit": limit}
            )
        except stripe.StripeError as error:
            raise self._map_error(
                operation="list_balance_transactions",
                error=error,
                effect=EffectState.ABSENT,
                parameters={"customer_id": customer_id, "limit": limit},
            ) from error
        return tuple(
            CreditAdjustment(
                transaction_id=item.id,
                customer_id=customer_id,
                amount_minor_units=item.amount,
                currency=item.currency,
                ending_balance_minor_units=item.ending_balance,
                metadata=_string_metadata(item.metadata),
                created_epoch_seconds=item.created,
            )
            for item in page.data
        )

    async def read_customer_ids(self, *, limit: int) -> tuple[str, ...]:
        """List customer ids in the billing account.

        Used by readiness checks and the live smoke test to prove the sandbox grant works
        without writing anything.
        """
        try:
            page = await self._client.v1.customers.list_async({"limit": limit})
        except stripe.StripeError as error:
            raise self._map_error(
                operation="read_customer_ids",
                error=error,
                effect=EffectState.ABSENT,
                parameters={"limit": limit},
            ) from error
        return tuple(customer.id for customer in page.data)

    async def read_invoice_applications(
        self, *, customer_id: str, limit: int
    ) -> tuple[AppliedInvoiceCredit, ...]:
        """Read the customer's invoices and how much credit each one applied.

        Application is reported only from the invoice's own fields. A verified adjustment
        means credit is issued, not consumed, and the current balance alone never tells you
        which offer an invoice consumed.
        """
        try:
            page = await self._client.v1.invoices.list_async(
                {"customer": customer_id, "limit": limit}
            )
        except stripe.StripeError as error:
            raise self._map_error(
                operation="read_invoice_applications",
                error=error,
                effect=EffectState.ABSENT,
                parameters={"customer_id": customer_id, "limit": limit},
            ) from error
        applications: list[AppliedInvoiceCredit] = []
        for invoice in page.data:
            invoice_id = invoice.id
            if invoice_id is None:
                continue
            applications.append(
                AppliedInvoiceCredit(
                    invoice_id=invoice_id,
                    customer_id=customer_id,
                    currency=invoice.currency or "",
                    total_minor_units=invoice.total or 0,
                    applied_balance_minor_units=invoice.starting_balance
                    - (invoice.ending_balance or 0)
                    if invoice.starting_balance is not None
                    else 0,
                    amount_due_minor_units=invoice.amount_due or 0,
                    status=str(invoice.status),
                )
            )
        return tuple(applications)

    def _map_error(
        self,
        *,
        operation: str,
        error: stripe.StripeError,
        effect: EffectState,
        parameters: Mapping[str, object],
    ) -> ConnectorFailure:
        """Map a Stripe SDK error onto the typed failure.

        A connection error leaves the caller's declared effect state intact, because a
        write whose response was lost may well have been applied.
        """
        status = error.http_status
        if isinstance(error, stripe.APIConnectionError):
            category = TechnicalCategory.PROVIDER_UNAVAILABLE
        elif status is not None:
            category = technical_category_for_status(status)
        else:
            category = TechnicalCategory.INTERNAL
        advice = (
            default_retry_advice(category)
            if effect is EffectState.ABSENT
            else RetryAdvice.AFTER_CONDITION_CHANGE
        )
        return ConnectorFailure(
            FailureDetail(
                provider=_PROVIDER,
                operation=operation,
                category=category,
                retry_advice=advice,
                effect_state=effect,
                message=f"Stripe call failed: {type(error).__name__}",
                request_parameters=redacted_parameters(parameters),
                status_code=status,
                provider_error_code=error.code,
                response_body=str(error.user_message) if error.user_message else None,
            )
        )


def _string_metadata(metadata: object) -> dict[str, str]:
    """Render Stripe metadata as a plain string mapping.

    Stripe returns metadata as its own object type. Copying it into a plain dict keeps the
    provider object out of a uTrace record and makes the values explicitly strings.

    `StripeObject` is not a `Mapping` in the installed SDK and raises on `dict()`, so it is
    converted through the SDK's own `to_dict` first. Without that step every adjustment
    would carry empty metadata and `reconcile_issued_credit` could never match an already
    issued credit, which is the only thing standing between an uncertain response and a
    second, duplicate invoice credit.
    """
    if isinstance(metadata, StripeObject):
        metadata = metadata.to_dict()
    if not isinstance(metadata, Mapping):
        return {}
    return {str(key): str(value) for key, value in metadata.items()}


def reconcile_issued_credit(
    *, transactions: Sequence[CreditAdjustment], operation_id: str
) -> CreditAdjustment | None:
    """Find an already-issued adjustment for one issuance operation.

    Pure function so the reconciliation rule is testable without Stripe. Matching is on the
    `utrace_operation_id` metadata, which the issuing command sets, not on amount or time:
    two rewards of the same size to the same organization must not collapse into one.
    """
    for transaction in transactions:
        if transaction.metadata.get("utrace_operation_id") == operation_id:
            return transaction
    return None


def stripe_credits_from_environment(*, api_timeout_seconds: float) -> StripeCredits:
    """Build the connector from `STRIPE_SANDBOX_SECRET_KEY`, naming it when it is unset.

    On a deployed compute the key is a Supabase Compute project secret.
    """
    secret_key = os.environ.get("STRIPE_SANDBOX_SECRET_KEY")
    if not secret_key:
        raise ConnectorFailure(
            FailureDetail(
                provider=_PROVIDER,
                operation="configure",
                category=TechnicalCategory.CONFIGURATION,
                retry_advice=RetryAdvice.AFTER_CONDITION_CHANGE,
                effect_state=EffectState.ABSENT,
                message="STRIPE_SANDBOX_SECRET_KEY is not set; credit issuance needs it",
            )
        )
    return StripeCredits(secret_key=secret_key, api_timeout_seconds=api_timeout_seconds)
