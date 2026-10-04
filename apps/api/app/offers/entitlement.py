"""Participation-based entitlement for one Outreach Offer.

The [completion conditions](../../../../docs/PRD.md#outreach-offers) are evaluated
from recorded participation, gate decisions, validation results, revision
accounting, and the reason the work ended. A session ending, a Case File
existing, or an agent saying "done" is not evidence, and eligibility is never
assigned from a terminal state label alone.

``decide`` is pure so every rule is provable without a database. The controller
below reads the facts, then converts the reservation into an earned obligation or
releases it, atomically and exactly once per offer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    GateDecisionKind,
    OfferLedgerEntryKind,
    OfferState,
    ValidationOutcome,
)

from app.db.kernel import apply_transition_on_connection
from app.db.pool import Database
from app.db.rows import as_int, as_optional_str, as_str, as_uuid
from app.envelope import (
    TRANSIENT_CATEGORIES,
    Actor,
    AuditIntent,
    CommandIntent,
    EventAppend,
    JobIntent,
    StreamRef,
)
from app.ids import uuid7
from app.lifecycle.machine import plan_transition
from app.logging import get_logger
from app.offers.job_types import ISSUE_CREDIT
from app.offers.ledger import record_entry
from app.offers.machine import OFFER_MACHINE, OfferFacts

__all__ = [
    "CaseStopReason",
    "Entitlement",
    "EntitlementDecision",
    "ParticipationFacts",
    "decide",
    "evaluate_entitlement",
]

_log = get_logger(__name__)

_SERVICE_ACTOR = Actor(kind=ActorKind.SERVICE, ref="offers.entitlement")
_ISSUANCE_DEADLINE_SECONDS = 30 * 24 * 60 * 60


class CaseStopReason(StrEnum):
    """Why the work on a case ended, as the controller recorded it."""

    VALIDATION_COMPLETED = "validation_completed"
    GATE_NO_IMPLEMENTATION = "gate_no_implementation"
    CLIENT_STOP = "client_stop"
    TECHNICAL_BLOCKER = "technical_blocker"
    SCOPE_LIMIT = "scope_limit"
    TEMPORARY_BLOCKER = "temporary_blocker"
    VALIDATION_RETURN_DEADLINE_EXPIRED = "validation_return_deadline_expired"
    CONTACT_EXPIRY_WITHOUT_RETURN = "contact_expiry_without_return"
    PARTICIPANT_CANCELED = "participant_canceled"
    DECLINED_CONTINUATION = "declined_continuation"


class EntitlementDecision(StrEnum):
    """Whether the promised reward is earned, not earned, or still pending."""

    EARNED = "earned"
    NOT_EARNED = "not_earned"
    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class ParticipationFacts:
    """What the originating user actually did, read from durable records."""

    participation_started: bool
    interview_reached_gate: bool
    gate_decision: GateDecisionKind | None
    implementation_dispatched: bool
    validation_outcome: ValidationOutcome | None
    outstanding_participation: bool
    stop_reason: CaseStopReason


@dataclass(frozen=True, slots=True)
class Entitlement:
    """The recorded disposition of one offer."""

    outreach_offer_id: UUID
    decision: EntitlementDecision
    reason: str
    offer_entitlement_id: UUID | None
    issuance_command_id: UUID | None


_NEVER_EARNS: frozenset[CaseStopReason] = frozenset(
    {
        CaseStopReason.PARTICIPANT_CANCELED,
        CaseStopReason.DECLINED_CONTINUATION,
    }
)

_ABANDONED: frozenset[CaseStopReason] = frozenset(
    {
        CaseStopReason.CONTACT_EXPIRY_WITHOUT_RETURN,
        CaseStopReason.VALIDATION_RETURN_DEADLINE_EXPIRED,
    }
)

_SYSTEM_STOPS: frozenset[CaseStopReason] = frozenset(
    {
        CaseStopReason.CLIENT_STOP,
        CaseStopReason.TECHNICAL_BLOCKER,
        CaseStopReason.SCOPE_LIMIT,
    }
)


def decide(facts: ParticipationFacts) -> tuple[EntitlementDecision, str]:
    """Return the entitlement decision and the sentence that justifies it."""
    if not facts.participation_started:
        return (
            EntitlementDecision.NOT_EARNED,
            "the case ended before the originating user participated",
        )
    if facts.stop_reason in _NEVER_EARNS:
        return (
            EntitlementDecision.NOT_EARNED,
            "the originating user withdrew or declined a required continuation",
        )
    if facts.stop_reason is CaseStopReason.TEMPORARY_BLOCKER:
        return (
            EntitlementDecision.PENDING,
            "a temporary blocker leaves the promised participation unfinished",
        )
    if facts.stop_reason in _ABANDONED:
        if facts.outstanding_participation:
            return (
                EntitlementDecision.NOT_EARNED,
                "participation uTrace asked for was outstanding when the case expired",
            )
        return (
            EntitlementDecision.EARNED,
            "the originating user fulfilled every request before the case expired",
        )
    if facts.stop_reason is CaseStopReason.GATE_NO_IMPLEMENTATION:
        if not facts.interview_reached_gate:
            return (
                EntitlementDecision.NOT_EARNED,
                "the interview did not reach the Implementation Gate",
            )
        return (
            EntitlementDecision.EARNED,
            "a completed interview followed by no implementation earns the reward",
        )
    if facts.stop_reason is CaseStopReason.VALIDATION_COMPLETED:
        if facts.validation_outcome is None:
            return (
                EntitlementDecision.PENDING,
                "no structured validation outcome is recorded yet",
            )
        return (
            EntitlementDecision.EARNED,
            (
                "the originating user participated through validation; a negative "
                "verdict does not deny the reward"
            ),
        )
    if facts.stop_reason in _SYSTEM_STOPS:
        if facts.outstanding_participation:
            return (
                EntitlementDecision.NOT_EARNED,
                (
                    "participation uTrace asked for was still outstanding when the "
                    "system-initiated stop took effect"
                ),
            )
        return (
            EntitlementDecision.EARNED,
            (
                "a system-initiated stop earns the reward when the originating user "
                "fulfilled every request made before it"
            ),
        )
    return (
        EntitlementDecision.PENDING,
        f"stop reason '{facts.stop_reason.value}' leaves eligibility undecided",
    )


async def read_participation(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID, stop_reason: CaseStopReason
) -> ParticipationFacts:
    """Read the durable participation record for one case."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select
              exists (
                select 1 from public.interview_sessions as sessions
                join public.session_readiness as readiness
                  on readiness.interview_session_id = sessions.id
                where sessions.product_signal_id = %s
                  and readiness.conversational_ready
              ) as participated,
              (select decision from public.gate_decisions
               where product_signal_id = %s order by decided_at desc limit 1)
                as gate_decision,
              exists (
                select 1 from public.implementation_attempts
                where product_signal_id = %s
              ) as implementation_dispatched,
              (select outcome from public.validation_attempts
               where product_signal_id = %s and state = 'completed'
               order by completed_at desc limit 1) as validation_outcome,
              exists (
                select 1 from public.validation_attempts
                where product_signal_id = %s and state in ('pending', 'interrupted')
              ) as open_validation
            """,
            (product_signal_id,) * 5,
        )
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"product signal {product_signal_id} has no participation record")
    gate = as_optional_str(row, "gate_decision")
    outcome = as_optional_str(row, "validation_outcome")
    return ParticipationFacts(
        participation_started=bool(row["participated"]),
        interview_reached_gate=gate is not None,
        gate_decision=None if gate is None else GateDecisionKind(gate),
        implementation_dispatched=bool(row["implementation_dispatched"]),
        validation_outcome=None if outcome is None else ValidationOutcome(outcome),
        outstanding_participation=bool(row["open_validation"]),
        stop_reason=stop_reason,
    )


async def evaluate_entitlement(
    database: Database, *, product_signal_id: UUID, stop_reason: CaseStopReason
) -> Entitlement | None:
    """Convert the case's reservation into an earned obligation, or release it.

    Returns ``None`` when the case has no Outreach Offer. Re-running is safe:
    an offer that already reached ``earned`` or ``released`` is returned as it
    stands, so one offer can never carry two entitlements.
    """
    now = datetime.now(tz=UTC)
    async with database.transaction() as conn:
        offer = await _load_offer(conn, product_signal_id=product_signal_id)
        if offer is None:
            return None
        offer_id, client_id, state, revision, currency, amount = offer
        if state is not OfferState.RESERVED:
            return await _existing(conn, offer_id=offer_id, state=state)

        facts = await read_participation(
            conn, product_signal_id=product_signal_id, stop_reason=stop_reason
        )
        decision, reason = decide(facts)
        if decision is EntitlementDecision.PENDING:
            _log.info(
                "offer_entitlement_pending",
                product_signal_id=str(product_signal_id),
                outreach_offer_id=str(offer_id),
                stop_reason=stop_reason.value,
                reason=reason,
            )
            return Entitlement(
                outreach_offer_id=offer_id,
                decision=decision,
                reason=reason,
                offer_entitlement_id=None,
                issuance_command_id=None,
            )

        if decision is EntitlementDecision.EARNED:
            return await _earn(
                conn,
                offer_id=offer_id,
                client_id=client_id,
                revision=revision,
                currency=currency,
                amount_minor_units=amount,
                product_signal_id=product_signal_id,
                facts=facts,
                reason=reason,
                now=now,
            )
        return await _release(
            conn,
            offer_id=offer_id,
            client_id=client_id,
            revision=revision,
            currency=currency,
            amount_minor_units=amount,
            reason=reason,
        )


async def _load_offer(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID
) -> tuple[UUID, UUID, OfferState, int, str, int] | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, client_id, state, revision, currency, amount_minor_units
            from public.outreach_offers
            where product_signal_id = %s
            for update
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return (
        as_uuid(row, "id"),
        as_uuid(row, "client_id"),
        OfferState(as_str(row, "state")),
        as_int(row, "revision"),
        as_str(row, "currency"),
        as_int(row, "amount_minor_units"),
    )


async def _existing(
    conn: AsyncConnection[DictRow], *, offer_id: UUID, state: OfferState
) -> Entitlement:
    """Return the disposition an already-decided offer carries."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select entitlements.id as entitlement_id, operations.command_id
            from public.offer_entitlements as entitlements
            left join public.offer_issuance_operations as operations
              on operations.offer_entitlement_id = entitlements.id
            where entitlements.outreach_offer_id = %s
            """,
            (offer_id,),
        )
        row = await cur.fetchone()
    return Entitlement(
        outreach_offer_id=offer_id,
        decision=(
            EntitlementDecision.EARNED
            if state is OfferState.EARNED
            else EntitlementDecision.NOT_EARNED
        ),
        reason=f"the offer already reached '{state.value}'",
        offer_entitlement_id=None if row is None else as_uuid(row, "entitlement_id"),
        issuance_command_id=None if row is None else as_uuid(row, "command_id"),
    )


async def _earn(
    conn: AsyncConnection[DictRow],
    *,
    offer_id: UUID,
    client_id: UUID,
    revision: int,
    currency: str,
    amount_minor_units: int,
    product_signal_id: UUID,
    facts: ParticipationFacts,
    reason: str,
    now: datetime,
) -> Entitlement:
    """Convert the reservation into the one earned obligation and queue issuance."""
    entitlement_id = uuid7()
    command_id = uuid7()
    operation_id = uuid7()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select customer_account_id, billing_account_ref, billing_customer_ref
            from public.outreach_offers where id = %s
            """,
            (offer_id,),
        )
        billing = await cur.fetchone()
        if billing is None:
            raise LookupError(f"outreach offer {offer_id} disappeared")
        billing_account_ref = as_str(billing, "billing_account_ref")
        billing_customer_ref = as_str(billing, "billing_customer_ref")
        await cur.execute(
            """
            insert into public.offer_entitlements (
                id, client_id, outreach_offer_id, product_signal_id,
                customer_account_id, currency, amount_minor_units,
                participation_evidence, stop_reason
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                entitlement_id,
                client_id,
                offer_id,
                product_signal_id,
                as_uuid(billing, "customer_account_id"),
                currency,
                amount_minor_units,
                Jsonb(
                    {
                        "participation_started": facts.participation_started,
                        "interview_reached_gate": facts.interview_reached_gate,
                        "gate_decision": (
                            None if facts.gate_decision is None else facts.gate_decision.value
                        ),
                        "implementation_dispatched": facts.implementation_dispatched,
                        "validation_outcome": (
                            None
                            if facts.validation_outcome is None
                            else facts.validation_outcome.value
                        ),
                        "outstanding_participation": facts.outstanding_participation,
                        "reason": reason,
                    }
                ),
                facts.stop_reason.value,
            ),
        )
        await cur.execute(
            """
            update public.offer_reservations
            set converted_at = now(), updated_at = now()
            where outreach_offer_id = %s and converted_at is null and released_at is null
            """,
            (offer_id,),
        )

    stream = StreamRef(kind=AggregateKind.OUTREACH_OFFER, id=offer_id)
    request = plan_transition(
        OFFER_MACHINE,
        OfferFacts(
            participation_complete=True,
            outstanding_participation=False,
            entitlement_recorded=True,
        ),
        trigger="earn",
        current_state=OfferState.RESERVED.value,
        client_id=client_id,
        aggregate_kind=AggregateKind.OUTREACH_OFFER,
        aggregate_id=offer_id,
        expected_revision=revision,
        state_column="state",
        mutation={},
        event=EventAppend(
            message_id=uuid7(),
            stream=stream,
            event_type="outreach_offer_earned",
            payload={"reason": reason, "stop_reason": facts.stop_reason.value},
            actor=_SERVICE_ACTOR,
            causation_id=None,
            correlation_id=product_signal_id,
            occurred_at=now,
        ),
        jobs=(
            JobIntent(
                job_id=uuid7(),
                job_type=ISSUE_CREDIT,
                input={
                    "offer_issuance_operation_id": str(operation_id),
                    "command_id": str(command_id),
                },
                deadline_at=now + timedelta(seconds=_ISSUANCE_DEADLINE_SECONDS),
                timeout_seconds=120,
                max_runs=8,
                resource_budget={"kind": "offer_issuance"},
                retryable_categories=TRANSIENT_CATEGORIES,
                backoff_base_seconds=30,
                backoff_max_seconds=3600,
                concurrency_key=f"offer_issuance:{offer_id}",
                cancellation_scope_id=None,
                not_before=now,
            ),
        ),
        commands=(
            CommandIntent(
                command_id=command_id,
                command_type="stripe.create_customer_balance_transaction",
                target=stream,
                payload={
                    "offer_issuance_operation_id": str(operation_id),
                    "amount_minor_units": amount_minor_units,
                    "currency": currency,
                },
                declared_revisions={"outreach_offer": revision},
                cancellation_scope_id=None,
            ),
        ),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=stream,
            action="record_offer_entitlement",
            outcome="earned",
            reason_code=facts.stop_reason.value,
            actor=_SERVICE_ACTOR,
            pinned_configuration_release_id=None,
            detail={"reason": reason},
        ),
        additional_locks=(),
    )
    await apply_transition_on_connection(conn, request)
    # The issuance operation references the Command the transition just wrote, so
    # it is inserted after the commit order, never before its own key exists.
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.offer_issuance_operations (
                id, client_id, offer_entitlement_id, command_id, state, currency,
                amount_minor_units, idempotency_key, billing_account_ref,
                billing_customer_ref
            )
            values (%s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s)
            """,
            (
                operation_id,
                client_id,
                entitlement_id,
                command_id,
                currency,
                amount_minor_units,
                f"utrace-offer-issuance-{operation_id}",
                billing_account_ref,
                billing_customer_ref,
            ),
        )
    await record_entry(
        conn,
        client_id=client_id,
        currency=currency,
        entry_kind=OfferLedgerEntryKind.CREDIT_EARNED,
        delta_minor_units=0,
        outreach_offer_id=offer_id,
        offer_entitlement_id=entitlement_id,
        offer_issuance_operation_id=operation_id,
        causal_evidence={"reason": reason, "stop_reason": facts.stop_reason.value},
    )
    _log.info(
        "offer_entitlement_earned",
        client_id=str(client_id),
        product_signal_id=str(product_signal_id),
        outreach_offer_id=str(offer_id),
        offer_entitlement_id=str(entitlement_id),
        amount_minor_units=amount_minor_units,
        currency=currency,
        stop_reason=facts.stop_reason.value,
    )
    return Entitlement(
        outreach_offer_id=offer_id,
        decision=EntitlementDecision.EARNED,
        reason=reason,
        offer_entitlement_id=entitlement_id,
        issuance_command_id=command_id,
    )


async def _release(
    conn: AsyncConnection[DictRow],
    *,
    offer_id: UUID,
    client_id: UUID,
    revision: int,
    currency: str,
    amount_minor_units: int,
    reason: str,
) -> Entitlement:
    """Release an unearned reservation back to the client's allowance."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.offer_reservations
            set released_at = now(), updated_at = now()
            where outreach_offer_id = %s and released_at is null and converted_at is null
            """,
            (offer_id,),
        )
    stream = StreamRef(kind=AggregateKind.OUTREACH_OFFER, id=offer_id)
    request = plan_transition(
        OFFER_MACHINE,
        OfferFacts(
            participation_complete=False,
            outstanding_participation=True,
            entitlement_recorded=False,
        ),
        trigger="release",
        current_state=OfferState.RESERVED.value,
        client_id=client_id,
        aggregate_kind=AggregateKind.OUTREACH_OFFER,
        aggregate_id=offer_id,
        expected_revision=revision,
        state_column="state",
        mutation={},
        event=EventAppend(
            message_id=uuid7(),
            stream=stream,
            event_type="outreach_offer_released",
            payload={"reason": reason},
            actor=_SERVICE_ACTOR,
            causation_id=None,
            correlation_id=offer_id,
            occurred_at=datetime.now(tz=UTC),
        ),
        jobs=(),
        commands=(),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=stream,
            action="record_offer_entitlement",
            outcome="released",
            reason_code=None,
            actor=_SERVICE_ACTOR,
            pinned_configuration_release_id=None,
            detail={"reason": reason},
        ),
        additional_locks=(),
    )
    await apply_transition_on_connection(conn, request)
    await record_entry(
        conn,
        client_id=client_id,
        currency=currency,
        entry_kind=OfferLedgerEntryKind.RESERVATION_RELEASED,
        delta_minor_units=amount_minor_units,
        outreach_offer_id=offer_id,
        offer_entitlement_id=None,
        offer_issuance_operation_id=None,
        causal_evidence={"reason": reason},
    )
    _log.info(
        "offer_reservation_released",
        client_id=str(client_id),
        outreach_offer_id=str(offer_id),
        amount_minor_units=amount_minor_units,
        currency=currency,
        reason=reason,
    )
    return Entitlement(
        outreach_offer_id=offer_id,
        decision=EntitlementDecision.NOT_EARNED,
        reason=reason,
        offer_entitlement_id=None,
        issuance_command_id=None,
    )
