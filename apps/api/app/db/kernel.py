"""The transactional transition kernel.

One Postgres transaction locks the affected aggregate rows in deterministic
identifier order, checks the expected revision and the legal predecessor state,
writes the projection mutation, appends the Event at the next monotonic sequence
for its primary lifecycle stream, writes the outbox work, writes the audit
record, and commits once. External work begins only after that commit.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.sql import SQL, Identifier, Placeholder
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import AggregateKind

from app.db.pool import Database
from app.envelope import AuditIntent, CommandIntent, EventAppend, JobIntent
from app.errors import AggregateNotFoundError, IllegalTransitionError, RevisionConflictError
from app.logging import get_logger

__all__ = [
    "AggregateRef",
    "ColumnValue",
    "TransitionOutcome",
    "TransitionRequest",
    "append_fact",
    "apply_transition",
    "apply_transition_on_connection",
    "table_for",
]

_log = get_logger(__name__)

type ColumnValue = str | int | float | bool | None | UUID | datetime | Jsonb

_TABLE_BY_KIND: Mapping[AggregateKind, str] = {
    AggregateKind.INTAKE_ITEM: "intake_items",
    AggregateKind.PRODUCT_SIGNAL: "product_signals",
    AggregateKind.OUTREACH_ATTEMPT: "outreach_attempts",
    AggregateKind.INVITATION: "invitations",
    AggregateKind.INTERVIEW_SESSION: "interview_sessions",
    AggregateKind.VOICE_CONNECTION: "voice_connections",
    AggregateKind.PREVIEW_RUNTIME_ALLOCATION: "preview_runtime_allocations",
    AggregateKind.ENVIRONMENT_LEASE: "environment_leases",
    AggregateKind.IMPLEMENTATION_ATTEMPT: "implementation_attempts",
    AggregateKind.PREVIEW_VERSION: "preview_versions",
    AggregateKind.VALIDATION_ATTEMPT: "validation_attempts",
    AggregateKind.CASE_FILE: "case_files",
    AggregateKind.DELIVERY: "deliveries",
    AggregateKind.OUTREACH_OFFER: "outreach_offers",
    AggregateKind.JOB: "jobs",
    AggregateKind.JOB_RUN: "job_runs",
    AggregateKind.COMMAND: "commands",
}


def table_for(kind: AggregateKind) -> str:
    """Return the projection table that owns one aggregate kind."""
    return _TABLE_BY_KIND[kind]


@dataclass(frozen=True, slots=True)
class AggregateRef:
    """One aggregate row to lock. ``expected_revision`` of ``None`` locks only."""

    kind: AggregateKind
    row_id: UUID
    expected_revision: int | None


@dataclass(frozen=True, slots=True)
class TransitionRequest:
    """Everything one lifecycle transition commits, in one transaction."""

    client_id: UUID
    aggregate: AggregateRef
    machine: str
    machine_version: int
    state_column: str
    from_states: frozenset[str]
    to_state: str
    mutation: Mapping[str, ColumnValue]
    event: EventAppend
    jobs: Sequence[JobIntent]
    commands: Sequence[CommandIntent]
    audit: AuditIntent
    additional_locks: Sequence[AggregateRef]


@dataclass(frozen=True, slots=True)
class TransitionOutcome:
    """What the committed transition produced."""

    aggregate_id: UUID
    from_state: str
    to_state: str
    revision: int
    event_id: UUID
    sequence: int
    job_ids: tuple[UUID, ...]
    command_ids: tuple[UUID, ...]


def _ordered_locks(request: TransitionRequest) -> tuple[AggregateRef, ...]:
    """Order every lock by table then identifier so transactions cannot deadlock."""
    refs = (request.aggregate, *request.additional_locks)
    return tuple(sorted(refs, key=lambda ref: (table_for(ref.kind), ref.row_id)))


async def _lock_row(
    conn: AsyncConnection[DictRow], ref: AggregateRef, state_column: str | None
) -> DictRow:
    table = table_for(ref.kind)
    columns = [Identifier("revision")]
    if state_column is not None:
        columns.append(Identifier(state_column))
    statement = SQL("select {columns} from {table} where id = {row_id} for update").format(
        columns=SQL(", ").join(columns),
        table=Identifier("public", table),
        row_id=Placeholder(),
    )
    async with conn.cursor() as cur:
        await cur.execute(statement, (ref.row_id,))
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table=table, row_id=ref.row_id)
    actual = int(row["revision"])
    if ref.expected_revision is not None and actual != ref.expected_revision:
        raise RevisionConflictError(
            table=table, row_id=ref.row_id, expected=ref.expected_revision, actual=actual
        )
    return row


async def _write_projection(
    conn: AsyncConnection[DictRow], request: TransitionRequest
) -> int:
    assignments = [
        SQL("{column} = {value}").format(
            column=Identifier(request.state_column), value=Placeholder()
        ),
        SQL("revision = revision + 1"),
        SQL("updated_at = now()"),
    ]
    params: list[ColumnValue] = [request.to_state]
    for column, value in request.mutation.items():
        assignments.append(
            SQL("{column} = {value}").format(column=Identifier(column), value=Placeholder())
        )
        params.append(value)
    statement = SQL(
        "update {table} set {assignments} where id = {row_id} returning revision"
    ).format(
        table=Identifier("public", table_for(request.aggregate.kind)),
        assignments=SQL(", ").join(assignments),
        row_id=Placeholder(),
    )
    params.append(request.aggregate.row_id)
    async with conn.cursor() as cur:
        await cur.execute(statement, tuple(params))
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table=table_for(request.aggregate.kind), row_id=request.aggregate.row_id
        )
    return int(row["revision"])


async def _append_event(
    conn: AsyncConnection[DictRow], client_id: UUID, event: EventAppend
) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select coalesce(max(sequence), 0) + 1 as next_sequence
            from public.events
            where stream_kind = %s and stream_id = %s
            """,
            (event.stream.kind.value, event.stream.id),
        )
        row = await cur.fetchone()
        sequence = int(row["next_sequence"]) if row is not None else 1
        await cur.execute(
            """
            insert into public.events (
                id, client_id, stream_kind, stream_id, sequence, event_type, payload,
                actor_kind, actor_ref, causation_id, correlation_id, occurred_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event.message_id,
                client_id,
                event.stream.kind.value,
                event.stream.id,
                sequence,
                event.event_type,
                Jsonb(event.payload),
                event.actor.kind.value,
                event.actor.ref,
                event.causation_id,
                event.correlation_id,
                event.occurred_at,
            ),
        )
    return sequence


async def _write_jobs(
    conn: AsyncConnection[DictRow], client_id: UUID, jobs: Sequence[JobIntent]
) -> tuple[UUID, ...]:
    for job in jobs:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.jobs (
                    id, client_id, job_type, state, input, deadline_at, timeout_seconds,
                    max_runs, resource_budget, retryable_categories, backoff_base_seconds,
                    backoff_max_seconds, concurrency_key, cancellation_scope_id, not_before
                )
                values (%s, %s, %s, 'ready', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    job.job_id,
                    client_id,
                    job.job_type,
                    Jsonb(job.input),
                    job.deadline_at,
                    job.timeout_seconds,
                    job.max_runs,
                    Jsonb(job.resource_budget),
                    list(job.retryable_categories),
                    job.backoff_base_seconds,
                    job.backoff_max_seconds,
                    job.concurrency_key,
                    job.cancellation_scope_id,
                    job.not_before,
                ),
            )
            await cur.execute(
                """
                insert into public.outbox (id, client_id, state, job_id, available_at)
                values (%s, %s, 'pending', %s, %s)
                """,
                (_outbox_id(job.job_id), client_id, job.job_id, job.not_before),
            )
    return tuple(job.job_id for job in jobs)


async def _write_commands(
    conn: AsyncConnection[DictRow], client_id: UUID, commands: Sequence[CommandIntent]
) -> tuple[UUID, ...]:
    for command in commands:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.commands (
                    id, client_id, command_type, state, target_kind, target_id,
                    payload, declared_revisions, cancellation_scope_id, queued_at
                )
                values (%s, %s, %s, 'queued', %s, %s, %s, %s, %s, now())
                """,
                (
                    command.command_id,
                    client_id,
                    command.command_type,
                    command.target.kind.value,
                    command.target.id,
                    Jsonb(command.payload),
                    Jsonb(command.declared_revisions),
                    command.cancellation_scope_id,
                ),
            )
            await cur.execute(
                """
                insert into public.outbox (id, client_id, state, command_id, available_at)
                values (%s, %s, 'pending', %s, now())
                """,
                (_outbox_id(command.command_id), client_id, command.command_id),
            )
    return tuple(command.command_id for command in commands)


def _outbox_id(intent_id: UUID) -> UUID:
    """Derive the outbox row identifier from its single intent.

    Deriving rather than generating makes a replayed write collide on the primary
    key instead of queueing the same effect twice.
    """
    return intent_id


async def _write_audit(
    conn: AsyncConnection[DictRow], client_id: UUID, audit: AuditIntent, revision: int
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.audit_records (
                id, client_id, subject_kind, subject_id, action, outcome, reason_code,
                actor_kind, actor_ref, lifecycle_revision,
                pinned_configuration_release_id, detail
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                audit.audit_id,
                client_id,
                audit.subject.kind.value,
                audit.subject.id,
                audit.action,
                audit.outcome,
                audit.reason_code,
                audit.actor.kind.value,
                audit.actor.ref,
                revision,
                audit.pinned_configuration_release_id,
                Jsonb(audit.detail),
            ),
        )


async def _wake_outbox(conn: AsyncConnection[DictRow], expected: int) -> None:
    """Move the freshly written outbox rows onto the pgmq wake-up queue."""
    if expected == 0:
        return
    async with conn.cursor() as cur:
        await cur.execute("select utrace.enqueue_ready_outbox(%s) as moved", (expected,))


async def append_fact(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    event: EventAppend,
    audit: AuditIntent,
    jobs: Sequence[JobIntent],
) -> int:
    """Append an accepted fact that changes no projection state.

    Some facts withdraw authority without advancing a lifecycle: a client Stop
    that must still wait for its Case File is the motivating case. The fact still
    gets its sequenced Event, its audit record, and any follow-up Job.
    """
    sequence = await _append_event(conn, client_id, event)
    await _write_jobs(conn, client_id, jobs)
    await _write_audit(conn, client_id, audit, sequence)
    await _wake_outbox(conn, len(jobs))
    return sequence


async def apply_transition_on_connection(
    conn: AsyncConnection[DictRow], request: TransitionRequest
) -> TransitionOutcome:
    """Apply one transition inside a caller-owned transaction.

    Use this when a controller must write further rows in the same transaction.
    """
    primary_id = request.aggregate.row_id
    from_state = ""
    for ref in _ordered_locks(request):
        state_column = request.state_column if ref.row_id == primary_id else None
        row = await _lock_row(conn, ref, state_column)
        if ref.row_id == primary_id:
            from_state = str(row[request.state_column])

    if from_state not in request.from_states:
        raise IllegalTransitionError(
            machine=request.machine,
            machine_version=request.machine_version,
            state=from_state,
            trigger=request.event.event_type,
        )

    revision = await _write_projection(conn, request)
    sequence = await _append_event(conn, request.client_id, request.event)
    job_ids = await _write_jobs(conn, request.client_id, request.jobs)
    command_ids = await _write_commands(conn, request.client_id, request.commands)
    await _write_audit(conn, request.client_id, request.audit, revision)
    await _wake_outbox(conn, len(job_ids) + len(command_ids))

    _log.info(
        "transition_committed",
        machine=request.machine,
        machine_version=request.machine_version,
        client_id=str(request.client_id),
        aggregate_kind=request.aggregate.kind.value,
        aggregate_id=str(primary_id),
        from_state=from_state,
        to_state=request.to_state,
        revision=revision,
        sequence=sequence,
        job_count=len(job_ids),
        command_count=len(command_ids),
    )
    return TransitionOutcome(
        aggregate_id=primary_id,
        from_state=from_state,
        to_state=request.to_state,
        revision=revision,
        event_id=request.event.message_id,
        sequence=sequence,
        job_ids=job_ids,
        command_ids=command_ids,
    )


async def apply_transition(
    database: Database, request: TransitionRequest
) -> TransitionOutcome:
    """Apply one transition in its own transaction and commit once."""
    async with database.transaction() as conn:
        return await apply_transition_on_connection(conn, request)
