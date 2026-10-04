"""Shared helpers for the database-backed tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from utrace_contracts import payload_digest
from utrace_contracts.base import JsonObject
from utrace_contracts.domain.lifecycle import ActorKind, AggregateKind

from app.db.pool import Database
from app.db.rows import as_int
from app.envelope import Actor, AuditIntent, EventAppend, JobIntent, JsonValue, StreamRef
from app.ids import uuid7
from app.interview.readiness import (
    ExtensionReadiness,
    record_conversational_facts,
    record_observation_facts,
)

__all__ = [
    "SYSTEM_ACTOR",
    "audit_actions",
    "count_rows",
    "event_sequences",
    "make_audit",
    "make_event",
    "make_job",
    "policy_digest",
    "read_state",
    "record_session_readiness",
]

SYSTEM_ACTOR = Actor(kind=ActorKind.SYSTEM, ref="integration-test")


def policy_digest(policy: Mapping[str, object]) -> str:
    """Return the release digest for one fixture's configuration policy document.

    `app.clients.configuration.release_digest` takes the whole typed policy. A
    fixture stores only the fields its test reads, so it digests the canonical
    bytes of the document it actually inserts, which is what the column's check
    constraint and `ActiveRelease.digest` require.

    The cast states what the caller already relies on: the same mapping is handed
    to `Jsonb`, so it is JSON by construction, and `payload_digest` raises if it
    is not.
    """
    return f"sha256:{payload_digest(cast(JsonObject, policy))}"


async def record_session_readiness(
    database: Database,
    *,
    interview_session_id: UUID,
    conversational: bool,
    observation: bool,
) -> None:
    """Record one session's readiness through the evaluator that owns it.

    `public.session_readiness` is the readiness record, and
    `app.interview.readiness` derives both boundaries from per-component facts. A
    fixture therefore states the boundaries it wants and lets the evaluator keep
    the components, blockers, and revision consistent, exactly as a real run
    does. The session row must already be committed.
    """
    if observation and not conversational:
        raise AssertionError(
            "observation readiness requires conversational readiness; "
            "a fixture cannot record one without the other"
        )
    await record_conversational_facts(
        database,
        interview_session_id=interview_session_id,
        participant_authenticated=conversational,
        voice_transport_ready=conversational,
        sideband_attached=conversational,
        backend_context_current=conversational,
    )
    if observation:
        await record_observation_facts(
            database,
            interview_session_id=interview_session_id,
            preview_allocation_ready=True,
            extension_readiness=ExtensionReadiness.READY,
            browser_sdk_ready=True,
            server_sdk_ready=True,
            trace_correlation_ready=True,
        )


def make_event(
    *,
    stream_kind: AggregateKind,
    stream_id: UUID,
    event_type: str,
    payload: Mapping[str, JsonValue],
) -> EventAppend:
    """Build one Event append for a test transition."""
    return EventAppend(
        message_id=uuid7(),
        stream=StreamRef(kind=stream_kind, id=stream_id),
        event_type=event_type,
        payload=payload,
        actor=SYSTEM_ACTOR,
        causation_id=None,
        correlation_id=None,
        occurred_at=datetime.now(tz=UTC),
    )


def make_audit(
    *, subject_kind: AggregateKind, subject_id: UUID, action: str, outcome: str
) -> AuditIntent:
    """Build one audit record for a test transition."""
    return AuditIntent(
        audit_id=uuid7(),
        subject=StreamRef(kind=subject_kind, id=subject_id),
        action=action,
        outcome=outcome,
        reason_code=None,
        actor=SYSTEM_ACTOR,
        pinned_configuration_release_id=None,
        detail={},
    )


_HOST_CLOCK_TOLERANCE = timedelta(seconds=1)


def make_job(
    *,
    job_type: str,
    input: Mapping[str, JsonValue],
    concurrency_key: str | None,
    cancellation_scope_id: UUID | None,
    max_runs: int,
    retryable_categories: Sequence[str],
    not_before_seconds: int,
) -> JobIntent:
    """Build one durable Job intent with an explicit frozen budget.

    `not_before_seconds=0` means due now. The database compares `not_before` with
    its own clock, and a development machine's clock can run tens of
    milliseconds ahead of it, so "now" is taken a second early to stay due on
    either clock.
    """
    now = datetime.now(tz=UTC) - _HOST_CLOCK_TOLERANCE
    return JobIntent(
        job_id=uuid7(),
        job_type=job_type,
        input=input,
        deadline_at=now + timedelta(hours=1),
        timeout_seconds=60,
        max_runs=max_runs,
        resource_budget={"kind": "test"},
        retryable_categories=retryable_categories,
        backoff_base_seconds=1,
        backoff_max_seconds=4,
        concurrency_key=concurrency_key,
        cancellation_scope_id=cancellation_scope_id,
        not_before=now + timedelta(seconds=not_before_seconds),
    )


async def read_state(
    database: Database, *, table: str, row_id: UUID, column: str
) -> tuple[str, int]:
    """Return one row's lifecycle column and revision."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            f"select {column} as lifecycle, revision from public.{table} where id = %s",
            (row_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AssertionError(f"{table}.{row_id} does not exist")
    return str(row["lifecycle"]), as_int(row, "revision")


async def event_sequences(
    database: Database, *, stream_kind: AggregateKind, stream_id: UUID
) -> list[int]:
    """Return the sequence numbers appended to one primary lifecycle stream."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select sequence from public.events "
            "where stream_kind = %s and stream_id = %s order by sequence",
            (stream_kind.value, stream_id),
        )
        rows = await cur.fetchall()
    return [as_int(row, "sequence") for row in rows]


async def audit_actions(
    database: Database, *, subject_id: UUID
) -> list[tuple[str, str]]:
    """Return the audit actions and outcomes recorded against one subject."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select action, outcome from public.audit_records "
            "where subject_id = %s order by recorded_at",
            (subject_id,),
        )
        rows = await cur.fetchall()
    return [(str(row["action"]), str(row["outcome"])) for row in rows]


async def count_rows(database: Database, *, table: str, where: str, params: Sequence[object]) -> int:
    """Count rows in one owned table."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            f"select count(*) as total from public.{table} where {where}",
            tuple(params),
        )
        row = await cur.fetchone()
    return as_int(row, "total") if row is not None else 0
