"""The two Jobs every terminal case outcome leaves behind.

A case that closes after participation owes a Case File and an Outreach Offer
entitlement evaluation, whichever controller closed it: validation completion,
return expiry, or an Implementation Attempt that ended without a candidate. Both
Jobs are committed with the transition that closes the case, and neither belongs
to a cancellation scope: a later client Stop withdraws work authority but must not
cancel the explanatory output it obliges.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from utrace_contracts.domain.lifecycle import CaseFileVariant

from app.envelope import TRANSIENT_CATEGORIES, JobIntent
from app.ids import uuid7
from app.offers.job_types import EVALUATE_ENTITLEMENT
from app.outputs.job_types import ASSEMBLE_CASE_FILE

__all__ = [
    "CASE_FILE_DEADLINE_SECONDS",
    "NARRATIVE_SOURCE",
    "case_file_assembly_job",
    "entitlement_job",
]

CASE_FILE_DEADLINE_SECONDS = 7 * 24 * 3600
"""How long a closing case's Case File assembly and entitlement Jobs may take."""

NARRATIVE_SOURCE = "model_drafted"
"""GPT-6.1 Sol drafts every Case File's prose; code then verifies every citation."""


def case_file_assembly_job(
    *,
    product_signal_id: UUID,
    validation_attempt_id: UUID | None,
    variant: CaseFileVariant,
    blocker_reason: str | None,
    closure_trigger: str,
    closure_reason: str,
    now: datetime,
    deadline_seconds: int,
) -> JobIntent:
    """Build the Job that assembles, seals, and publishes the Case File, then closes the case."""
    return JobIntent(
        job_id=uuid7(),
        job_type=ASSEMBLE_CASE_FILE,
        input={
            "product_signal_id": str(product_signal_id),
            "validation_attempt_id": (
                None if validation_attempt_id is None else str(validation_attempt_id)
            ),
            "variant": variant.value,
            "blocker_reason": blocker_reason,
            "closure_trigger": closure_trigger,
            "closure_reason": closure_reason,
            "narrative_source": NARRATIVE_SOURCE,
        },
        deadline_at=now + timedelta(seconds=deadline_seconds),
        timeout_seconds=600,
        max_runs=5,
        resource_budget={"kind": "case_file_assembly"},
        retryable_categories=TRANSIENT_CATEGORIES,
        backoff_base_seconds=30,
        backoff_max_seconds=900,
        concurrency_key=f"case_file:{product_signal_id}",
        cancellation_scope_id=None,
        not_before=now,
    )


def entitlement_job(
    *, product_signal_id: UUID, stop_reason: str, now: datetime, deadline_seconds: int
) -> JobIntent:
    """Build the Outreach Offer entitlement evaluation for a case's terminal milestone."""
    return JobIntent(
        job_id=uuid7(),
        job_type=EVALUATE_ENTITLEMENT,
        input={"product_signal_id": str(product_signal_id), "stop_reason": stop_reason},
        deadline_at=now + timedelta(seconds=deadline_seconds),
        timeout_seconds=300,
        max_runs=5,
        resource_budget={"kind": "offer_entitlement"},
        retryable_categories=TRANSIENT_CATEGORIES,
        backoff_base_seconds=30,
        backoff_max_seconds=900,
        concurrency_key=f"offer_entitlement:{product_signal_id}",
        cancellation_scope_id=None,
        not_before=now,
    )
