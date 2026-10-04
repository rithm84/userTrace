"""The atomic commit of one routing outcome.

Every successfully routed batch commits exactly one of `close_as_noise` or
`create_product_signal`. Unresolved uncertainty, and a batch in a flow the demo
does not run, commit `routing_blocked` instead and preserve the Intake Item.

One transaction carries the Intake Item's move, the rows the outcome creates,
the immutable routing decision, and the follow-up Job. Outreach begins only
after that transaction commits.

Contract: docs/arch-components/signal-and-outreach.md#routing-contract
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import FlowType, ProductSignalState, RoutingOutcome

from app.envelope import JsonValue
from app.ids import uuid7
from app.intake.concerns import insert_concern
from app.intake.context import RoutingContext
from app.intake.errors import UnwatchedSurfaceError
from app.intake.jobs import send_initial_invitation_job
from app.intake.routing_record import (
    CommittedRoute,
    excerpt,
    route_intake_item_state,
    write_routing_decision,
)
from app.logging import get_logger

__all__ = [
    "commit_blocked",
    "commit_noise",
    "commit_product_signal",
]

_log = get_logger(__name__)

_CONCERN_ORIGIN_ROUTING = "routing_inference"


async def commit_blocked(
    conn: AsyncConnection[DictRow],
    *,
    context: RoutingContext,
    attempt_index: int,
    reason: str,
    decision_model_id: str,
    questions: Mapping[str, JsonValue],
    answers: Mapping[str, JsonValue],
    acceptance: Mapping[str, JsonValue],
) -> CommittedRoute:
    """Preserve the Intake Item in `routing_blocked` with its unresolved facts."""
    await route_intake_item_state(
        conn,
        context=context,
        trigger="block_routing",
        mutation={"blocked_reason": reason},
        event_type="intake_routing_blocked",
        payload={"reason": reason},
        reason_code="routing_blocked",
        jobs=(),
    )
    await write_routing_decision(
        conn,
        context=context,
        attempt_index=attempt_index,
        outcome=None,
        blocked_reason=reason,
        product_signal_id=None,
        source_report_id=None,
        decision_model_id=decision_model_id,
        questions=questions,
        answers=answers,
        acceptance=acceptance,
    )
    _log.warning(
        "intake_routing_blocked",
        client_id=str(context.scope.client_id),
        intake_item_id=str(context.scope.intake_item_id),
        reason=reason,
    )
    return CommittedRoute(
        intake_item_id=context.scope.intake_item_id,
        outcome=None,
        product_signal_id=None,
        source_report_id=None,
        blocked_reason=reason,
    )


async def commit_noise(
    conn: AsyncConnection[DictRow],
    *,
    context: RoutingContext,
    attempt_index: int,
    pointer: str | None,
    decision_model_id: str,
    questions: Mapping[str, JsonValue],
    answers: Mapping[str, JsonValue],
    acceptance: Mapping[str, JsonValue],
) -> CommittedRoute:
    """Close the batch as noise, recording the optional source-linked pointer.

    Noise ends the flow immediately: no Product Signal, no interview, no
    implementation, and no client output follow.
    """
    await route_intake_item_state(
        conn,
        context=context,
        trigger="commit_route",
        mutation={"routing_outcome": RoutingOutcome.CLOSE_AS_NOISE.value},
        event_type="intake_closed_as_noise",
        payload={"pointer": pointer},
        reason_code=RoutingOutcome.CLOSE_AS_NOISE.value,
        jobs=(),
    )
    await write_routing_decision(
        conn,
        context=context,
        attempt_index=attempt_index,
        outcome=RoutingOutcome.CLOSE_AS_NOISE,
        blocked_reason=None,
        product_signal_id=None,
        source_report_id=None,
        decision_model_id=decision_model_id,
        questions=questions,
        answers=answers,
        acceptance=acceptance,
    )
    return CommittedRoute(
        intake_item_id=context.scope.intake_item_id,
        outcome=RoutingOutcome.CLOSE_AS_NOISE,
        product_signal_id=None,
        source_report_id=None,
        blocked_reason=None,
    )


async def _insert_product_signal(
    conn: AsyncConnection[DictRow], *, context: RoutingContext
) -> tuple[UUID, UUID]:
    """Insert one Feature Request Flow Product Signal and return it with its scope."""
    signal_id = uuid7()
    cancellation_scope_id = uuid7()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.product_signals (
                id, client_id, state, intake_item_id, pinned_configuration_release_id,
                originating_client_user_id, originating_customer_account_id,
                initial_flow_type, cancellation_scope_id
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                signal_id,
                context.scope.client_id,
                ProductSignalState.AWAITING_INTERVIEW.value,
                context.scope.intake_item_id,
                context.release.id,
                context.scope.client_user_id,
                context.scope.customer_account_id,
                FlowType.FEATURE_REQUEST_FLOW.value,
                cancellation_scope_id,
            ),
        )
    return signal_id, cancellation_scope_id


async def _insert_originating_evidence(
    conn: AsyncConnection[DictRow], *, context: RoutingContext, signal_id: UUID
) -> UUID:
    """Record the originating source report and one evidence row per message."""
    report_id = uuid7()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.source_reports (
                id, client_id, product_signal_id, intake_item_id,
                reporting_client_user_id, reporting_customer_account_id,
                source_excerpt, occurred_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                report_id,
                context.scope.client_id,
                signal_id,
                context.scope.intake_item_id,
                context.scope.client_user_id,
                context.scope.customer_account_id,
                excerpt(context),
                context.scope.occurred_at,
            ),
        )
        for message in context.messages:
            await cur.execute(
                """
                insert into public.evidence_records (
                    id, client_id, product_signal_id, evidence_class, claim_state,
                    source_ref, context_revision, body
                )
                values (%s, %s, %s, 'intake_source', 'proposed', %s, %s, %s)
                """,
                (
                    uuid7(),
                    context.scope.client_id,
                    signal_id,
                    f"{context.scope.channel_ref}/{message.provider_message_id}",
                    context.overview.version_number,
                    Jsonb(
                        {
                            "ordinal": message.ordinal,
                            "content": message.content,
                            "occurred_at": message.occurred_at,
                            "reply_to_provider_message_id": (
                                message.reply_to_provider_message_id
                            ),
                            "thread_ref": message.thread_ref,
                        }
                    ),
                ),
            )
    return report_id


def _channel_name(context: RoutingContext) -> str:
    """Return the configured name of the watched channel the report came from."""
    surface = context.release.policy.customer_surface
    for channel in surface.watched_channels:
        if channel.channel_ref == context.scope.channel_ref:
            return channel.name
    raise UnwatchedSurfaceError(
        client_id=context.scope.client_id, channel_ref=context.scope.channel_ref
    )


def _routing_concern(context: RoutingContext) -> tuple[str, str]:
    """Return the source-linked pain point and validation criterion inference.

    Both preserve the originating user's wording and name no detail the report
    did not supply; the interview establishes the final understanding.
    """
    wording = excerpt(context)
    pain_point = (
        f"Reported in #{_channel_name(context)} by the originating user: {wording}"
    )
    criterion = (
        "The originating user can accomplish the job they described without the "
        f"friction they reported: {wording}"
    )
    return pain_point, criterion


async def commit_product_signal(
    conn: AsyncConnection[DictRow],
    *,
    context: RoutingContext,
    attempt_index: int,
    decision_model_id: str,
    questions: Mapping[str, JsonValue],
    answers: Mapping[str, JsonValue],
    acceptance: Mapping[str, JsonValue],
) -> CommittedRoute:
    """Create one Product Signal, its evidence, and its initial invitation Job."""
    signal_id, cancellation_scope_id = await _insert_product_signal(conn, context=context)
    report_id = await _insert_originating_evidence(
        conn, context=context, signal_id=signal_id
    )
    pain_point, criterion = _routing_concern(context)
    await insert_concern(
        conn,
        client_id=context.scope.client_id,
        product_signal_id=signal_id,
        concern_revision=0,
        origin=_CONCERN_ORIGIN_ROUTING,
        pain_point=pain_point,
        validation_criterion=criterion,
        user_wording={"messages": [message.content for message in context.messages]},
        unknowns={
            "impact": "unknown",
            "frequency_for_reporter": "unknown",
            "workaround": "unknown",
            "expected_behavior": "unknown",
        },
    )
    await route_intake_item_state(
        conn,
        context=context,
        trigger="commit_route",
        mutation={
            "routing_outcome": RoutingOutcome.CREATE_PRODUCT_SIGNAL.value,
            "product_signal_id": signal_id,
        },
        event_type="product_signal_created",
        payload={
            "product_signal_id": str(signal_id),
            "initial_flow_type": FlowType.FEATURE_REQUEST_FLOW.value,
            "originating_client_user_id": str(context.scope.client_user_id),
        },
        reason_code=RoutingOutcome.CREATE_PRODUCT_SIGNAL.value,
        jobs=(
            send_initial_invitation_job(
                client_id=context.scope.client_id,
                product_signal_id=signal_id,
                cancellation_scope_id=cancellation_scope_id,
            ),
        ),
    )
    await write_routing_decision(
        conn,
        context=context,
        attempt_index=attempt_index,
        outcome=RoutingOutcome.CREATE_PRODUCT_SIGNAL,
        blocked_reason=None,
        product_signal_id=signal_id,
        source_report_id=report_id,
        decision_model_id=decision_model_id,
        questions=questions,
        answers=answers,
        acceptance=acceptance,
    )
    _log.info(
        "product_signal_created",
        client_id=str(context.scope.client_id),
        product_signal_id=str(signal_id),
    )
    return CommittedRoute(
        intake_item_id=context.scope.intake_item_id,
        outcome=RoutingOutcome.CREATE_PRODUCT_SIGNAL,
        product_signal_id=signal_id,
        source_report_id=report_id,
        blocked_reason=None,
    )

