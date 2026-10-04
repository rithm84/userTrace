"""Issuing and reconciling one earned invoice credit.

The Command and its outbox intent are persisted before Stripe is called, and a
retry reuses the same operation and idempotency key. Stripe can prune those keys
after about 24 hours, so an uncertain outcome is reconciled against the
customer's transaction history and the operation's metadata before any
redispatch: uTrace never creates a fresh adjustment merely because a callback was
late.

A failure preserves the earned obligation. Nothing here reverses an earned credit
or asks the participant to repeat completed work.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import (
    CommandDisposition,
    CommandState,
    EffectState,
    OfferIssuanceState,
    OfferLedgerEntryKind,
)

from app.connectors.failures import ConnectorFailure
from app.connectors.failures import EffectState as ConnectorEffectState
from app.connectors.stripe_credits import CreditAdjustment, reconcile_issued_credit
from app.db.pool import Database
from app.db.rows import as_int, as_optional_str, as_str, as_uuid
from app.errors import GuardRejectedError
from app.execution.commands import (
    TerminalResult,
    mark_dispatched,
    mark_dispatching,
    record_terminal_result,
    start_reconciliation,
)
from app.ids import uuid7
from app.logging import get_logger
from app.offers.ledger import record_entry

__all__ = [
    "CreditIssuancePort",
    "IssuanceOperation",
    "IssuanceOutcome",
    "dispatch_issuance",
    "load_operation",
    "reconcile_issuance",
    "verify_adjustment",
]

_log = get_logger(__name__)

_HISTORY_LIMIT = 100


@dataclass(frozen=True, slots=True)
class IssuanceOperation:
    """One issuance operation and the identity it will be verified against."""

    id: UUID
    client_id: UUID
    offer_entitlement_id: UUID
    command_id: UUID
    state: OfferIssuanceState
    currency: str
    amount_minor_units: int
    idempotency_key: str
    billing_account_ref: str
    billing_customer_ref: str
    provider_transaction_ref: str | None
    dispatch_count: int


@dataclass(frozen=True, slots=True)
class IssuanceOutcome:
    """What one issuance attempt established."""

    operation_id: UUID
    state: OfferIssuanceState
    provider_transaction_ref: str | None
    blocked_reason: str | None
    created_adjustment: bool


@runtime_checkable
class CreditIssuancePort(Protocol):
    """The exact Stripe operations issuance performs.

    ``StripeCredits`` satisfies this structurally; naming it here keeps the
    controller to the two calls it is authorized to make. Creating a charge,
    finalizing an invoice, issuing a refund, and moving funds are all outside it.
    """

    async def issue_credit(
        self,
        *,
        customer_id: str,
        amount_minor_units: int,
        currency: str,
        idempotency_key: str,
        metadata: Mapping[str, str],
    ) -> CreditAdjustment:
        """Create one negative customer balance transaction."""
        ...

    async def list_balance_transactions(
        self, *, customer_id: str, limit: int
    ) -> tuple[CreditAdjustment, ...]:
        """Read the customer's balance transactions, newest first."""
        ...


async def load_operation(
    conn: AsyncConnection[DictRow], *, operation_id: UUID
) -> IssuanceOperation:
    """Return one issuance operation."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, client_id, offer_entitlement_id, command_id, state, currency,
                   amount_minor_units, idempotency_key, billing_account_ref,
                   billing_customer_ref, provider_transaction_ref, dispatch_count
            from public.offer_issuance_operations where id = %s
            """,
            (operation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise GuardRejectedError(
            machine="offer_issuance",
            trigger="load",
            reason=f"issuance operation {operation_id} does not exist",
        )
    return IssuanceOperation(
        id=as_uuid(row, "id"),
        client_id=as_uuid(row, "client_id"),
        offer_entitlement_id=as_uuid(row, "offer_entitlement_id"),
        command_id=as_uuid(row, "command_id"),
        state=OfferIssuanceState(as_str(row, "state")),
        currency=as_str(row, "currency"),
        amount_minor_units=as_int(row, "amount_minor_units"),
        idempotency_key=as_str(row, "idempotency_key"),
        billing_account_ref=as_str(row, "billing_account_ref"),
        billing_customer_ref=as_str(row, "billing_customer_ref"),
        provider_transaction_ref=as_optional_str(row, "provider_transaction_ref"),
        dispatch_count=as_int(row, "dispatch_count"),
    )


def verify_adjustment(
    adjustment: CreditAdjustment, operation: IssuanceOperation
) -> str | None:
    """Return why a returned transaction is not this operation's credit, or ``None``.

    Customer, account scope, amount, sign, currency, and the operation reference
    in the metadata all have to agree before uTrace records a credit as issued.
    """
    if adjustment.customer_id != operation.billing_customer_ref:
        return (
            f"the adjustment belongs to customer {adjustment.customer_id}, not "
            f"{operation.billing_customer_ref}"
        )
    if adjustment.amount_minor_units != -operation.amount_minor_units:
        return (
            f"the adjustment is {adjustment.amount_minor_units} minor units, not the "
            f"promised credit of {-operation.amount_minor_units}"
        )
    if adjustment.currency.upper() != operation.currency.upper():
        return (
            f"the adjustment is in {adjustment.currency.upper()}, not "
            f"{operation.currency.upper()}"
        )
    if adjustment.metadata.get("utrace_operation_id") != str(operation.id):
        return "the adjustment carries another operation's reference"
    return None


def issuance_metadata(operation: IssuanceOperation) -> Mapping[str, str]:
    """Build the stable references the adjustment carries.

    Offer and operation identifiers only: no interview content and no personal
    evidence reaches the billing account.
    """
    return {
        "utrace_operation_id": str(operation.id),
        "utrace_entitlement_id": str(operation.offer_entitlement_id),
        "utrace_client_id": str(operation.client_id),
    }


async def _record_issued(
    database: Database,
    *,
    operation: IssuanceOperation,
    adjustment: CreditAdjustment,
    created: bool,
) -> IssuanceOutcome:
    """Persist the verified transaction identity and close the Command."""
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update public.offer_issuance_operations
                set state = %s, provider_transaction_ref = %s, verified_at = now(),
                    ending_balance_minor_units = %s, blocked_reason = null,
                    revision = revision + 1, updated_at = now()
                where id = %s
                """,
                (
                    OfferIssuanceState.ISSUED.value,
                    adjustment.transaction_id,
                    adjustment.ending_balance_minor_units,
                    operation.id,
                ),
            )
        await record_entry(
            conn,
            client_id=operation.client_id,
            currency=operation.currency,
            entry_kind=OfferLedgerEntryKind.CREDIT_ISSUED,
            delta_minor_units=0,
            outreach_offer_id=None,
            offer_entitlement_id=operation.offer_entitlement_id,
            offer_issuance_operation_id=operation.id,
            causal_evidence={
                "provider_transaction_ref": adjustment.transaction_id,
                "ending_balance_minor_units": adjustment.ending_balance_minor_units,
                "created_adjustment": created,
            },
        )
    await record_terminal_result(
        database,
        message_id=uuid7(),
        client_id=operation.client_id,
        source="stripe:customer_balance_transaction",
        result=TerminalResult(
            result_id=uuid7(),
            command_id=operation.command_id,
            disposition=CommandDisposition.SUCCEEDED,
            effect_state=EffectState.COMPLETED,
            technical_category=None,
            retry_advice=None,
            payload={"transaction_id": adjustment.transaction_id},
        ),
    )
    _log.info(
        "offer_credit_issued",
        client_id=str(operation.client_id),
        offer_issuance_operation_id=str(operation.id),
        provider_transaction_ref=adjustment.transaction_id,
        amount_minor_units=adjustment.amount_minor_units,
        currency=adjustment.currency,
        created_adjustment=created,
    )
    return IssuanceOutcome(
        operation_id=operation.id,
        state=OfferIssuanceState.ISSUED,
        provider_transaction_ref=adjustment.transaction_id,
        blocked_reason=None,
        created_adjustment=created,
    )


async def _block(
    database: Database, operation: IssuanceOperation, *, reason: str
) -> IssuanceOutcome:
    """Block issuance for reconciliation while preserving the earned obligation."""
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update public.offer_issuance_operations
                set state = %s, blocked_reason = %s, revision = revision + 1,
                    updated_at = now()
                where id = %s
                """,
                (OfferIssuanceState.BLOCKED.value, reason, operation.id),
            )
        await record_entry(
            conn,
            client_id=operation.client_id,
            currency=operation.currency,
            entry_kind=OfferLedgerEntryKind.ISSUANCE_BLOCKED,
            delta_minor_units=0,
            outreach_offer_id=None,
            offer_entitlement_id=operation.offer_entitlement_id,
            offer_issuance_operation_id=operation.id,
            causal_evidence={"reason": reason},
        )
    _log.info(
        "offer_issuance_blocked",
        client_id=str(operation.client_id),
        offer_issuance_operation_id=str(operation.id),
        reason=reason,
    )
    return IssuanceOutcome(
        operation_id=operation.id,
        state=OfferIssuanceState.BLOCKED,
        provider_transaction_ref=None,
        blocked_reason=reason,
        created_adjustment=False,
    )


async def dispatch_issuance(
    database: Database, credits: CreditIssuancePort, *, operation_id: UUID
) -> IssuanceOutcome:
    """Issue one earned credit, verifying the transaction Stripe returned."""
    async with database.connection() as conn:
        operation = await load_operation(conn, operation_id=operation_id)
    if operation.state is OfferIssuanceState.ISSUED:
        return IssuanceOutcome(
            operation_id=operation.id,
            state=operation.state,
            provider_transaction_ref=operation.provider_transaction_ref,
            blocked_reason=None,
            created_adjustment=False,
        )
    if operation.state is OfferIssuanceState.BLOCKED:
        return await reconcile_issuance(
            database, credits, operation_id=operation_id
        )

    await _ensure_dispatching(database, command_id=operation.command_id)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.offer_issuance_operations
            set state = %s, dispatch_count = dispatch_count + 1,
                revision = revision + 1, updated_at = now()
            where id = %s
            """,
            (OfferIssuanceState.DISPATCHED.value, operation.id),
        )

    try:
        adjustment = await credits.issue_credit(
            customer_id=operation.billing_customer_ref,
            amount_minor_units=operation.amount_minor_units,
            currency=operation.currency.lower(),
            idempotency_key=operation.idempotency_key,
            metadata=issuance_metadata(operation),
        )
    except ConnectorFailure as failure:
        if failure.detail.effect_state is ConnectorEffectState.ABSENT:
            raise
        await start_reconciliation(database, command_id=operation.command_id)
        return await _reconcile(
            database,
            credits,
            operation,
            uncertain_reason=failure.detail.message,
        )

    refusal = verify_adjustment(adjustment, operation)
    if refusal is not None:
        return await _block(
            database,
            operation,
            reason=f"the returned transaction failed verification: {refusal}",
        )
    await mark_dispatched(
        database, command_id=operation.command_id, provider_ref=adjustment.transaction_id
    )
    return await _record_issued(
        database, operation=operation, adjustment=adjustment, created=True
    )


async def reconcile_issuance(
    database: Database, credits: CreditIssuancePort, *, operation_id: UUID
) -> IssuanceOutcome:
    """Resolve an uncertain issuance against the customer's transaction history."""
    async with database.connection() as conn:
        operation = await load_operation(conn, operation_id=operation_id)
    if operation.state is OfferIssuanceState.ISSUED:
        return IssuanceOutcome(
            operation_id=operation.id,
            state=operation.state,
            provider_transaction_ref=operation.provider_transaction_ref,
            blocked_reason=None,
            created_adjustment=False,
        )
    return await _reconcile(
        database, credits, operation, uncertain_reason="explicit reconciliation"
    )


async def _reconcile(
    database: Database,
    credits: CreditIssuancePort,
    operation: IssuanceOperation,
    *,
    uncertain_reason: str,
) -> IssuanceOutcome:
    """Match the operation against the customer's existing adjustments."""
    transactions = await credits.list_balance_transactions(
        customer_id=operation.billing_customer_ref, limit=_HISTORY_LIMIT
    )
    existing = reconcile_issued_credit(
        transactions=transactions, operation_id=str(operation.id)
    )
    if existing is None:
        return await _block(
            database,
            operation,
            reason=(
                "the issuance outcome could not be established from the customer's "
                f"transaction history ({uncertain_reason}); no second adjustment was "
                "created"
            ),
        )
    refusal = verify_adjustment(existing, operation)
    if refusal is not None:
        return await _block(
            database,
            operation,
            reason=f"a matching transaction failed verification: {refusal}",
        )
    return await _record_issued(
        database, operation=operation, adjustment=existing, created=False
    )


async def _ensure_dispatching(database: Database, *, command_id: UUID) -> None:
    """Claim the Command for dispatch, tolerating a re-entered attempt.

    A retry after a transport failure finds its own Command already claimed. That
    is the same attempt continuing, not a second effect, so the claim is a no-op
    rather than an illegal transition.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state from public.commands where id = %s", (command_id,)
        )
        row = await cur.fetchone()
    if row is None:
        raise GuardRejectedError(
            machine="offer_issuance",
            trigger="dispatch",
            reason=f"issuance Command {command_id} does not exist",
        )
    if as_str(row, "state") in {
        CommandState.CREATED.value,
        CommandState.QUEUED.value,
    }:
        await mark_dispatching(database, command_id=command_id)
