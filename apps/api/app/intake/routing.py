"""The Intake Router.

One sealed Intake Item becomes exactly one committed routing outcome. Jev
supplies the semantic decisions; application code assembles the bounded context,
checks the answers, applies deterministic policy, and owns the commit.

Noise ends the flow first. A batch worth investigating then needs a resolved
flow type: Feature Request Flow creates a Product Signal and its invitation, and a
product failure stays in `routing_blocked` with the reason `unsupported_flow`,
because the demo does not run Bugfix Flow. Every routed report opens its own
Product Signal; intake never matches a batch against existing ones.

Contract: docs/arch-components/signal-and-outreach.md#routing-context-and-cognition
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from utrace_contracts.domain.lifecycle import FlowType, RoutingOutcome

from app.clients.configuration import load_active_release
from app.clients.overview import load_active_overview
from app.connectors.openai_models import OpenAIResponsesGateway
from app.connectors.ports import CustomerSurfacePort
from app.connectors.typesafe_jev import JevClient
from app.db.pool import Database
from app.db.rows import as_str, as_uuid
from app.errors import AggregateNotFoundError
from app.intake.assistance import interpret_batch
from app.intake.cognition import RoutingJudgement, ask_routing_questions
from app.intake.commit import commit_blocked, commit_noise, commit_product_signal
from app.intake.context import (
    RoutingContext,
    assemble_context,
    load_scope,
    with_assistance,
    with_interpretation,
)
from app.intake.errors import RoutingBlockedError, UnwatchedSurfaceError
from app.intake.routing_record import CommittedRoute
from app.logging import get_logger

__all__ = [
    "UNSUPPORTED_FLOW",
    "RoutingDependencies",
    "route_intake_item",
]

_log = get_logger(__name__)

UNSUPPORTED_FLOW: Final[str] = "unsupported_flow"
"""The blocked reason for a batch in a flow the demo does not run."""

_NOISE_POINTER: Final[str] = (
    "Thanks for the note. This did not look like a product problem to investigate, so "
    "no interview was scheduled."
)


@dataclass(frozen=True, slots=True)
class RoutingDependencies:
    """The providers routing may call.

    `surface` is the scoped fetch of conditional assistance; without it there are
    no nearby messages to read. `reasoning` is GPT-6.1 Sol under the
    intake-assistance role, the last branch before a batch is blocked.
    """

    jev: JevClient
    surface: CustomerSurfacePort | None
    reasoning: OpenAIResponsesGateway


async def _already_routed(
    database: Database, *, intake_item_id: UUID
) -> CommittedRoute | None:
    """Return the committed outcome when this Intake Item was already routed."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state, routing_outcome, product_signal_id "
            "from public.intake_items where id = %s",
            (intake_item_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="intake_items", row_id=intake_item_id)
    if as_str(row, "state") != "routed":
        return None
    outcome = row["routing_outcome"]
    return CommittedRoute(
        intake_item_id=intake_item_id,
        outcome=RoutingOutcome(str(outcome)) if outcome is not None else None,
        product_signal_id=(
            as_uuid(row, "product_signal_id")
            if row["product_signal_id"] is not None
            else None
        ),
        source_report_id=None,
        blocked_reason=None,
    )


def _missing_facts(judgement: RoutingJudgement) -> tuple[str, ...]:
    """Return the question ids whose answers routing could not resolve."""
    missing = list(judgement.unresolved)
    if judgement.context_sufficient is False:
        missing.append("supplied_context")
    return tuple(dict.fromkeys(missing))


async def _fetch_nearby_context(
    surface: CustomerSurfacePort, *, context: RoutingContext
) -> tuple[str, ...]:
    """Fetch the direct reply targets the batch references, and nothing else.

    Intake fetches nearby Discord context only when the batch explicitly
    requires it, so only messages the batch itself names are read.
    """
    fetched: list[str] = []
    for message in context.messages:
        target = message.reply_to_provider_message_id
        if target is None:
            continue
        receipt = await surface.fetch_message(
            channel_id=context.scope.channel_ref, message_id=target
        )
        fetched.append(f"[reply target {target}] {receipt.content}")
    return tuple(fetched)


async def _retrieve(
    dependencies: RoutingDependencies,
    *,
    context: RoutingContext,
    missing: Sequence[str],
) -> RoutingContext | None:
    """Widen the context once, or return None when it already holds everything.

    The rest of the Product Overview joins the context, with any nearby messages
    the batch names.
    """
    if context.product_documentation is not None:
        return None
    nearby: tuple[str, ...] = ()
    if dependencies.surface is not None:
        nearby = await _fetch_nearby_context(dependencies.surface, context=context)
    return with_assistance(
        context,
        reason=f"required facts unresolved: {', '.join(missing)}",
        nearby_messages=nearby,
    )


async def _interpret(
    dependencies: RoutingDependencies,
    *,
    context: RoutingContext,
    unresolved: Sequence[str],
) -> RoutingContext:
    """Add GPT-6.1 Sol's interpretation, or block when the batch cannot be interpreted."""
    interpretation = await interpret_batch(
        dependencies.reasoning, context=context, unresolved=unresolved
    )
    if not interpretation.interpretable:
        raise RoutingBlockedError(
            intake_item_id=context.scope.intake_item_id,
            reason=(
                f"required facts unresolved: {', '.join(unresolved)}; the batch could "
                "not be interpreted from the supplied facts"
            ),
        )
    return with_interpretation(
        context,
        reason=f"interpretation needed for: {', '.join(unresolved)}",
        interpretation=interpretation.interpretation,
    )


async def _commit(
    database: Database,
    *,
    context: RoutingContext,
    judgement: RoutingJudgement,
    attempt_index: int,
) -> CommittedRoute:
    """Apply deterministic policy to the accepted answers and commit one outcome."""
    if judgement.warrants_investigation is False:
        async with database.transaction() as conn:
            return await commit_noise(
                conn,
                context=context,
                attempt_index=attempt_index,
                pointer=_NOISE_POINTER,
                decision_model_id=judgement.model_id,
                questions=judgement.questions,
                answers=judgement.answers,
                acceptance=judgement.acceptance,
            )

    flow = judgement.flow_type
    if flow is None:
        raise RoutingBlockedError(
            intake_item_id=context.scope.intake_item_id,
            reason="the flow type stayed unresolved for a batch worth investigating",
        )
    if flow is not FlowType.FEATURE_REQUEST_FLOW:
        raise RoutingBlockedError(
            intake_item_id=context.scope.intake_item_id, reason=UNSUPPORTED_FLOW
        )

    async with database.transaction() as conn:
        return await commit_product_signal(
            conn,
            context=context,
            attempt_index=attempt_index,
            decision_model_id=judgement.model_id,
            questions=judgement.questions,
            answers=judgement.answers,
            acceptance=judgement.acceptance,
        )


async def route_intake_item(
    database: Database,
    dependencies: RoutingDependencies,
    *,
    intake_item_id: UUID,
) -> CommittedRoute:
    """Route one sealed Intake Item and commit exactly one outcome.

    Replaying a routed Intake Item returns its committed outcome without asking
    the decision model again.
    """
    replayed = await _already_routed(database, intake_item_id=intake_item_id)
    if replayed is not None:
        _log.info("intake_routing_replayed", intake_item_id=str(intake_item_id))
        return replayed

    scope = await load_scope(database, intake_item_id=intake_item_id)
    release = await load_active_release(database, client_id=scope.client_id)
    if not release.policy.customer_surface.watches(scope.channel_ref):
        raise UnwatchedSurfaceError(
            client_id=scope.client_id, channel_ref=scope.channel_ref
        )
    overview = await load_active_overview(database, client_id=scope.client_id)

    context = await assemble_context(
        database,
        intake_item_id=intake_item_id,
        release=release,
        overview=overview,
        scope=scope,
    )
    judgement = await ask_routing_questions(dependencies.jev, context=context)
    attempt_index = 1
    missing = _missing_facts(judgement)
    if missing:
        retrieved = await _retrieve(dependencies, context=context, missing=missing)
        if retrieved is not None:
            context = retrieved
            attempt_index += 1
            judgement = await ask_routing_questions(dependencies.jev, context=context)
    if judgement.unresolved:
        try:
            context = await _interpret(
                dependencies, context=context, unresolved=judgement.unresolved
            )
        except RoutingBlockedError as blocked:
            return await _block(
                database,
                context=context,
                judgement=judgement,
                attempt_index=attempt_index,
                reason=blocked.reason,
            )
        attempt_index += 1
        judgement = await ask_routing_questions(dependencies.jev, context=context)
        if judgement.unresolved:
            return await _block(
                database,
                context=context,
                judgement=judgement,
                attempt_index=attempt_index,
                reason=(
                    "required facts unresolved after assistance: "
                    f"{', '.join(judgement.unresolved)}"
                ),
            )

    try:
        return await _commit(
            database,
            context=context,
            judgement=judgement,
            attempt_index=attempt_index,
        )
    except RoutingBlockedError as blocked:
        return await _block(
            database,
            context=context,
            judgement=judgement,
            attempt_index=attempt_index,
            reason=blocked.reason,
        )


async def _block(
    database: Database,
    *,
    context: RoutingContext,
    judgement: RoutingJudgement,
    attempt_index: int,
    reason: str,
) -> CommittedRoute:
    """Commit `routing_blocked`, preserving the Intake Item and its analysis."""
    async with database.transaction() as conn:
        return await commit_blocked(
            conn,
            context=context,
            attempt_index=attempt_index,
            reason=reason,
            decision_model_id=judgement.model_id,
            questions=judgement.questions,
            answers=judgement.answers,
            acceptance=judgement.acceptance,
        )
