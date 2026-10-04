"""The Jobs that get a case's implementation path ready while its interview runs.

A leaf module, so the invitation claim can commit them beside the preview's own
preparation without importing the implementation's providers.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from app.envelope import TRANSIENT_CATEGORIES, JobIntent
from app.ids import uuid7

__all__ = [
    "INVESTIGATE_CODE",
    "PREPARE_WORKSPACE",
    "RELEASE_PREPARED_WORKSPACE",
    "investigate_code_job",
    "prepare_workspace_job",
]

INVESTIGATE_CODE: Final[str] = "implementation.investigate"
"""Investigate the authorized checkout read-only for the concern intake recorded."""

PREPARE_WORKSPACE: Final[str] = "implementation.prepare_workspace"
"""Prepare the workspace the case's Implementation Attempt would claim."""

RELEASE_PREPARED_WORKSPACE: Final[str] = "implementation.release_prepared_workspace"
"""Destroy a prepared workspace no attempt claimed. Queued by a scheduled scan."""


def investigate_code_job(
    *, product_signal_id: UUID, cancellation_scope_id: UUID, now: datetime
) -> JobIntent:
    """Build the Job that investigates the code while the interview runs."""
    return _job(
        job_type=INVESTIGATE_CODE,
        product_signal_id=product_signal_id,
        cancellation_scope_id=cancellation_scope_id,
        now=now,
        concurrency_key=f"investigation:{product_signal_id}",
    )


def prepare_workspace_job(
    *, product_signal_id: UUID, cancellation_scope_id: UUID, now: datetime
) -> JobIntent:
    """Build the Job that prepares the case's implementation workspace."""
    return _job(
        job_type=PREPARE_WORKSPACE,
        product_signal_id=product_signal_id,
        cancellation_scope_id=cancellation_scope_id,
        now=now,
        concurrency_key=f"prepared_workspace:{product_signal_id}",
    )


def _job(
    *,
    job_type: str,
    product_signal_id: UUID,
    cancellation_scope_id: UUID,
    now: datetime,
    concurrency_key: str,
) -> JobIntent:
    return JobIntent(
        job_id=uuid7(),
        job_type=job_type,
        input={"product_signal_id": str(product_signal_id)},
        deadline_at=now + timedelta(hours=1),
        timeout_seconds=1800,
        max_runs=2,
        resource_budget={"kind": job_type},
        retryable_categories=TRANSIENT_CATEGORIES,
        backoff_base_seconds=10,
        backoff_max_seconds=60,
        concurrency_key=concurrency_key,
        cancellation_scope_id=cancellation_scope_id,
        not_before=now,
    )
