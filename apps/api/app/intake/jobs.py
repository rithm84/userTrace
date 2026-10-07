"""Job types and intent builders for the intake and outreach path.

Every durable responsibility this workstream owns is one of these types. Budgets
are frozen at commit time, so a retry runs under the same limits the decision was
made with.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from app.envelope import TRANSIENT_CATEGORIES, JobIntent, JsonValue
from app.ids import uuid7

__all__ = [
    "EXPIRE_INITIAL_INVITATION",
    "PREPARE_BASE_ALLOCATION",
    "ROUTE_INTAKE_ITEM",
    "SEAL_INTAKE_BATCH",
    "SEND_INITIAL_INVITATION",
    "SEND_STOP_NOTICE",
    "expire_initial_invitation_job",
    "prepare_base_allocation_job",
    "route_intake_item_job",
    "seal_intake_batch_job",
    "send_initial_invitation_job",
    "send_stop_notice_job",
]

SEAL_INTAKE_BATCH: Final[str] = "intake.seal_batch"
ROUTE_INTAKE_ITEM: Final[str] = "intake.route_intake_item"
SEND_INITIAL_INVITATION: Final[str] = "intake.send_initial_invitation"
EXPIRE_INITIAL_INVITATION: Final[str] = "intake.expire_initial_invitation"
SEND_STOP_NOTICE: Final[str] = "intake.send_stop_notice"
PREPARE_BASE_ALLOCATION: Final[str] = "preview_runtime.prepare_base_allocation"
"""Handed to the preview-runtime workstream; this workstream only enqueues it."""

_RETRYABLE: Final[tuple[str, ...]] = TRANSIENT_CATEGORIES


def _job(
    *,
    job_type: str,
    input: Mapping[str, JsonValue],
    concurrency_key: str | None,
    cancellation_scope_id: UUID | None,
    not_before: datetime,
    deadline_seconds: int,
    timeout_seconds: int,
    max_runs: int,
) -> JobIntent:
    """Build one Job intent with its frozen budget."""
    return JobIntent(
        job_id=uuid7(),
        job_type=job_type,
        input=input,
        deadline_at=datetime.now(tz=UTC) + timedelta(seconds=deadline_seconds),
        timeout_seconds=timeout_seconds,
        max_runs=max_runs,
        resource_budget={"kind": job_type},
        retryable_categories=_RETRYABLE,
        backoff_base_seconds=2,
        backoff_max_seconds=60,
        concurrency_key=concurrency_key,
        cancellation_scope_id=cancellation_scope_id,
        not_before=not_before,
    )


def seal_intake_batch_job(
    *, client_id: UUID, intake_batch_id: UUID, not_before: datetime
) -> JobIntent:
    """Seal one debounced batch into an ordered immutable Intake Item.

    The concurrency key is the batch itself: a second seal for the same batch
    waits rather than racing the first.
    """
    return _job(
        job_type=SEAL_INTAKE_BATCH,
        input={"client_id": str(client_id), "intake_batch_id": str(intake_batch_id)},
        concurrency_key=f"intake_batch:{intake_batch_id}",
        cancellation_scope_id=None,
        not_before=not_before,
        deadline_seconds=3600,
        timeout_seconds=120,
        max_runs=5,
    )


def route_intake_item_job(*, client_id: UUID, intake_item_id: UUID) -> JobIntent:
    """Route one sealed Intake Item.

    Routing is serialized per client, so concurrent batches for one client cannot
    both create a Product Signal for the same concern.
    """
    return _job(
        job_type=ROUTE_INTAKE_ITEM,
        input={"client_id": str(client_id), "intake_item_id": str(intake_item_id)},
        concurrency_key=f"intake_routing:{client_id}",
        cancellation_scope_id=None,
        not_before=datetime.now(tz=UTC),
        deadline_seconds=6 * 3600,
        timeout_seconds=300,
        max_runs=4,
    )


def send_initial_invitation_job(
    *, client_id: UUID, product_signal_id: UUID, cancellation_scope_id: UUID
) -> JobIntent:
    """Reserve the offer and deliver one private initial invitation.

    Outreach for one case is serialized on the Product Signal so a retry cannot
    run beside the original attempt.
    """
    return _job(
        job_type=SEND_INITIAL_INVITATION,
        input={
            "client_id": str(client_id),
            "product_signal_id": str(product_signal_id),
        },
        concurrency_key=f"outreach:{product_signal_id}",
        cancellation_scope_id=cancellation_scope_id,
        not_before=datetime.now(tz=UTC),
        deadline_seconds=6 * 3600,
        timeout_seconds=120,
        max_runs=3,
    )


def send_stop_notice_job(*, client_id: UUID, product_signal_id: UUID) -> JobIntent:
    """Tell the originating user, privately, that the client stopped their case.

    The Job belongs to no cancellation scope: the stop that schedules it cancels
    every Job in the case's scope.
    """
    return _job(
        job_type=SEND_STOP_NOTICE,
        input={
            "client_id": str(client_id),
            "product_signal_id": str(product_signal_id),
        },
        concurrency_key=f"outreach:{product_signal_id}",
        cancellation_scope_id=None,
        not_before=datetime.now(tz=UTC),
        deadline_seconds=6 * 3600,
        timeout_seconds=120,
        max_runs=3,
    )


def expire_initial_invitation_job(
    *,
    client_id: UUID,
    product_signal_id: UUID,
    outreach_attempt_id: UUID,
    expires_at: datetime,
    cancellation_scope_id: UUID,
) -> JobIntent:
    """Commit the terminal expiry of one unanswered initial invitation.

    The Job is scheduled at the invitation's own pinned deadline, so the
    response window is durable rather than a timer in a running process.
    """
    return _job(
        job_type=EXPIRE_INITIAL_INVITATION,
        input={
            "client_id": str(client_id),
            "product_signal_id": str(product_signal_id),
            "outreach_attempt_id": str(outreach_attempt_id),
        },
        concurrency_key=f"outreach:{product_signal_id}",
        cancellation_scope_id=cancellation_scope_id,
        not_before=expires_at,
        deadline_seconds=int(
            (expires_at - datetime.now(tz=UTC)).total_seconds() + 24 * 3600
        ),
        timeout_seconds=120,
        max_runs=3,
    )


def prepare_base_allocation_job(
    *,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
    cancellation_scope_id: UUID,
) -> JobIntent:
    """Ask the preview-runtime workstream to prepare the claimed base allocation.

    This workstream commits the intent; it does not provision, seed, or bind the
    runtime.
    """
    return _job(
        job_type=PREPARE_BASE_ALLOCATION,
        input={
            "client_id": str(client_id),
            "product_signal_id": str(product_signal_id),
            "interview_session_id": str(interview_session_id),
        },
        concurrency_key=f"preview_allocation:{product_signal_id}",
        cancellation_scope_id=cancellation_scope_id,
        not_before=datetime.now(tz=UTC),
        deadline_seconds=2 * 3600,
        timeout_seconds=600,
        max_runs=3,
    )
