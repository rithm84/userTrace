"""The user-facing implementation progress projection.

The session tab reads four concise states from a durable column, not from a
model claim. Each move writes the attempt's projection, the live Interview
Session's projection, its sequenced Event, and its audit record in one
transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from utrace_contracts.domain.lifecycle import (
    AggregateKind,
    ImplementationProgress,
    InterviewSessionState,
    ProductSignalState,
)

from app.db.pool import Database
from app.db.rows import as_int, as_str
from app.implementation.progress import record_progress
from tests.factories import CaseFixture, create_case, create_interview_session
from tests.gate.factories import create_gated_case, gate_policy
from tests.helpers import audit_actions, event_sequences
from tests.implementation.factories import (
    authorize_attempt,
    saved_views_scope,
    shopalytics_checks,
)
from tests.supabase_auth import SignedInUser

SNAPSHOT = "utrace-implementation-node24-2026-09-20"

LIVE_SEQUENCE = (
    ImplementationProgress.REPRODUCTION_CAPTURED,
    ImplementationProgress.MAKING_CHANGES,
    ImplementationProgress.PREPARING_PREVIEW,
    ImplementationProgress.READY_FOR_VALIDATION,
)


async def _session_progress(database: Database, session_id: UUID) -> str:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select implementation_progress from public.interview_sessions where id = %s",
            (session_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    return as_str(row, "implementation_progress")


async def _attempt_progress(database: Database, attempt_id: UUID) -> str:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select progress from public.implementation_attempts where id = %s", (attempt_id,)
        )
        row = await cur.fetchone()
    assert row is not None
    return as_str(row, "progress")


async def test_live_progress_reaches_the_session_tab_projection(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """During live implementation the session's own projection advances with the run."""
    case: CaseFixture = await create_case(
        database,
        name="progress-live",
        state=ProductSignalState.IMPLEMENTING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    attempt_id = await _attempt_for_case(database, case)

    assert await _session_progress(database, session_id) == (
        ImplementationProgress.NOT_STARTED.value
    )
    for progress in LIVE_SEQUENCE:
        await record_progress(
            database,
            client_id=case.client_id,
            implementation_attempt_id=attempt_id,
            interview_session_id=session_id,
            progress=progress,
            occurred_at=datetime.now(tz=UTC),
        )
        assert await _session_progress(database, session_id) == progress.value
        assert await _attempt_progress(database, attempt_id) == progress.value

    sequences = await event_sequences(
        database, stream_kind=AggregateKind.IMPLEMENTATION_ATTEMPT, stream_id=attempt_id
    )
    assert sequences == [1, 2, 3, 4]
    actions = await audit_actions(database, subject_id=attempt_id)
    assert [outcome for _, outcome in actions] == [item.value for item in LIVE_SEQUENCE]
    assert {action for action, _ in actions} == {"record_implementation_progress"}


async def test_async_progress_is_durable_without_a_live_session(
    database: Database, auth_users: dict[str, SignedInUser], workspace_snapshot_id: str
) -> None:
    """Async work between sessions still exposes equivalent durable status."""
    gated = await create_gated_case(
        database,
        name="progress-async",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        policy=gate_policy(),
        state=ProductSignalState.IMPLEMENTING,
    )
    attempt = await authorize_attempt(
        database,
        case=gated,
        scope=saved_views_scope(checks=shopalytics_checks()),
        workspace_snapshot_id=workspace_snapshot_id,
    )
    await record_progress(
        database,
        client_id=gated.client_id,
        implementation_attempt_id=attempt.implementation_attempt_id,
        interview_session_id=None,
        progress=ImplementationProgress.MAKING_CHANGES,
        occurred_at=datetime.now(tz=UTC),
    )
    assert await _attempt_progress(database, attempt.implementation_attempt_id) == (
        ImplementationProgress.MAKING_CHANGES.value
    )
    sequences = await event_sequences(
        database,
        stream_kind=AggregateKind.IMPLEMENTATION_ATTEMPT,
        stream_id=attempt.implementation_attempt_id,
    )
    assert sequences == [1]


async def _attempt_for_case(database: Database, case: CaseFixture) -> UUID:
    """Insert the gate decision, attempt, and lease for a `tests.factories` case."""
    from tests.gate.factories import GatedCase

    gated = GatedCase(
        client_id=case.client_id,
        configuration_release_id=case.configuration_release_id,
        client_user_id=case.client_user_id,
        customer_account_id=case.customer_account_id,
        product_signal_id=case.product_signal_id,
        cancellation_scope_id=case.cancellation_scope_id,
    )
    attempt = await authorize_attempt(
        database,
        case=gated,
        scope=saved_views_scope(checks=shopalytics_checks()),
        workspace_snapshot_id=SNAPSHOT,
    )
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select revision from public.implementation_attempts where id = %s",
            (attempt.implementation_attempt_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert as_int(row, "revision") == 0
    return attempt.implementation_attempt_id
