"""Planning one Intake Item transition.

The Intake Item machine is pure and lives in the control-plane kernel. This
module supplies the routing-specific facts, Event, and audit record, so every
commit path builds its transition the same way.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from utrace_contracts.domain.lifecycle import ActorKind, AggregateKind, RoutingOutcome

from app.db.kernel import ColumnValue, TransitionRequest
from app.envelope import (
    Actor,
    AuditIntent,
    CommandIntent,
    EventAppend,
    JobIntent,
    JsonValue,
    StreamRef,
)
from app.ids import uuid7
from app.intake.context import RoutingContext
from app.lifecycle.intake import INTAKE_ITEM_MACHINE, IntakeItemFacts
from app.lifecycle.machine import plan_transition

__all__ = ["ROUTER_ACTOR", "intake_transition"]

ROUTER_ACTOR = Actor(kind=ActorKind.UTRACE_AGENT_ROLE, ref="intake_router")


def _facts(*, mutation: Mapping[str, ColumnValue], trigger: str) -> IntakeItemFacts:
    """Build the guard facts from what this transition is actually committing."""
    outcome = mutation.get("routing_outcome")
    blocked = mutation.get("blocked_reason")
    return IntakeItemFacts(
        routing_outcome=RoutingOutcome(outcome) if isinstance(outcome, str) else None,
        blocked_reason=blocked if isinstance(blocked, str) else None,
        route_committed=trigger == "commit_route",
    )


def intake_transition(
    *,
    context: RoutingContext,
    trigger: str,
    current_state: str,
    expected_revision: int,
    mutation: Mapping[str, ColumnValue],
    event_type: str,
    payload: Mapping[str, JsonValue],
    reason_code: str | None,
    jobs: Sequence[JobIntent],
    commands: Sequence[CommandIntent],
) -> TransitionRequest:
    """Return the request the kernel commits for one Intake Item move."""
    item_id = context.scope.intake_item_id
    return plan_transition(
        INTAKE_ITEM_MACHINE,
        _facts(mutation=mutation, trigger=trigger),
        trigger=trigger,
        current_state=current_state,
        client_id=context.scope.client_id,
        aggregate_kind=AggregateKind.INTAKE_ITEM,
        aggregate_id=item_id,
        expected_revision=expected_revision,
        state_column="state",
        mutation=mutation,
        event=EventAppend(
            message_id=uuid7(),
            stream=StreamRef(kind=AggregateKind.INTAKE_ITEM, id=item_id),
            event_type=event_type,
            payload=payload,
            actor=ROUTER_ACTOR,
            causation_id=item_id,
            correlation_id=item_id,
            occurred_at=datetime.now(tz=UTC),
        ),
        jobs=jobs,
        commands=commands,
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=StreamRef(kind=AggregateKind.INTAKE_ITEM, id=item_id),
            action=trigger,
            outcome=event_type,
            reason_code=reason_code,
            actor=ROUTER_ACTOR,
            pinned_configuration_release_id=context.release.id,
            detail={
                "product_overview_version_id": str(context.overview.id),
                "assistance_reason": context.assistance_reason,
            },
        ),
        additional_locks=(),
    )
