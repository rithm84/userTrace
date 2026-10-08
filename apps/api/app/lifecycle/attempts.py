"""Implementation Attempt creation and Environment Lease grants.

A Product Signal may use several Implementation Attempts, one per approving gate
decision: the first, then one for each change the originating user asks for while
validating a new version, up to the implementation limit its configuration release
pins. Creation locks the Product Signal, rechecks lifecycle, classification, gate
and policy revisions and the limit, then atomically commits the attempt row with
the Product Signal transition and the dispatch intent. A replayed request returns
the attempt its gate decision already created, and the database refuses a second
attempt for one decision or a second attempt in flight for one case.

Contract: docs/arch-components/implementation.md#attempt-protocol
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    AggregateKind,
    EnvironmentLeaseState,
    GateDecisionKind,
    ImplementationAttemptState,
    ProductSignalState,
)

from app.db.kernel import TransitionOutcome, apply_transition_on_connection
from app.db.pool import Database
from app.db.rows import as_datetime, as_int, as_str, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, JobIntent, JsonValue, StreamRef
from app.errors import AggregateNotFoundError, GuardRejectedError, RevisionConflictError
from app.ids import uuid7
from app.interview.voice.session_changes import wake_case_sidebands
from app.lifecycle.machine import plan_transition
from app.lifecycle.product_signal import PRODUCT_SIGNAL_MACHINE, ProductSignalFacts
from app.logging import get_logger

__all__ = [
    "AttemptCreation",
    "AttemptRequest",
    "LeaseGrant",
    "create_implementation_attempt",
    "grant_environment_lease",
    "release_environment_lease",
]

_log = get_logger(__name__)



@dataclass(frozen=True, slots=True)
class AttemptRequest:
    """Everything the pre-dispatch recheck needs, all of it durable."""

    client_id: UUID
    product_signal_id: UUID
    gate_decision_id: UUID
    repository_ref: str
    base_revision: str
    expected_classification_revision: int
    expected_configuration_release_id: UUID
    implementation_limit: int
    """The pinned release's limit on the case's Implementation Attempts."""
    consumed_budgets: Mapping[str, JsonValue]
    job: JobIntent
    actor: Actor


@dataclass(frozen=True, slots=True)
class AttemptCreation:
    """The committed attempt, or the one a replayed request already created."""

    attempt_id: UUID
    replayed: bool
    transition: TransitionOutcome | None


@dataclass(frozen=True, slots=True)
class LeaseGrant:
    """One fenced Environment Lease."""

    lease_id: UUID
    fencing_token: int
    expires_at: datetime


async def _existing_attempt(
    conn: AsyncConnection[DictRow], gate_decision_id: UUID
) -> DictRow | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.implementation_attempts
            where gate_decision_id = %s
            for update
            """,
            (gate_decision_id,),
        )
        return await cur.fetchone()


async def _attempts_used(conn: AsyncConnection[DictRow], product_signal_id: UUID) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            "select count(*) as used from public.implementation_attempts where product_signal_id = %s",
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="implementation_attempts", row_id=product_signal_id)
    return as_int(row, "used")


async def _load_signal(conn: AsyncConnection[DictRow], signal_id: UUID) -> DictRow:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, state, revision,
                   classification_revision, pinned_configuration_release_id,
                   cancellation_scope_id
            from public.product_signals
            where id = %s
            for update
            """,
            (signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="product_signals", row_id=signal_id)
    return row


async def _check_gate_decision(
    conn: AsyncConnection[DictRow], request: AttemptRequest
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select decision, classification_revision, pinned_configuration_release_id
            from public.gate_decisions
            where id = %s and product_signal_id = %s
            """,
            (request.gate_decision_id, request.product_signal_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="gate_decisions", row_id=request.gate_decision_id
        )
    decision = as_str(row, "decision")
    if decision != GateDecisionKind.APPROVED_LIVE.value:
        raise GuardRejectedError(
            machine="implementation_attempt",
            trigger="create",
            reason=f"gate decision '{decision}' authorizes no implementation",
        )
    if as_int(row, "classification_revision") != request.expected_classification_revision:
        raise GuardRejectedError(
            machine="implementation_attempt",
            trigger="create",
            reason=(
                "the gate decision pins classification revision "
                f"{as_int(row, 'classification_revision')}, "
                f"the request expects {request.expected_classification_revision}"
            ),
        )
    if as_uuid(row, "pinned_configuration_release_id") != request.expected_configuration_release_id:
        raise GuardRejectedError(
            machine="implementation_attempt",
            trigger="create",
            reason="the gate decision pins a different configuration release",
        )


async def create_implementation_attempt(
    database: Database, request: AttemptRequest
) -> AttemptCreation:
    """Create the attempt one gate decision authorized, or return it on replay."""
    async with database.transaction() as conn:
        signal = await _load_signal(conn, request.product_signal_id)

        replay = await _existing_attempt(conn, request.gate_decision_id)
        if replay is not None:
            _log.info(
                "implementation_attempt_replayed",
                client_id=str(request.client_id),
                product_signal_id=str(request.product_signal_id),
                attempt_id=str(as_uuid(replay, "id")),
            )
            return AttemptCreation(
                attempt_id=as_uuid(replay, "id"), replayed=True, transition=None
            )

        state = as_str(signal, "state")
        if state != ProductSignalState.GATE_PENDING.value:
            raise GuardRejectedError(
                machine="product_signal",
                trigger="authorize_implementation",
                reason=f"attempt creation requires gate_pending, found '{state}'",
            )
        if as_int(signal, "classification_revision") != request.expected_classification_revision:
            raise RevisionConflictError(
                table="product_signals",
                row_id=request.product_signal_id,
                expected=request.expected_classification_revision,
                actual=as_int(signal, "classification_revision"),
            )
        if (
            as_uuid(signal, "pinned_configuration_release_id")
            != request.expected_configuration_release_id
        ):
            raise GuardRejectedError(
                machine="product_signal",
                trigger="authorize_implementation",
                reason="the Product Signal pins a different configuration release",
            )

        await _check_gate_decision(conn, request)
        used = await _attempts_used(conn, request.product_signal_id)
        if used >= request.implementation_limit:
            raise GuardRejectedError(
                machine="implementation_attempt",
                trigger="create",
                reason=(
                    f"the Product Signal already used all {request.implementation_limit} "
                    "of its Implementation Attempts"
                ),
            )

        attempt_id = uuid7()
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.implementation_attempts (
                    id, client_id, product_signal_id, gate_decision_id, state,
                    repository_ref, base_revision, consumed_budgets
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    attempt_id,
                    request.client_id,
                    request.product_signal_id,
                    request.gate_decision_id,
                    ImplementationAttemptState.AUTHORIZED.value,
                    request.repository_ref,
                    request.base_revision,
                    Jsonb(request.consumed_budgets),
                ),
            )

        facts = ProductSignalFacts(participation_occurred=True, case_file_published=False)
        now = datetime.now(tz=UTC)
        stream = StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=request.product_signal_id)
        transition_request = plan_transition(
            PRODUCT_SIGNAL_MACHINE,
            facts,
            trigger="authorize_implementation",
            current_state=state,
            client_id=request.client_id,
            aggregate_kind=AggregateKind.PRODUCT_SIGNAL,
            aggregate_id=request.product_signal_id,
            expected_revision=as_int(signal, "revision"),
            state_column="state",
            mutation={},
            event=EventAppend(
                message_id=uuid7(),
                stream=stream,
                event_type="implementation_attempt_authorized",
                payload={
                    "implementation_attempt_id": str(attempt_id),
                    "gate_decision_id": str(request.gate_decision_id),
                },
                actor=request.actor,
                causation_id=request.gate_decision_id,
                correlation_id=request.product_signal_id,
                occurred_at=now,
            ),
            jobs=(request.job,),
            commands=(),
            audit=AuditIntent(
                audit_id=uuid7(),
                subject=StreamRef(
                    kind=AggregateKind.IMPLEMENTATION_ATTEMPT, id=attempt_id
                ),
                action="create_implementation_attempt",
                outcome="authorized",
                reason_code=None,
                actor=request.actor,
                pinned_configuration_release_id=request.expected_configuration_release_id,
                detail={
                    "repository_ref": request.repository_ref,
                    "base_revision": request.base_revision,
                },
            ),
            additional_locks=(),
        )
        outcome = await apply_transition_on_connection(conn, transition_request)
        # The case's live sideband says it is building only once it is.
        await wake_case_sidebands(conn, product_signal_id=request.product_signal_id)

    return AttemptCreation(attempt_id=attempt_id, replayed=False, transition=outcome)


async def grant_environment_lease(
    database: Database,
    *,
    client_id: UUID,
    implementation_attempt_id: UUID,
    workspace_ref: str | None,
    duration_seconds: int,
) -> LeaseGrant:
    """Grant one active fenced lease, fencing any lease the attempt already holds."""
    lease_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "select id from public.implementation_attempts where id = %s for update",
            (implementation_attempt_id,),
        )
        if await cur.fetchone() is None:
            raise AggregateNotFoundError(
                table="implementation_attempts", row_id=implementation_attempt_id
            )
        await cur.execute(
            """
            update public.environment_leases
            set state = %s, revision = revision + 1, updated_at = now()
            where implementation_attempt_id = %s and state = %s
            """,
            (
                EnvironmentLeaseState.FENCED.value,
                implementation_attempt_id,
                EnvironmentLeaseState.ACTIVE.value,
            ),
        )
        await cur.execute(
            """
            insert into public.environment_leases (
                id, client_id, implementation_attempt_id, state, fencing_token,
                workspace_ref, expires_at
            )
            values (
                %s, %s, %s, %s, utrace.next_fencing_token(), %s,
                now() + make_interval(secs => %s)
            )
            returning fencing_token, expires_at
            """,
            (
                lease_id,
                client_id,
                implementation_attempt_id,
                EnvironmentLeaseState.ACTIVE.value,
                workspace_ref,
                duration_seconds,
            ),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="environment_leases", row_id=lease_id)
    grant = LeaseGrant(
        lease_id=lease_id,
        fencing_token=as_int(row, "fencing_token"),
        expires_at=as_datetime(row, "expires_at"),
    )
    _log.info(
        "environment_lease_granted",
        client_id=str(client_id),
        implementation_attempt_id=str(implementation_attempt_id),
        lease_id=str(lease_id),
        fencing_token=grant.fencing_token,
    )
    return grant


async def release_environment_lease(
    database: Database, *, lease_id: UUID, fencing_token: int
) -> None:
    """Release a run's own lease once its workspace has been destroyed.

    The released lease is the durable record that the workspace is gone, which the
    Case File reports as the attempt's cleanup state. Only the lease and fencing
    token the run held are released: a run that outlived its lease cannot release
    the one a newer run of the same attempt now holds.
    """
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.environment_leases
            set state = %s, released_at = now(), revision = revision + 1, updated_at = now()
            where id = %s and fencing_token = %s and state = %s
            """,
            (
                EnvironmentLeaseState.RELEASED.value,
                lease_id,
                fencing_token,
                EnvironmentLeaseState.ACTIVE.value,
            ),
        )
        released = cur.rowcount
    _log.info(
        "environment_lease_released",
        lease_id=str(lease_id),
        fencing_token=fencing_token,
        released=released,
    )
