"""The Implementation Gate Job's type and builder, and the fact its terminal failure commits.

A leaf module, so the interview can commit the gate Job with the transition that
reaches `gate_pending`, and read what a failed gate settled, without importing the
gate's providers.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from app.envelope import TRANSIENT_CATEGORIES, JobIntent
from app.ids import uuid7

__all__ = [
    "DECIDE_GATE",
    "GATE_FAILED_EVENT",
    "GATE_NO_IMPLEMENTATION_EVENT",
    "GateFailureOutcome",
    "decide_gate_job",
]

DECIDE_GATE: Final[str] = "gate.decide"
"""Investigate, propose, decide, and act on the Implementation Gate's decision."""

GATE_FAILED_EVENT: Final[str] = "implementation_gate_failed"
"""The Product Signal fact a gate Job with no legal run left commits."""

GATE_NO_IMPLEMENTATION_EVENT: Final[str] = "gate_chose_no_implementation"
"""The Product Signal fact a gate that chose no implementation for the agreed change commits."""


class GateFailureOutcome(StrEnum):
    """What a terminally failed gate leaves the case with; the change starts in neither."""

    CASE_BLOCKED = "case_blocked"
    """No version exists yet, so the case closes with its blocker Case File."""
    CHANGE_DECLINED = "change_declined"
    """A ready version exists, so validation of it goes on without the asked-for change."""


def decide_gate_job(
    *, product_signal_id: UUID, cancellation_scope_id: UUID, now: datetime
) -> JobIntent:
    """Build the Job that runs the Implementation Gate for one Product Signal."""
    return JobIntent(
        job_id=uuid7(),
        job_type=DECIDE_GATE,
        input={"product_signal_id": str(product_signal_id)},
        deadline_at=now + timedelta(hours=2),
        timeout_seconds=1800,
        max_runs=2,
        resource_budget={"kind": DECIDE_GATE},
        retryable_categories=TRANSIENT_CATEGORIES,
        backoff_base_seconds=30,
        backoff_max_seconds=300,
        concurrency_key=f"gate:{product_signal_id}",
        cancellation_scope_id=cancellation_scope_id,
        not_before=now,
    )
