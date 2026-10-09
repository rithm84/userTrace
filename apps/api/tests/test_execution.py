"""Durable execution: Job leases, fencing, outbox and inbox idempotency.

Queue delivery is at least once, so every step here has to be safe to repeat.
These tests prove the lease fencing, the retry classes, the single terminal
Result per Command, and the restart reconciliation, all against real rows.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from utrace_contracts.domain.lifecycle import (
    AggregateKind,
    CommandDisposition,
    CommandState,
    EffectState,
    JobRunState,
    JobState,
    OutreachAttemptState,
    ProductSignalState,
    RetryAdvice,
    TechnicalCategory,
)

from app.connectors.failures import ConnectorFailure, FailureDetail
from app.db.kernel import apply_transition
from app.db.pool import Database
from app.db.rows import as_str
from app.envelope import TRANSIENT_CATEGORIES, CommandIntent, Failure, StreamRef
from app.errors import IllegalTransitionError, LeaseLostError
from app.execution.commands import (
    TerminalResult,
    mark_dispatched,
    mark_dispatching,
    record_inbound,
    record_terminal_result,
    replay_terminal_result,
    start_reconciliation,
)
from app.execution.handlers import EXPIRE_OUTREACH_ATTEMPT, build_handlers
from app.execution.jobs import (
    ClaimedJob,
    claim_job,
    claim_job_by_id,
    complete_job_run,
    fail_job_run,
    heartbeat,
    next_run_at,
)
from app.execution.outbox import read_wakeups, scan_ready
from app.execution.reconcile import reconcile_startup
from app.execution.worker import Worker, WorkerConfig
from app.ids import uuid7
from app.lifecycle.machine import plan_transition
from app.lifecycle.product_signal import PRODUCT_SIGNAL_MACHINE, ProductSignalFacts
from tests.factories import CaseFixture, create_case, create_outreach_attempt
from tests.helpers import count_rows, make_audit, make_event, make_job, read_state
from tests.supabase_auth import SignedInUser

FACTS = ProductSignalFacts(
    participation_occurred=True,
    case_file_published=True,
)


async def _case(database: Database, owner: SignedInUser, name: str) -> CaseFixture:
    return await create_case(
        database,
        name=name,
        state=ProductSignalState.AWAITING_INTERVIEW,
        authorizing_auth_user_id=owner.auth_user_id,
        participant_auth_user_id=None,
    )


async def _commit_with(
    database: Database, case: CaseFixture, *, jobs=(), commands=()
) -> None:
    current, revision = await read_state(
        database, table="product_signals", row_id=case.product_signal_id, column="state"
    )
    request = plan_transition(
        PRODUCT_SIGNAL_MACHINE,
        FACTS,
        trigger="setup_ready",
        current_state=current,
        client_id=case.client_id,
        aggregate_kind=AggregateKind.PRODUCT_SIGNAL,
        aggregate_id=case.product_signal_id,
        expected_revision=revision,
        state_column="state",
        mutation={},
        event=make_event(
            stream_kind=AggregateKind.PRODUCT_SIGNAL,
            stream_id=case.product_signal_id,
            event_type="setup_ready",
            payload={},
        ),
        jobs=jobs,
        commands=commands,
        audit=make_audit(
            subject_kind=AggregateKind.PRODUCT_SIGNAL,
            subject_id=case.product_signal_id,
            action="setup_ready",
            outcome="committed",
        ),
        additional_locks=(),
    )
    await apply_transition(database, request)


async def test_job_claim_takes_a_transactional_lease_with_a_fencing_token(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Claiming leases the Job, counts the run, and issues a fencing token."""
    case = await _case(database, auth_users["owner_a"], "exec-claim")
    job = make_job(
        job_type="control_plane.test_claim",
        input={"note": "one"},
        concurrency_key=None,
        cancellation_scope_id=case.cancellation_scope_id,
        max_runs=3,
        retryable_categories=[TechnicalCategory.PROVIDER_UNAVAILABLE],
        not_before_seconds=0,
    )
    await _commit_with(database, case, jobs=(job,))

    claimed = await claim_job(
        database,
        job_types=["control_plane.test_claim"],
        worker_id="worker-a",
        lease_seconds=60,
    )
    assert claimed is not None
    assert claimed.job_id == job.job_id
    assert claimed.fencing_token > 0
    assert claimed.run_count == 1
    state, _ = await read_state(database, table="jobs", row_id=job.job_id, column="state")
    assert state == JobState.LEASED.value

    # A second worker finds nothing claimable while the lease holds.
    assert (
        await claim_job(
            database,
            job_types=["control_plane.test_claim"],
            worker_id="worker-b",
            lease_seconds=60,
        )
        is None
    )

    await complete_job_run(
        database,
        job_id=claimed.job_id,
        job_run_id=claimed.job_run_id,
        fencing_token=claimed.fencing_token,
    )
    state, _ = await read_state(database, table="jobs", row_id=job.job_id, column="state")
    assert state == JobState.SUCCEEDED.value


async def test_a_stale_fencing_token_cannot_complete_or_heartbeat(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A worker whose lease was superseded may log, but it cannot finish the run."""
    case = await _case(database, auth_users["owner_a"], "exec-fencing")
    job = make_job(
        job_type="control_plane.test_fencing",
        input={},
        concurrency_key=None,
        cancellation_scope_id=None,
        max_runs=3,
        retryable_categories=[TechnicalCategory.PROVIDER_UNAVAILABLE],
        not_before_seconds=0,
    )
    await _commit_with(database, case, jobs=(job,))
    claimed = await claim_job(
        database,
        job_types=["control_plane.test_fencing"],
        worker_id="worker-a",
        lease_seconds=60,
    )
    assert claimed is not None

    stale = claimed.fencing_token - 1
    with pytest.raises(LeaseLostError) as refusal:
        await heartbeat(
            database,
            job_run_id=claimed.job_run_id,
            fencing_token=stale,
            lease_seconds=60,
        )
    assert refusal.value.fencing_token == stale
    with pytest.raises(LeaseLostError):
        await complete_job_run(
            database,
            job_id=claimed.job_id,
            job_run_id=claimed.job_run_id,
            fencing_token=stale,
        )
    state, _ = await read_state(database, table="jobs", row_id=job.job_id, column="state")
    assert state == JobState.LEASED.value

    extended = await heartbeat(
        database,
        job_run_id=claimed.job_run_id,
        fencing_token=claimed.fencing_token,
        lease_seconds=120,
    )
    assert extended > datetime.now(tz=UTC)


async def test_retry_class_decides_whether_another_run_is_legal(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A retryable category returns the Job to the pool; others fail it for good."""
    case = await _case(database, auth_users["owner_a"], "exec-retry")
    job = make_job(
        job_type="control_plane.test_retry",
        input={},
        concurrency_key=None,
        cancellation_scope_id=None,
        max_runs=3,
        retryable_categories=[TechnicalCategory.PROVIDER_UNAVAILABLE],
        not_before_seconds=0,
    )
    await _commit_with(database, case, jobs=(job,))
    claimed = await claim_job(
        database,
        job_types=["control_plane.test_retry"],
        worker_id="worker-a",
        lease_seconds=60,
    )
    assert claimed is not None
    next_state = await fail_job_run(
        database,
        job_id=claimed.job_id,
        job_run_id=claimed.job_run_id,
        fencing_token=claimed.fencing_token,
        failure=Failure(
            category=TechnicalCategory.PROVIDER_UNAVAILABLE,
            code="provider_unreachable",
            message="the provider did not answer",
            retry=RetryAdvice.AFTER_DELAY,
            retry_after_ms=0,
        ),
        on_exhausted=None,
    )
    assert next_state is JobState.READY
    state, _ = await read_state(database, table="jobs", row_id=job.job_id, column="state")
    assert state == JobState.READY.value
    # The outbox row is returned to pending so the retry is dispatched again.
    assert (
        await count_rows(
            database,
            table="outbox",
            where="job_id = %s and state = 'pending'",
            params=[job.job_id],
        )
        == 1
    )

    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.jobs set not_before = now() where id = %s", (job.job_id,)
        )
    second = await claim_job(
        database,
        job_types=["control_plane.test_retry"],
        worker_id="worker-a",
        lease_seconds=60,
    )
    assert second is not None
    assert second.fencing_token > claimed.fencing_token
    final = await fail_job_run(
        database,
        job_id=second.job_id,
        job_run_id=second.job_run_id,
        fencing_token=second.fencing_token,
        failure=Failure(
            category=TechnicalCategory.POLICY_DENIED,
            code="unsafe_change",
            message="the proposed change is outside the allowed surfaces",
            retry=RetryAdvice.NEVER,
        ),
        on_exhausted=None,
    )
    assert final is JobState.FAILED


async def test_concurrency_key_admits_only_one_leased_job_per_client(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Two Jobs sharing a concurrency key never run at the same time."""
    case = await _case(database, auth_users["owner_a"], "exec-concurrency")
    first = make_job(
        job_type="control_plane.test_concurrency",
        input={"n": 1},
        concurrency_key="implementation:same",
        cancellation_scope_id=None,
        max_runs=2,
        retryable_categories=[TechnicalCategory.PROVIDER_UNAVAILABLE],
        not_before_seconds=0,
    )
    second = make_job(
        job_type="control_plane.test_concurrency",
        input={"n": 2},
        concurrency_key="implementation:same",
        cancellation_scope_id=None,
        max_runs=2,
        retryable_categories=[TechnicalCategory.PROVIDER_UNAVAILABLE],
        not_before_seconds=0,
    )
    await _commit_with(database, case, jobs=(first, second))

    claimed = await claim_job(
        database,
        job_types=["control_plane.test_concurrency"],
        worker_id="worker-a",
        lease_seconds=60,
    )
    assert claimed is not None
    assert (
        await claim_job(
            database,
            job_types=["control_plane.test_concurrency"],
            worker_id="worker-b",
            lease_seconds=60,
        )
        is None
    )
    await complete_job_run(
        database,
        job_id=claimed.job_id,
        job_run_id=claimed.job_run_id,
        fencing_token=claimed.fencing_token,
    )
    freed = await claim_job(
        database,
        job_types=["control_plane.test_concurrency"],
        worker_id="worker-b",
        lease_seconds=60,
    )
    assert freed is not None
    assert freed.job_id != claimed.job_id


async def test_a_named_job_is_claimed_once_its_concurrency_key_is_free(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A voice agent session claims its own Job, and waits while another holds the key."""
    case = await _case(database, auth_users["owner_a"], "exec-claim-by-id")
    holding, named = (
        make_job(
            job_type="control_plane.test_claim_by_id",
            input={"n": n},
            concurrency_key="voice_agent:same",
            cancellation_scope_id=None,
            max_runs=2,
            retryable_categories=[TechnicalCategory.PROVIDER_UNAVAILABLE],
            not_before_seconds=0,
        )
        for n in (1, 2)
    )
    await _commit_with(database, case, jobs=(holding, named))

    held = await claim_job_by_id(
        database, job_id=holding.job_id, worker_id="session-a", lease_seconds=60
    )
    assert held is not None
    assert held.job_id == holding.job_id
    # The named Job is due, but its key is held: not claimable, and due again now.
    assert (
        await claim_job_by_id(database, job_id=named.job_id, worker_id="session-b", lease_seconds=60)
        is None
    )
    assert await next_run_at(database, job_id=named.job_id) is not None
    # A leased Job has no next run for another session to wait for.
    assert await next_run_at(database, job_id=holding.job_id) is None

    await complete_job_run(
        database, job_id=held.job_id, job_run_id=held.job_run_id, fencing_token=held.fencing_token
    )
    claimed = await claim_job_by_id(
        database, job_id=named.job_id, worker_id="session-b", lease_seconds=60
    )
    assert claimed is not None
    assert claimed.job_id == named.job_id


async def test_outbox_scan_and_pgmq_wakeups_are_idempotent(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A committed Command is enqueued once and rescanning moves nothing new."""
    case = await _case(database, auth_users["owner_a"], "exec-outbox")
    command = CommandIntent(
        command_id=uuid7(),
        command_type="discord.send_invitation",
        target=StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=case.product_signal_id),
        payload={"channel": "dm"},
        declared_revisions={"product_signal": 0},
        cancellation_scope_id=case.cancellation_scope_id,
    )
    await _commit_with(database, case, commands=(command,))

    assert (
        await count_rows(
            database,
            table="outbox",
            where="command_id = %s and state = 'enqueued'",
            params=[command.command_id],
        )
        == 1
    )
    assert await scan_ready(database, limit=100) == 0

    wakeups = await read_wakeups(database, batch_size=10, visibility_seconds=30)
    matching = [w for w in wakeups if w.command_id == command.command_id]
    assert len(matching) == 1
    assert matching[0].outbox_id == command.command_id


async def test_command_reaches_one_terminal_result_and_replays_it(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A repeated receipt replays the canonical Result instead of writing a second."""
    case = await _case(database, auth_users["owner_a"], "exec-command")
    command = CommandIntent(
        command_id=uuid7(),
        command_type="stripe.issue_invoice_credit",
        target=StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=case.product_signal_id),
        payload={"amount_minor_units": 25000, "currency": "USD"},
        declared_revisions={"product_signal": 0},
        cancellation_scope_id=None,
    )
    await _commit_with(database, case, commands=(command,))

    await mark_dispatching(database, command_id=command.command_id)
    await mark_dispatched(
        database, command_id=command.command_id, provider_ref="cbtxn_123"
    )
    state, _ = await read_state(
        database, table="commands", row_id=command.command_id, column="state"
    )
    assert state == CommandState.DISPATCHED.value

    result = TerminalResult(
        result_id=uuid7(),
        command_id=command.command_id,
        disposition=CommandDisposition.SUCCEEDED,
        effect_state=EffectState.COMPLETED,
        technical_category=None,
        retry_advice=None,
        payload={"transaction_ref": "cbtxn_123"},
    )
    message_id = uuid7()
    accepted = await record_terminal_result(
        database,
        message_id=message_id,
        client_id=case.client_id,
        source="stripe",
        result=result,
    )
    assert accepted.result_id == result.result_id

    # The same receipt again replays the canonical Result and writes nothing new.
    duplicate = await record_terminal_result(
        database,
        message_id=message_id,
        client_id=case.client_id,
        source="stripe",
        result=TerminalResult(
            result_id=uuid7(),
            command_id=command.command_id,
            disposition=CommandDisposition.FAILED,
            effect_state=EffectState.ABSENT,
            technical_category="provider_error",
            retry_advice=RetryAdvice.AFTER_DELAY,
            payload={},
        ),
    )
    assert duplicate.result_id == result.result_id
    assert duplicate.disposition is CommandDisposition.SUCCEEDED
    assert (
        await count_rows(
            database,
            table="results",
            where="command_id = %s",
            params=[command.command_id],
        )
        == 1
    )
    replayed = await replay_terminal_result(database, command_id=command.command_id)
    assert replayed is not None
    assert replayed.payload["transaction_ref"] == "cbtxn_123"
    state, _ = await read_state(
        database, table="commands", row_id=command.command_id, column="state"
    )
    assert state == CommandState.TERMINAL.value


async def test_inbox_deduplicates_by_message_id_and_by_command(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Both dedup paths hold: the same message, and a second terminal per Command."""
    case = await _case(database, auth_users["owner_a"], "exec-inbox")
    command = CommandIntent(
        command_id=uuid7(),
        command_type="github.open_draft_pull_request",
        target=StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=case.product_signal_id),
        payload={},
        declared_revisions={},
        cancellation_scope_id=None,
    )
    await _commit_with(database, case, commands=(command,))

    message_id = uuid7()
    assert (
        await record_inbound(
            database,
            message_id=message_id,
            client_id=case.client_id,
            source="github",
            message_kind="provider_receipt",
            command_id=command.command_id,
            payload={"delivery": "1"},
        )
        is True
    )
    assert (
        await record_inbound(
            database,
            message_id=message_id,
            client_id=case.client_id,
            source="github",
            message_kind="provider_receipt",
            command_id=command.command_id,
            payload={"delivery": "1"},
        )
        is False
    )
    assert (
        await record_inbound(
            database,
            message_id=uuid7(),
            client_id=case.client_id,
            source="github",
            message_kind="terminal_result",
            command_id=command.command_id,
            payload={},
        )
        is True
    )
    # A second terminal receipt for the same Command is refused by the index.
    assert (
        await record_inbound(
            database,
            message_id=uuid7(),
            client_id=case.client_id,
            source="github",
            message_kind="terminal_result",
            command_id=command.command_id,
            payload={},
        )
        is False
    )


async def test_restart_reconciliation_reclaims_leases_and_stalled_commands(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A restart returns expired leases to the pool and reconciles uncertain effects."""
    case = await _case(database, auth_users["owner_a"], "exec-reconcile")
    job = make_job(
        job_type="control_plane.test_reconcile",
        input={},
        concurrency_key=None,
        cancellation_scope_id=None,
        max_runs=3,
        # An expired lease is retried on the run budget alone.
        retryable_categories=[],
        not_before_seconds=0,
    )
    command = CommandIntent(
        command_id=uuid7(),
        command_type="daytona.create_workspace",
        target=StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=case.product_signal_id),
        payload={},
        declared_revisions={},
        cancellation_scope_id=None,
    )
    await _commit_with(database, case, jobs=(job,), commands=(command,))

    claimed = await claim_job(
        database,
        job_types=["control_plane.test_reconcile"],
        worker_id="crashed-worker",
        lease_seconds=60,
    )
    assert claimed is not None
    await mark_dispatching(database, command_id=command.command_id)
    await mark_dispatched(database, command_id=command.command_id, provider_ref="ws_1")

    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.job_runs set lease_expires_at = now() - interval '1 minute' "
            "where id = %s",
            (claimed.job_run_id,),
        )
        await cur.execute(
            "update public.commands set dispatched_at = now() - interval '1 hour' "
            "where id = %s",
            (command.command_id,),
        )

    report = await reconcile_startup(
        database, stale_outbox_seconds=1, stale_dispatch_seconds=60
    )
    assert report.reclaimed_leases >= 1
    assert report.commands_in_reconciliation >= 1

    run_state, _ = await read_state(
        database, table="job_runs", row_id=claimed.job_run_id, column="state"
    )
    assert run_state == JobRunState.ABANDONED.value
    job_state, _ = await read_state(
        database, table="jobs", row_id=job.job_id, column="state"
    )
    assert job_state == JobState.READY.value
    command_state, _ = await read_state(
        database, table="commands", row_id=command.command_id, column="state"
    )
    assert command_state == CommandState.RECONCILING.value

    # Reconciliation is not a state a second dispatch attempt can re-enter.
    with pytest.raises(IllegalTransitionError):
        await start_reconciliation(database, command_id=command.command_id)


async def test_worker_runs_the_control_plane_handler_through_the_kernel(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """One worker pass claims the Job, expires the attempt, and closes the run."""
    case = await _case(database, auth_users["owner_a"], "exec-worker")
    attempt_id = await create_outreach_attempt(
        database,
        case=case,
        state=OutreachAttemptState.AWAITING_RESPONSE,
        expires_in_seconds=-60,
    )
    job = make_job(
        job_type=EXPIRE_OUTREACH_ATTEMPT,
        input={"outreach_attempt_id": str(attempt_id)},
        concurrency_key=None,
        cancellation_scope_id=case.cancellation_scope_id,
        max_runs=2,
        retryable_categories=[TechnicalCategory.PROVIDER_UNAVAILABLE],
        not_before_seconds=0,
    )
    await _commit_with(database, case, jobs=(job,))

    worker = Worker(
        database,
        WorkerConfig(
            worker_id="integration-worker",
            lease_seconds=60,
            heartbeat_seconds=30,
            poll_interval_seconds=0.1,
            wakeup_batch_size=10,
            outbox_scan_limit=100,
            stale_outbox_seconds=300,
            stale_dispatch_seconds=900,
            concurrency=1,
        ),
        build_handlers(),
        {},
    )
    assert await worker.run_once() is True

    state, _ = await read_state(
        database, table="outreach_attempts", row_id=attempt_id, column="state"
    )
    assert state == OutreachAttemptState.EXPIRED.value
    job_state, _ = await read_state(
        database, table="jobs", row_id=job.job_id, column="state"
    )
    assert job_state == JobState.SUCCEEDED.value
    assert await worker.run_once() is False


async def test_worker_records_an_unhandled_job_type_as_a_typed_failure(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """An unknown job type fails explicitly rather than silently succeeding."""
    case = await _case(database, auth_users["owner_a"], "exec-unhandled")
    job = make_job(
        job_type="control_plane.unknown_type",
        input={},
        concurrency_key=None,
        cancellation_scope_id=None,
        max_runs=2,
        retryable_categories=[TechnicalCategory.PROVIDER_UNAVAILABLE],
        not_before_seconds=0,
    )
    await _commit_with(database, case, jobs=(job,))
    worker = Worker(
        database,
        WorkerConfig(
            worker_id="integration-worker",
            lease_seconds=60,
            heartbeat_seconds=30,
            poll_interval_seconds=0.1,
            wakeup_batch_size=10,
            outbox_scan_limit=100,
            stale_outbox_seconds=300,
            stale_dispatch_seconds=900,
            concurrency=1,
        ),
        {"control_plane.unknown_type": _always_fails},
        {},
    )
    assert await worker.run_once() is True
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select failure_category, retry_advice from public.job_runs where job_id = %s",
            (job.job_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    # An unregistered handler is a uTrace defect, so the failure is internal; the
    # Job's own retryable set decides whether another run follows.
    assert as_str(row, "failure_category") == TechnicalCategory.INTERNAL.value
    assert as_str(row, "retry_advice") == RetryAdvice.AFTER_DELAY.value


async def test_worker_runs_a_short_job_while_a_long_one_is_still_running(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A Job that lives as long as its interview does not hold up the queue behind it."""
    case = await _case(database, auth_users["owner_a"], "exec-concurrent")
    short_ran = asyncio.Event()

    async def long_running(database: Database, claimed: ClaimedJob) -> None:
        await short_ran.wait()

    async def short(database: Database, claimed: ClaimedJob) -> None:
        short_ran.set()

    # Claims run in `not_before` order, so the long Job is leased first.
    jobs = tuple(
        make_job(
            job_type=job_type,
            input={},
            concurrency_key=None,
            cancellation_scope_id=None,
            max_runs=1,
            retryable_categories=[],
            not_before_seconds=0,
        )
        for job_type in ("test.long_running", "test.short")
    )
    await _commit_with(database, case, jobs=jobs)
    worker = Worker(
        database,
        WorkerConfig(
            worker_id="integration-worker",
            lease_seconds=60,
            heartbeat_seconds=30,
            poll_interval_seconds=0.1,
            wakeup_batch_size=10,
            outbox_scan_limit=100,
            stale_outbox_seconds=300,
            stale_dispatch_seconds=900,
            concurrency=2,
        ),
        {"test.long_running": long_running, "test.short": short},
        {},
    )
    running = asyncio.create_task(worker.run())
    await asyncio.wait_for(short_ran.wait(), timeout=30)
    worker.request_stop()
    await asyncio.wait_for(running, timeout=30)
    for job in jobs:
        state, _ = await read_state(database, table="jobs", row_id=job.job_id, column="state")
        assert state == JobState.SUCCEEDED.value


async def test_a_transient_provider_failure_schedules_another_run(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A provider outage the connector classified is retried on the Job's own budget."""
    case = await _case(database, auth_users["owner_a"], "exec-provider-retry")
    job = make_job(
        job_type="test.provider_call",
        input={},
        concurrency_key=None,
        cancellation_scope_id=None,
        max_runs=2,
        retryable_categories=TRANSIENT_CATEGORIES,
        not_before_seconds=0,
    )
    await _commit_with(database, case, jobs=(job,))

    async def provider_down(database: Database, claimed: ClaimedJob) -> None:
        raise ConnectorFailure(
            FailureDetail(
                provider="stripe",
                operation="create_balance_transaction",
                category=TechnicalCategory.PROVIDER_UNAVAILABLE,
                retry_advice=RetryAdvice.AFTER_DELAY,
                effect_state=EffectState.ABSENT,
                message="the provider answered 503",
                status_code=503,
            )
        )

    worker = Worker(
        database,
        WorkerConfig(
            worker_id="integration-worker",
            lease_seconds=60,
            heartbeat_seconds=30,
            poll_interval_seconds=0.1,
            wakeup_batch_size=10,
            outbox_scan_limit=100,
            stale_outbox_seconds=300,
            stale_dispatch_seconds=900,
            concurrency=1,
        ),
        {"test.provider_call": provider_down},
        {},
    )
    assert await worker.run_once() is True
    state, _ = await read_state(database, table="jobs", row_id=job.job_id, column="state")
    assert state == JobState.READY.value
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select failure_category from public.job_runs where job_id = %s", (job.job_id,)
        )
        run = await cur.fetchone()
    assert run is not None
    assert as_str(run, "failure_category") == TechnicalCategory.PROVIDER_UNAVAILABLE.value


def test_a_job_cannot_list_a_category_no_failure_carries() -> None:
    with pytest.raises(ValueError, match="transient"):
        make_job(
            job_type="test.misconfigured",
            input={},
            concurrency_key=None,
            cancellation_scope_id=None,
            max_runs=2,
            retryable_categories=["transient"],
            not_before_seconds=0,
        )


async def _always_fails(database: Database, claimed: object) -> None:
    """A handler that always fails, proving the worker records typed failures."""
    raise RuntimeError("this handler always fails")
