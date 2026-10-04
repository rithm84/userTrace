"""Implementation Attempt state transitions and checkpoints.

Every move an attempt makes goes through the lifecycle machine and the
transactional kernel: the machine's guards read durable facts, and the kernel
locks the row, checks the expected revision and the legal predecessor, writes the
projection, appends the sequenced Event, and records the audit fact in one
transaction.

A checkpoint is what a paused, departed, or infrastructure-blocked attempt
resumes from. It carries consumed budgets and the cycle the run reached, never a
credential and never a workspace token.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import structlog
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import ActorKind, AggregateKind, ImplementationAttemptState

from app.db.kernel import ColumnValue, apply_transition_on_connection
from app.db.pool import Database
from app.db.rows import as_int, as_str
from app.envelope import Actor, AuditIntent, EventAppend, JsonValue, StreamRef
from app.errors import AggregateNotFoundError
from app.ids import uuid7
from app.implementation.budgets import ConsumedBudgets
from app.lifecycle.implementation import (
    IMPLEMENTATION_ATTEMPT_MACHINE,
    ImplementationAttemptFacts,
)
from app.lifecycle.machine import plan_transition

__all__ = [
    "AttemptCheckpoint",
    "advance_attempt",
    "current_attempt",
    "write_checkpoint",
]

_logger = structlog.get_logger(__name__)

_ATTEMPT_ACTOR = Actor(kind=ActorKind.UTRACE_AGENT_ROLE, ref="implementation_controller")


@dataclass(frozen=True, slots=True)
class AttemptCheckpoint:
    """The durable point a resumable attempt continues from."""

    cycle_index: int
    consumed: ConsumedBudgets
    workspace_ref: str | None
    last_scope_digest: str | None

    def as_column(self) -> dict[str, JsonValue]:
        """Render the checkpoint for the attempt's `checkpoint` column."""
        return {
            "cycle_index": self.cycle_index,
            "consumed_budgets": self.consumed.as_column(),
            "workspace_ref": self.workspace_ref,
            "last_scope_digest": self.last_scope_digest,
        }


@dataclass(frozen=True, slots=True)
class CurrentAttempt:
    """The attempt row's lifecycle position, read before a step decides anything."""

    state: ImplementationAttemptState
    revision: int
    consumed: ConsumedBudgets


async def current_attempt(database: Database, *, attempt_id: UUID) -> CurrentAttempt:
    """Read the attempt's durable state, revision, and consumption."""
    async with database.connection() as conn:
        return await _read(conn, attempt_id)


async def _read(conn: AsyncConnection[DictRow], attempt_id: UUID) -> CurrentAttempt:
    async with conn.cursor() as cur:
        await cur.execute(
            "select state, revision, consumed_budgets from public.implementation_attempts "
            "where id = %s",
            (attempt_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="implementation_attempts", row_id=attempt_id)
    stored = row["consumed_budgets"]
    if not isinstance(stored, Mapping):
        raise TypeError("implementation_attempts.consumed_budgets is not a JSON object")
    return CurrentAttempt(
        state=ImplementationAttemptState(as_str(row, "state")),
        revision=as_int(row, "revision"),
        consumed=ConsumedBudgets.from_column(stored),
    )


async def advance_attempt(
    database: Database,
    *,
    client_id: UUID,
    attempt_id: UUID,
    trigger: str,
    facts: ImplementationAttemptFacts,
    mutation: Mapping[str, ColumnValue],
    payload: Mapping[str, JsonValue],
    reason_code: str | None,
) -> ImplementationAttemptState:
    """Move the attempt through one legal transition and commit it once."""
    async with database.transaction() as conn:
        current = await _read(conn, attempt_id)
        stream = StreamRef(kind=AggregateKind.IMPLEMENTATION_ATTEMPT, id=attempt_id)
        request = plan_transition(
            IMPLEMENTATION_ATTEMPT_MACHINE,
            facts,
            trigger=trigger,
            current_state=current.state.value,
            client_id=client_id,
            aggregate_kind=AggregateKind.IMPLEMENTATION_ATTEMPT,
            aggregate_id=attempt_id,
            expected_revision=current.revision,
            state_column="state",
            mutation=mutation,
            event=EventAppend(
                message_id=uuid7(),
                stream=stream,
                event_type=f"implementation_attempt_{trigger}",
                payload=payload,
                actor=_ATTEMPT_ACTOR,
                causation_id=None,
                correlation_id=attempt_id,
                occurred_at=datetime.now(tz=UTC),
            ),
            jobs=(),
            commands=(),
            audit=AuditIntent(
                audit_id=uuid7(),
                subject=stream,
                action=f"implementation_attempt_{trigger}",
                outcome=trigger,
                reason_code=reason_code,
                actor=_ATTEMPT_ACTOR,
                pinned_configuration_release_id=None,
                detail=dict(payload),
            ),
            additional_locks=(),
        )
        outcome = await apply_transition_on_connection(conn, request)
    _logger.info(
        "implementation.attempt.advanced",
        attempt_id=str(attempt_id),
        trigger=trigger,
        from_state=outcome.from_state,
        to_state=outcome.to_state,
        revision=outcome.revision,
    )
    return ImplementationAttemptState(outcome.to_state)


async def write_checkpoint(
    database: Database, *, attempt_id: UUID, checkpoint: AttemptCheckpoint
) -> None:
    """Persist the resumable checkpoint and the consumption it preserves."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.implementation_attempts
            set checkpoint = %s, consumed_budgets = %s, workspace_ref = %s,
                revision = revision + 1, updated_at = now()
            where id = %s
            """,
            (
                Jsonb(checkpoint.as_column()),
                Jsonb(checkpoint.consumed.as_column()),
                checkpoint.workspace_ref,
                attempt_id,
            ),
        )
    _logger.info(
        "implementation.attempt.checkpointed",
        attempt_id=str(attempt_id),
        cycle_index=checkpoint.cycle_index,
        workspace_ref=checkpoint.workspace_ref,
    )
