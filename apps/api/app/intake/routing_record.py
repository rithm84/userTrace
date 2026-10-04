"""Moving the Intake Item and recording what routing decided.

One Intake Item walks `received -> routing -> routed`, or into
`routing_blocked` with its unresolved facts preserved. Every evaluation appends
an immutable routing decision, including the ones that blocked and the ones that
followed conditional assistance.

Contract: docs/arch-components/signal-and-outreach.md#routing-and-matching-contract
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import IntakeItemState, RoutingOutcome

from app.db.kernel import ColumnValue, apply_transition_on_connection
from app.db.rows import as_int, as_str
from app.envelope import JobIntent, JsonValue
from app.ids import uuid7
from app.intake.context import RoutingContext
from app.intake.intake_machine import intake_transition

__all__ = [
    "EXCERPT_LIMIT",
    "CommittedRoute",
    "excerpt",
    "route_intake_item_state",
    "write_routing_decision",
]

EXCERPT_LIMIT: Final[int] = 2000


@dataclass(frozen=True, slots=True)
class CommittedRoute:
    """What one committed routing outcome produced."""

    intake_item_id: UUID
    outcome: RoutingOutcome | None
    product_signal_id: UUID | None
    source_report_id: UUID | None
    blocked_reason: str | None


def excerpt(context: RoutingContext) -> str:
    """Return the originating user's own wording, bounded for storage."""
    return context.wording()[:EXCERPT_LIMIT]


async def write_routing_decision(
    conn: AsyncConnection[DictRow],
    *,
    context: RoutingContext,
    attempt_index: int,
    outcome: RoutingOutcome | None,
    blocked_reason: str | None,
    product_signal_id: UUID | None,
    source_report_id: UUID | None,
    decision_model_id: str,
    questions: Mapping[str, JsonValue],
    answers: Mapping[str, JsonValue],
    acceptance: Mapping[str, JsonValue],
) -> None:
    """Append the immutable record of one routing evaluation."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.routing_decisions (
                id, client_id, intake_item_id, attempt_index, outcome, blocked_reason,
                product_signal_id, source_report_id, pinned_configuration_release_id,
                product_overview_version_id, decision_model_id, questions, answers,
                acceptance, assistance_reason, assisted_interpretation
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                uuid7(),
                context.scope.client_id,
                context.scope.intake_item_id,
                attempt_index,
                outcome.value if outcome is not None else None,
                blocked_reason,
                product_signal_id,
                source_report_id,
                context.release.id,
                context.overview.id,
                decision_model_id,
                Jsonb(dict(questions)),
                Jsonb(dict(answers)),
                Jsonb(dict(acceptance)),
                context.assistance_reason,
                context.interpretation,
            ),
        )


async def _load_intake_state(
    conn: AsyncConnection[DictRow], *, intake_item_id: UUID
) -> tuple[str, int]:
    """Return the Intake Item's current state and revision under a row lock."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select state, revision from public.intake_items where id = %s for update",
            (intake_item_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise ValueError(f"intake item {intake_item_id} does not exist")
    return as_str(row, "state"), as_int(row, "revision")


async def route_intake_item_state(
    conn: AsyncConnection[DictRow],
    *,
    context: RoutingContext,
    trigger: str,
    mutation: Mapping[str, ColumnValue],
    event_type: str,
    payload: Mapping[str, JsonValue],
    reason_code: str | None,
    jobs: Sequence[JobIntent],
) -> None:
    """Move the Intake Item through `routing` into its terminal routing state."""
    state, revision = await _load_intake_state(
        conn, intake_item_id=context.scope.intake_item_id
    )
    if state in {IntakeItemState.RECEIVED.value, IntakeItemState.ROUTING_BLOCKED.value}:
        outcome = await apply_transition_on_connection(
            conn,
            intake_transition(
                context=context,
                trigger="begin_routing",
                current_state=state,
                expected_revision=revision,
                mutation={},
                event_type="intake_routing_started",
                payload={},
                reason_code=None,
                jobs=(),
                commands=(),
            ),
        )
        revision = outcome.revision
        state = outcome.to_state

    await apply_transition_on_connection(
        conn,
        intake_transition(
            context=context,
            trigger=trigger,
            current_state=state,
            expected_revision=revision,
            mutation=mutation,
            event_type=event_type,
            payload=payload,
            reason_code=reason_code,
            jobs=jobs,
            commands=(),
        ),
    )


