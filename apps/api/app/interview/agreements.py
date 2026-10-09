"""What the interview establishes and the control plane freezes.

Two records, both typed and source-linked, both immutable once written:

- The interview-established flow classification, which retains or corrects Jev's
  initial judgement before any gate proposal. A spoken label alone changes
  neither policy nor state.
- The agreed scope and its acceptance criteria at one criteria revision. The
  frozen criteria themselves go to `public.validation_criteria`, which
  validation reads.

Both reject a stale bundle: a result assembled against revisions that have since
moved cannot update the current scope. Correcting a classification a gate
already used raises a hold on new implementation steps until a fresh gate
decision exists; historical gate records stay fixed and consumed budgets do not
reset.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    CriterionObservability,
    FlowType,
    ProductSignalState,
)

from app.context.bundles import AssembledBundle
from app.context.freshness import check_bundle_freshness_on_connection
from app.db.kernel import append_fact
from app.db.pool import Database
from app.db.rows import as_int, as_str, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, JsonValue, StreamRef
from app.errors import GuardRejectedError, StaleContextError
from app.gate.job_types import decide_gate_job
from app.ids import uuid7
from app.interview.projection import return_to_implementation_on_connection
from app.lifecycle.product_signal import ProductSignalFacts
from app.lifecycle.signal_transitions import advance_product_signal_on_connection
from app.logging import get_logger
from app.validation.controller import abandon_validation_attempt, open_attempt_id

__all__ = [
    "AcceptanceCriteriaAgreement",
    "ChangeRequest",
    "CriterionDraft",
    "FlowClassificationRecord",
    "record_acceptance_criteria_agreement",
    "record_change_request",
    "record_flow_classification",
]

_log = get_logger(__name__)

_AGENT_ACTOR = Actor(kind=ActorKind.SERVICE, ref="interview.agreements")
_CLASSIFICATION_SUPERSEDED = "classification_revision_superseded"
_VALIDATING_STATES = frozenset(
    {ProductSignalState.AWAITING_VALIDATION.value, ProductSignalState.VALIDATING.value}
)


@dataclass(frozen=True, slots=True)
class CriterionDraft:
    """One acceptance criterion the interview agreed, before it is frozen."""

    ordinal: int
    statement: str
    observability: CriterionObservability


@dataclass(frozen=True, slots=True)
class FlowClassificationRecord:
    """The committed interview-established classification at one revision."""

    record_id: UUID
    product_signal_id: UUID
    classification_revision: int
    flow_type: FlowType
    retained: bool
    gate_hold_id: UUID | None


@dataclass(frozen=True, slots=True)
class AcceptanceCriteriaAgreement:
    """The committed agreed scope and its frozen criteria at one revision."""

    record_id: UUID
    product_signal_id: UUID
    acceptance_criteria_revision: int
    criterion_ids: tuple[UUID, ...]
    concern_revision: int


@dataclass(frozen=True, slots=True)
class ChangeRequest:
    """A change the originating user asked for in a new version, sent to the gate."""

    agreement: AcceptanceCriteriaAgreement
    implementation_number: int
    """Which of the case's implementations the change will be, counting the first."""
    implementation_limit: int


async def record_flow_classification(
    database: Database,
    *,
    bundle: AssembledBundle,
    flow_type: FlowType,
    rationale: str,
    supporting_evidence_ids: Sequence[UUID],
    semantic_judgement_id: UUID | None,
) -> FlowClassificationRecord:
    """Freeze the interview-established classification and its supporting sources."""
    async with database.transaction() as conn:
        signal = await _lock_signal(conn, bundle.product_signal_id)
        await _require_fresh(conn, bundle=bundle, action="record_flow_classification")
        initial = FlowType(as_str(signal, "initial_flow_type"))
        classification_revision = as_int(signal, "classification_revision") + 1
        record_id = uuid7()
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.flow_classification_records (
                    id, client_id, product_signal_id, interview_session_id,
                    classification_revision, flow_type, initial_flow_type, retained,
                    rationale, supporting_evidence_ids, context_bundle_id,
                    semantic_judgement_id
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record_id,
                    bundle.client_id,
                    bundle.product_signal_id,
                    bundle.interview_session_id,
                    classification_revision,
                    flow_type.value,
                    initial.value,
                    flow_type is initial,
                    rationale,
                    list(supporting_evidence_ids),
                    bundle.bundle_id,
                    semantic_judgement_id,
                ),
            )
            await cur.execute(
                """
                update public.product_signals
                set established_flow_type = %s,
                    classification_revision = %s,
                    revision = revision + 1,
                    updated_at = now()
                where id = %s
                """,
                (flow_type.value, classification_revision, bundle.product_signal_id),
            )
        gate_hold_id = await _hold_when_gate_used_older_classification(
            conn,
            bundle=bundle,
            classification_revision=classification_revision,
            raised_by_record_id=record_id,
        )
        await append_fact(
            conn,
            client_id=bundle.client_id,
            event=EventAppend(
                message_id=uuid7(),
                stream=StreamRef(
                    kind=AggregateKind.PRODUCT_SIGNAL, id=bundle.product_signal_id
                ),
                event_type="interview_flow_classification_recorded",
                payload={
                    "classification_revision": classification_revision,
                    "flow_type": flow_type.value,
                    "initial_flow_type": initial.value,
                    "retained": flow_type is initial,
                    "context_bundle_id": str(bundle.bundle_id),
                    "gate_hold_id": str(gate_hold_id) if gate_hold_id else None,
                },
                actor=_AGENT_ACTOR,
                causation_id=None,
                correlation_id=bundle.product_signal_id,
                occurred_at=datetime.now(tz=UTC),
            ),
            audit=AuditIntent(
                audit_id=uuid7(),
                subject=StreamRef(
                    kind=AggregateKind.PRODUCT_SIGNAL, id=bundle.product_signal_id
                ),
                action="record_flow_classification",
                outcome="retained" if flow_type is initial else "corrected",
                reason_code=None,
                actor=_AGENT_ACTOR,
                pinned_configuration_release_id=bundle.dependencies.pinned_configuration_release_id,
                detail={"rationale": rationale},
            ),
            jobs=(),
        )
    _log.info(
        "flow_classification_recorded",
        client_id=str(bundle.client_id),
        product_signal_id=str(bundle.product_signal_id),
        classification_revision=classification_revision,
        flow_type=flow_type.value,
        retained=flow_type is initial,
        gate_hold_id=str(gate_hold_id) if gate_hold_id else None,
    )
    return FlowClassificationRecord(
        record_id=record_id,
        product_signal_id=bundle.product_signal_id,
        classification_revision=classification_revision,
        flow_type=flow_type,
        retained=flow_type is initial,
        gate_hold_id=gate_hold_id,
    )


async def record_acceptance_criteria_agreement(
    database: Database,
    *,
    bundle: AssembledBundle,
    agreed_scope: Mapping[str, JsonValue],
    criteria: Sequence[CriterionDraft],
    supporting_evidence_ids: Sequence[UUID],
) -> AcceptanceCriteriaAgreement:
    """Freeze the agreed scope and its acceptance criteria at a new revision.

    The first agreement is what makes the interview's evidence sufficient for the
    gate: an interviewing Product Signal moves to `gate_pending` in the same
    transaction, with the Implementation Gate Job committed beside it. A later
    agreement records the corrected criteria and moves nothing; a change asked for
    while a new version is validated is `record_change_request`.
    """
    _require_agreement(bundle=bundle, criteria=criteria)
    async with database.transaction() as conn:
        signal = await _lock_signal(conn, bundle.product_signal_id)
        await _require_fresh(conn, bundle=bundle, action="record_acceptance_criteria_agreement")
        agreement = await _freeze_agreement(
            conn,
            bundle=bundle,
            signal=signal,
            agreed_scope=agreed_scope,
            criteria=criteria,
            supporting_evidence_ids=supporting_evidence_ids,
        )
        if as_str(signal, "state") == ProductSignalState.INTERVIEWING.value:
            await advance_product_signal_on_connection(
                conn,
                client_id=bundle.client_id,
                product_signal_id=bundle.product_signal_id,
                trigger="evidence_sufficient",
                facts=ProductSignalFacts(participation_occurred=True, case_file_published=False),
                actor=_AGENT_ACTOR,
                payload={"acceptance_criteria_revision": agreement.acceptance_criteria_revision},
                jobs=(
                    decide_gate_job(
                        product_signal_id=bundle.product_signal_id,
                        cancellation_scope_id=as_uuid(signal, "cancellation_scope_id"),
                        now=datetime.now(tz=UTC),
                    ),
                ),
                reason_code=None,
            )
    return agreement


async def record_change_request(
    database: Database,
    *,
    bundle: AssembledBundle,
    agreed_scope: Mapping[str, JsonValue],
    criteria: Sequence[CriterionDraft],
    supporting_evidence_ids: Sequence[UUID],
    implementation_limit: int,
) -> ChangeRequest:
    """Freeze a change the originating user asked for in the new version, and gate it.

    While a version is being validated, the person may ask for a change to it. The
    whole change, as it now stands, and the checks that now apply are frozen at a
    new criteria revision; the open Validation Attempt is abandoned, because the
    version it validates will be replaced; the session goes back to implementation;
    and the Product Signal returns to `gate_pending` with the gate Job, all in one
    transaction. The case's pinned implementation limit is checked here and again
    when the attempt is created.
    """
    _require_agreement(bundle=bundle, criteria=criteria)
    if bundle.interview_session_id is None:
        raise ValueError("a change request belongs to an Interview Session")
    async with database.transaction() as conn:
        signal = await _lock_signal(conn, bundle.product_signal_id)
        await _require_fresh(conn, bundle=bundle, action="revision_requested")
        state = as_str(signal, "state")
        if state not in _VALIDATING_STATES:
            raise GuardRejectedError(
                machine="product_signal",
                trigger="revision_requested",
                reason=f"a change is asked for while a new version is validated; the case is {state}",
            )
        used = await _implementations_used(conn, product_signal_id=bundle.product_signal_id)
        if used >= implementation_limit:
            raise GuardRejectedError(
                machine="product_signal",
                trigger="revision_requested",
                reason=f"the case already used all {implementation_limit} of its implementations",
            )
        agreement = await _freeze_agreement(
            conn,
            bundle=bundle,
            signal=signal,
            agreed_scope=agreed_scope,
            criteria=criteria,
            supporting_evidence_ids=supporting_evidence_ids,
        )
        open_validation = await open_attempt_id(conn, product_signal_id=bundle.product_signal_id)
        if open_validation is not None:
            await abandon_validation_attempt(
                conn, attempt_id=open_validation, reason="change_requested"
            )
        await return_to_implementation_on_connection(
            conn, interview_session_id=bundle.interview_session_id
        )
        await advance_product_signal_on_connection(
            conn,
            client_id=bundle.client_id,
            product_signal_id=bundle.product_signal_id,
            trigger="revision_requested",
            facts=ProductSignalFacts(participation_occurred=True, case_file_published=False),
            actor=_AGENT_ACTOR,
            payload={
                "acceptance_criteria_revision": agreement.acceptance_criteria_revision,
                "implementations_used": used,
                "implementation_limit": implementation_limit,
            },
            jobs=(
                decide_gate_job(
                    product_signal_id=bundle.product_signal_id,
                    cancellation_scope_id=as_uuid(signal, "cancellation_scope_id"),
                    now=datetime.now(tz=UTC),
                ),
            ),
            reason_code=None,
        )
    _log.info(
        "change_requested",
        client_id=str(bundle.client_id),
        product_signal_id=str(bundle.product_signal_id),
        acceptance_criteria_revision=agreement.acceptance_criteria_revision,
        implementations_used=used,
        implementation_limit=implementation_limit,
    )
    return ChangeRequest(
        agreement=agreement,
        implementation_number=used + 1,
        implementation_limit=implementation_limit,
    )


def _require_agreement(*, bundle: AssembledBundle, criteria: Sequence[CriterionDraft]) -> None:
    if not criteria:
        raise ValueError("an acceptance-criteria agreement needs at least one criterion")
    if bundle.interview_session_id is None:
        raise ValueError("an acceptance-criteria agreement belongs to an Interview Session")


async def _implementations_used(conn: AsyncConnection[DictRow], *, product_signal_id: UUID) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            "select count(*) as used from public.implementation_attempts where product_signal_id = %s",
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"product signal {product_signal_id} has no attempt count")
    return as_int(row, "used")


async def _freeze_agreement(
    conn: AsyncConnection[DictRow],
    *,
    bundle: AssembledBundle,
    signal: DictRow,
    agreed_scope: Mapping[str, JsonValue],
    criteria: Sequence[CriterionDraft],
    supporting_evidence_ids: Sequence[UUID],
) -> AcceptanceCriteriaAgreement:
    """Insert the criteria and the agreement at the next revision, with its fact."""
    criteria_revision = as_int(signal, "acceptance_criteria_revision") + 1
    concern_revision = as_int(signal, "concern_revision") + 1
    criterion_ids: list[UUID] = []
    record_id = uuid7()
    async with conn.cursor() as cur:
        for draft in criteria:
            criterion_id = uuid7()
            criterion_ids.append(criterion_id)
            await cur.execute(
                """
                insert into public.validation_criteria (
                    id, client_id, product_signal_id, criteria_revision, ordinal,
                    statement, observability
                )
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    criterion_id,
                    bundle.client_id,
                    bundle.product_signal_id,
                    criteria_revision,
                    draft.ordinal,
                    draft.statement,
                    draft.observability.value,
                ),
            )
        await cur.execute(
            """
            insert into public.acceptance_criteria_agreements (
                id, client_id, product_signal_id, interview_session_id,
                acceptance_criteria_revision, agreed_scope, criterion_ids,
                supporting_evidence_ids, context_bundle_id
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record_id,
                bundle.client_id,
                bundle.product_signal_id,
                bundle.interview_session_id,
                criteria_revision,
                Jsonb(dict(agreed_scope)),
                criterion_ids,
                list(supporting_evidence_ids),
                bundle.bundle_id,
            ),
        )
        await cur.execute(
            """
            update public.product_signals
            set acceptance_criteria_revision = %s,
                concern_revision = %s,
                revision = revision + 1,
                updated_at = now()
            where id = %s
            """,
            (criteria_revision, concern_revision, bundle.product_signal_id),
        )
    await append_fact(
        conn,
        client_id=bundle.client_id,
        event=EventAppend(
            message_id=uuid7(),
            stream=StreamRef(
                kind=AggregateKind.PRODUCT_SIGNAL, id=bundle.product_signal_id
            ),
            event_type="acceptance_criteria_agreed",
            payload={
                "acceptance_criteria_revision": criteria_revision,
                "concern_revision": concern_revision,
                "criterion_ids": [str(identifier) for identifier in criterion_ids],
                "context_bundle_id": str(bundle.bundle_id),
            },
            actor=_AGENT_ACTOR,
            causation_id=None,
            correlation_id=bundle.product_signal_id,
            occurred_at=datetime.now(tz=UTC),
        ),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=StreamRef(
                kind=AggregateKind.PRODUCT_SIGNAL, id=bundle.product_signal_id
            ),
            action="record_acceptance_criteria_agreement",
            outcome="agreed",
            reason_code=None,
            actor=_AGENT_ACTOR,
            pinned_configuration_release_id=bundle.dependencies.pinned_configuration_release_id,
            detail={"criterion_count": len(criterion_ids)},
        ),
        jobs=(),
    )
    _log.info(
        "acceptance_criteria_agreed",
        client_id=str(bundle.client_id),
        product_signal_id=str(bundle.product_signal_id),
        acceptance_criteria_revision=criteria_revision,
        concern_revision=concern_revision,
        criterion_count=len(criterion_ids),
    )
    return AcceptanceCriteriaAgreement(
        record_id=record_id,
        product_signal_id=bundle.product_signal_id,
        acceptance_criteria_revision=criteria_revision,
        criterion_ids=tuple(criterion_ids),
        concern_revision=concern_revision,
    )




async def _require_fresh(
    conn: AsyncConnection[DictRow], *, bundle: AssembledBundle, action: str
) -> None:
    """Refuse to commit a record assembled against revisions that have moved.

    Called with the Product Signal locked, so nothing it checked can move before
    the record commits.
    """
    freshness = await check_bundle_freshness_on_connection(conn, bundle_id=bundle.bundle_id)
    if freshness.fresh:
        return
    raise StaleContextError(trigger=action, reason=freshness.detail)


async def _lock_signal(
    conn: AsyncConnection[DictRow], product_signal_id: UUID
) -> DictRow:
    """Lock the Product Signal so two agreements cannot take the same revision."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select initial_flow_type, classification_revision,
                   acceptance_criteria_revision, concern_revision, state,
                   cancellation_scope_id
            from public.product_signals
            where id = %s
            for update
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"product signal {product_signal_id} does not exist")
    return row


async def _hold_when_gate_used_older_classification(
    conn: AsyncConnection[DictRow],
    *,
    bundle: AssembledBundle,
    classification_revision: int,
    raised_by_record_id: UUID,
) -> UUID | None:
    """Hold new implementation steps when a gate already used an older revision.

    The historical gate decision stays fixed. The hold stops incompatible steps
    until a fresh gate decision is made against the corrected classification.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select 1 from public.gate_decisions
            where product_signal_id = %s and classification_revision < %s
            limit 1
            """,
            (bundle.product_signal_id, classification_revision),
        )
        used = await cur.fetchone()
        if used is None:
            return None
        await cur.execute(
            """
            select id from public.evidence_records
            where product_signal_id = %s
            order by recorded_at desc
            limit 1
            """,
            (bundle.product_signal_id,),
        )
        anchor = await cur.fetchone()
        if anchor is None:
            raise GuardRejectedError(
                machine="product_signal",
                trigger="record_flow_classification",
                reason=(
                    "a reclassification after a gate decision must cite the evidence "
                    "that caused it, and this Product Signal has no evidence record"
                ),
            )
        hold_id = uuid7()
        await cur.execute(
            """
            insert into public.dependency_holds (
                id, client_id, product_signal_id, interview_session_id, hold_kind,
                reason, raised_by_evidence_id, affected_evidence_id,
                semantic_judgement_id, blocks_new_implementation_steps
            )
            values (%s, %s, %s, %s, 'accepted_correction', %s, %s, null, null, true)
            """,
            (
                hold_id,
                bundle.client_id,
                bundle.product_signal_id,
                bundle.interview_session_id,
                f"{_CLASSIFICATION_SUPERSEDED}:{raised_by_record_id}",
                anchor["id"],
            ),
        )
    return hold_id
