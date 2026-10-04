"""Committing one Product Signal transition from its current durable state.

Orchestration steps — setup readiness, evidence sufficiency, a candidate becoming
ready — each move the Product Signal once. This reads the signal's state and
revision under the transition's own lock and commits the transition with its event,
audit, and any Jobs it obliges, so a stale caller loses to the revision check
rather than overwriting a newer state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import AggregateKind, ProductSignalState

from app.db.kernel import apply_transition_on_connection
from app.db.pool import Database
from app.db.rows import as_int, as_str
from app.envelope import Actor, AuditIntent, EventAppend, JobIntent, JsonValue, StreamRef
from app.errors import AggregateNotFoundError
from app.ids import uuid7
from app.lifecycle.machine import plan_transition
from app.lifecycle.product_signal import PRODUCT_SIGNAL_MACHINE, ProductSignalFacts

__all__ = ["advance_product_signal", "advance_product_signal_on_connection"]


async def advance_product_signal(
    database: Database,
    *,
    client_id: UUID,
    product_signal_id: UUID,
    trigger: str,
    facts: ProductSignalFacts,
    actor: Actor,
    payload: Mapping[str, JsonValue],
    jobs: Sequence[JobIntent],
    reason_code: str | None,
) -> ProductSignalState:
    """Move the Product Signal through one legal transition and return its new state."""
    async with database.transaction() as conn:
        return await advance_product_signal_on_connection(
            conn,
            client_id=client_id,
            product_signal_id=product_signal_id,
            trigger=trigger,
            facts=facts,
            actor=actor,
            payload=payload,
            jobs=jobs,
            reason_code=reason_code,
        )


async def advance_product_signal_on_connection(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    product_signal_id: UUID,
    trigger: str,
    facts: ProductSignalFacts,
    actor: Actor,
    payload: Mapping[str, JsonValue],
    jobs: Sequence[JobIntent],
    reason_code: str | None,
) -> ProductSignalState:
    """Commit one Product Signal transition inside the caller's transaction."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select state, revision from public.product_signals where id = %s",
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="product_signals", row_id=product_signal_id)
    stream = StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=product_signal_id)
    request = plan_transition(
        PRODUCT_SIGNAL_MACHINE,
        facts,
        trigger=trigger,
        current_state=as_str(row, "state"),
        client_id=client_id,
        aggregate_kind=AggregateKind.PRODUCT_SIGNAL,
        aggregate_id=product_signal_id,
        expected_revision=as_int(row, "revision"),
        state_column="state",
        mutation={},
        event=EventAppend(
            message_id=uuid7(),
            stream=stream,
            event_type=f"product_signal_{trigger}",
            payload=dict(payload),
            actor=actor,
            causation_id=None,
            correlation_id=product_signal_id,
            occurred_at=datetime.now(tz=UTC),
        ),
        jobs=tuple(jobs),
        commands=(),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=stream,
            action=f"product_signal_{trigger}",
            outcome=trigger,
            reason_code=reason_code,
            actor=actor,
            pinned_configuration_release_id=None,
            detail=dict(payload),
        ),
        additional_locks=(),
    )
    outcome = await apply_transition_on_connection(conn, request)
    return ProductSignalState(outcome.to_state)
