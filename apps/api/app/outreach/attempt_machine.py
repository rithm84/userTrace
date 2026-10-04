"""Planning one Outreach Attempt transition.

Retries stay inside one logical attempt: a provider resend does not create a
second attempt, and a claim, decline, expiry, unreachable, or revocation is the
attempt's terminal state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import ActorKind, AggregateKind

from app.db.kernel import ColumnValue, TransitionRequest
from app.db.rows import as_int, as_str
from app.envelope import (
    Actor,
    AuditIntent,
    CommandIntent,
    EventAppend,
    JobIntent,
    JsonValue,
    StreamRef,
)
from app.errors import AggregateNotFoundError
from app.ids import uuid7
from app.lifecycle.machine import plan_transition
from app.lifecycle.outreach import OUTREACH_ATTEMPT_MACHINE, OutreachAttemptFacts

__all__ = ["OUTREACH_ACTOR", "attempt_transition", "load_attempt_state"]

OUTREACH_ACTOR = Actor(kind=ActorKind.UTRACE_AGENT_ROLE, ref="outreach_controller")


async def load_attempt_state(
    conn: AsyncConnection[DictRow], *, outreach_attempt_id: UUID
) -> tuple[str, int]:
    """Return one Outreach Attempt's state and revision under a row lock."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select state, revision from public.outreach_attempts where id = %s "
            "for update",
            (outreach_attempt_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="outreach_attempts", row_id=outreach_attempt_id
        )
    return as_str(row, "state"), as_int(row, "revision")


def attempt_transition(
    *,
    client_id: UUID,
    product_signal_id: UUID,
    outreach_attempt_id: UUID,
    trigger: str,
    current_state: str,
    expected_revision: int,
    facts: OutreachAttemptFacts,
    mutation: Mapping[str, ColumnValue],
    event_type: str,
    payload: Mapping[str, JsonValue],
    reason_code: str | None,
    pinned_configuration_release_id: UUID | None,
    jobs: Sequence[JobIntent],
    commands: Sequence[CommandIntent],
) -> TransitionRequest:
    """Return the request the kernel commits for one Outreach Attempt move."""
    stream = StreamRef(kind=AggregateKind.OUTREACH_ATTEMPT, id=outreach_attempt_id)
    return plan_transition(
        OUTREACH_ATTEMPT_MACHINE,
        facts,
        trigger=trigger,
        current_state=current_state,
        client_id=client_id,
        aggregate_kind=AggregateKind.OUTREACH_ATTEMPT,
        aggregate_id=outreach_attempt_id,
        expected_revision=expected_revision,
        state_column="state",
        mutation=mutation,
        event=EventAppend(
            message_id=uuid7(),
            stream=stream,
            event_type=event_type,
            payload=payload,
            actor=OUTREACH_ACTOR,
            causation_id=product_signal_id,
            correlation_id=product_signal_id,
            occurred_at=datetime.now(tz=UTC),
        ),
        jobs=jobs,
        commands=commands,
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=stream,
            action=trigger,
            outcome=event_type,
            reason_code=reason_code,
            actor=OUTREACH_ACTOR,
            pinned_configuration_release_id=pinned_configuration_release_id,
            detail={"product_signal_id": str(product_signal_id)},
        ),
        additional_locks=(),
    )
