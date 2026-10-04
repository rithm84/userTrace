"""The interview-established flow classification, confirmed before a gate proposal.

Jev proposes which flow the concern belongs to from the interview's own context
bundle: the participant's statements, the recorded evidence, and the agreed
scope. An accepted proposal is recorded as the typed, source-linked
classification the gate screens against. An unresolved proposal is recorded as
a judgement and nothing more, so the gate blocks on the missing classification
rather than inventing one.

The interview carries on while Jev judges, so the case can move under the
judgement. When something the classification depends on moved, the stale
judgement is not mistaken for an unresolved one: the classification is judged
again on a fresh bundle, a bounded number of times.

Contract: docs/arch-components/interview-and-voice.md#flow-classification
"""

from typing import Final
from uuid import UUID

from utrace_contracts.domain.lifecycle import FlowType

from app.connectors.typesafe_jev import JevClient
from app.context.bundles import assemble_bundle
from app.context.freshness import decide_on_fresh_context
from app.context.purposes import BundlePurpose
from app.db.pool import Database
from app.db.rows import as_optional_str, as_uuid
from app.errors import AggregateNotFoundError, StaleContextError
from app.interview.agreements import record_flow_classification
from app.interview.attention import propose_flow_classification
from app.interview.settings import get_interview_settings
from app.logging import get_logger

__all__ = ["establish_flow_classification"]

_log = get_logger(__name__)

_QUESTION = "classification::established_flow"
_ATTEMPTS: Final[int] = 3
"""How many times the classification is judged in all while the case moves under it."""


async def establish_flow_classification(
    database: Database, jev: JevClient, *, client_id: UUID, product_signal_id: UUID
) -> FlowType | None:
    """Return the case's established flow, confirming it first if the interview has not.

    `None` means the evidence did not settle the flow; the gate then records no
    implementation for want of a classification. A judgement the case moved under
    is made again on fresh context; the last `StaleContextError` is raised.
    """
    return await decide_on_fresh_context(
        step="flow_classification",
        product_signal_id=product_signal_id,
        attempts=_ATTEMPTS,
        decide=lambda: _establish(
            database, jev, client_id=client_id, product_signal_id=product_signal_id
        ),
    )


async def _establish(
    database: Database, jev: JevClient, *, client_id: UUID, product_signal_id: UUID
) -> FlowType | None:
    """Judge and record the flow once, on a bundle assembled now."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select signals.established_flow_type::text as established,
                   (
                     select sessions.id from public.interview_sessions as sessions
                     where sessions.product_signal_id = signals.id
                     order by sessions.created_at desc
                     limit 1
                   ) as interview_session_id
            from public.product_signals as signals
            where signals.id = %s
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="product_signals", row_id=product_signal_id)
    established = as_optional_str(row, "established")
    if established is not None:
        return FlowType(established)
    if row["interview_session_id"] is None:
        _log.info(
            "flow_classification_unresolved",
            client_id=str(client_id),
            product_signal_id=str(product_signal_id),
            reason="no_interview_session",
        )
        return None

    settings = get_interview_settings()
    bundle = await assemble_bundle(
        database,
        client_id=client_id,
        product_signal_id=product_signal_id,
        interview_session_id=as_uuid(row, "interview_session_id"),
        purpose=BundlePurpose.FLOW_CLASSIFICATION,
        budget_items=settings.context_budget_items,
        observations=(),
        extra_items=(),
        recent_fragment_limit=settings.recent_conversation_fragments,
        prior_command_limit=settings.selected_observation_limit,
    )
    proposal = await propose_flow_classification(database, jev, bundle=bundle)
    if not proposal.fresh:
        raise StaleContextError(trigger="propose_flow_classification", reason=proposal.detail)
    choice = proposal.selections.get(_QUESTION)
    if not proposal.accepted or choice is None or choice not in {
        flow.value for flow in FlowType
    }:
        _log.info(
            "flow_classification_unresolved",
            client_id=str(client_id),
            product_signal_id=str(product_signal_id),
            semantic_judgement_id=str(proposal.semantic_judgement_id),
            reason=proposal.detail,
        )
        return None

    flow = FlowType(choice)
    await record_flow_classification(
        database,
        bundle=bundle,
        flow_type=flow,
        rationale=(
            f"Jev judged the interview evidence to support {flow.value} "
            f"(confidence {proposal.confidences.get(_QUESTION, 0.0):.2f})."
        ),
        supporting_evidence_ids=bundle.cited_evidence_ids(),
        semantic_judgement_id=proposal.semantic_judgement_id,
    )
    _log.info(
        "flow_classification_established",
        client_id=str(client_id),
        product_signal_id=str(product_signal_id),
        flow_type=flow.value,
        semantic_judgement_id=str(proposal.semantic_judgement_id),
    )
    return flow
