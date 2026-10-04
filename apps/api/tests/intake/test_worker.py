"""The durable Job path from a recorded message to a committed routing outcome.

Nothing is called directly here: the worker claims the Jobs this workstream
committed, runs the registered handlers, and records their outcomes. It proves
the handlers are wired into `build_handlers()` and that the frozen Job inputs
carry everything a handler needs.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest_asyncio
from utrace_contracts.domain.lifecycle import RoutingOutcome

from app.db.pool import Database
from app.db.rows import as_int, as_str
from app.execution.handlers import build_handlers
from app.execution.worker import Worker, WorkerConfig
from app.intake.jobs import (
    ROUTE_INTAKE_ITEM,
    SEAL_INTAKE_BATCH,
    SEND_INITIAL_INVITATION,
)
from app.intake.messages import DebounceWindow, record_message
from tests.supabase_auth import SignedInUser
from tests.workstream import (
    WorkstreamFixture,
    attribute_author,
    create_workstream_client,
    post_fixture_message,
    receipt_for,
)

CANONICAL_REQUEST = (
    "I keep rebuilding the same conversion filters every Monday. Can Shopalytics let "
    "me save a view for TikTok traffic from women 25-34 on products with negative "
    "sizing reviews, then reopen it later?"
)
_WINDOW = DebounceWindow(quiet_seconds=1, maximum_seconds=2)
_CONFIG = WorkerConfig(
    worker_id="intake-worker-test",
    lease_seconds=120,
    heartbeat_seconds=60,
    poll_interval_seconds=0.1,
    wakeup_batch_size=10,
    outbox_scan_limit=50,
    stale_outbox_seconds=300,
    stale_dispatch_seconds=900,
    concurrency=1,
)


@pytest_asyncio.fixture
async def fixture(
    database: Database, auth_users: dict[str, SignedInUser]
) -> AsyncIterator[WorkstreamFixture]:
    """One configured client with its active Product Overview."""
    participant = auth_users["participant"]
    built = await create_workstream_client(
        database,
        owner_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_sign_in=(
            participant.identity_provider,
            participant.identity_provider_id,
        ),
        offers_enabled=False,
    )
    yield built


async def _job_state(database: Database, *, job_type: str) -> tuple[str, int]:
    """Return one Job type's state and run count for the client under test."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state, run_count from public.jobs where job_type = %s",
            (job_type,),
        )
        row = await cur.fetchone()
    assert row is not None, f"no {job_type} Job was committed"
    return as_str(row, "state"), as_int(row, "run_count")


async def test_the_worker_seals_and_routes_a_recorded_message(
    database: Database, fixture: WorkstreamFixture
) -> None:
    await attribute_author(
        database,
        client_id=fixture.client_id,
        author_provider_identity=fixture.bot_user_id,
        participant=fixture.maya,
    )
    message_id = await post_fixture_message(
        channel_id=fixture.channel_id, content=CANONICAL_REQUEST
    )
    receipt = await receipt_for(
        guild_id=fixture.guild_id,
        channel_id=fixture.channel_id,
        message_id=message_id,
    )
    await record_message(
        database,
        client_id=fixture.client_id,
        release=fixture.release,
        receipt=receipt,
        window=_WINDOW,
    )

    handlers = build_handlers()
    assert SEAL_INTAKE_BATCH in handlers
    assert ROUTE_INTAKE_ITEM in handlers
    assert SEND_INITIAL_INVITATION in handlers

    worker = Worker(database, _CONFIG, handlers, {})
    await asyncio.sleep(_WINDOW.quiet_seconds + 1)

    assert await worker.run_once()
    assert await _job_state(database, job_type=SEAL_INTAKE_BATCH) == ("succeeded", 1)

    assert await worker.run_once()
    assert await _job_state(database, job_type=ROUTE_INTAKE_ITEM) == ("succeeded", 1)

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state, routing_outcome from public.intake_items where client_id = %s",
            (fixture.client_id,),
        )
        item = await cur.fetchone()
        await cur.execute(
            "select originating_client_user_id from public.product_signals "
            "where client_id = %s",
            (fixture.client_id,),
        )
        signal = await cur.fetchone()
    assert item is not None
    assert as_str(item, "state") == "routed"
    assert as_str(item, "routing_outcome") == (
        RoutingOutcome.CREATE_PRODUCT_SIGNAL.value
    )
    assert signal is not None
    assert signal["originating_client_user_id"] == fixture.maya.client_user_id

    # The outreach Job is committed and claimable; this test stops before it so
    # no direct message is delivered.
    state, run_count = await _job_state(database, job_type=SEND_INITIAL_INVITATION)
    assert state == "ready"
    assert run_count == 0
