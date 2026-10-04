"""Job handlers for Outreach Offers.

Entitlement evaluation needs only durable records, so the control plane owns it.
Issuance and reconciliation call the client's billing account, so they are wired
by the process that installs the credit adapter.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from uuid import UUID

from app.connectors.stripe_credits import stripe_credits_from_environment
from app.db.pool import Database
from app.execution.jobs import ClaimedJob
from app.logging import get_logger
from app.offers.entitlement import CaseStopReason, evaluate_entitlement
from app.offers.issuance import dispatch_issuance, reconcile_issuance
from app.offers.job_types import EVALUATE_ENTITLEMENT, ISSUE_CREDIT, RECONCILE_ISSUANCE

__all__ = [
    "evaluate_entitlement_job",
    "issue_credit_job",
    "offer_handlers",
    "reconcile_issuance_job",
]

_log = get_logger(__name__)
_STRIPE_TIMEOUT_SECONDS = 20.0

type JobHandler = Callable[[Database, ClaimedJob], Awaitable[None]]


async def evaluate_entitlement_job(database: Database, claimed: ClaimedJob) -> None:
    """Convert or release one case's offer reservation from recorded participation."""
    outcome = await evaluate_entitlement(
        database,
        product_signal_id=UUID(str(claimed.input["product_signal_id"])),
        stop_reason=CaseStopReason(str(claimed.input["stop_reason"])),
    )
    _log.info(
        "offer_entitlement_evaluated",
        product_signal_id=str(claimed.input["product_signal_id"]),
        decision=None if outcome is None else outcome.decision.value,
    )


async def issue_credit_job(database: Database, claimed: ClaimedJob) -> None:
    """Issue one earned invoice credit in the client's Stripe sandbox."""
    await dispatch_issuance(
        database,
        stripe_credits_from_environment(api_timeout_seconds=_STRIPE_TIMEOUT_SECONDS),
        operation_id=UUID(str(claimed.input["offer_issuance_operation_id"])),
    )


async def reconcile_issuance_job(database: Database, claimed: ClaimedJob) -> None:
    """Resolve one issuance whose Stripe outcome is unknown."""
    await reconcile_issuance(
        database,
        stripe_credits_from_environment(api_timeout_seconds=_STRIPE_TIMEOUT_SECONDS),
        operation_id=UUID(str(claimed.input["offer_issuance_operation_id"])),
    )


def offer_handlers() -> Mapping[str, JobHandler]:
    """Return the Outreach Offer Job handlers for the worker's registry."""
    return {
        EVALUATE_ENTITLEMENT: evaluate_entitlement_job,
        ISSUE_CREDIT: issue_credit_job,
        RECONCILE_ISSUANCE: reconcile_issuance_job,
    }
