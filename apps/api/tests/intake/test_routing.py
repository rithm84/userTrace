"""Routing the canonical Shopalytics batch with live Jev and Discord.

Each test posts real messages in the watched channel, reads them back through
the connector, and routes the sealed Intake Item through the same code path the
durable Job runs. The decision model is the real one: no answer is stubbed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest_asyncio
from utrace_contracts.domain.lifecycle import FlowType, RoutingOutcome

from app.connectors.openai_roles import OpenAIRole, openai_gateway_for
from app.connectors.typesafe_jev import JevClient
from app.db.pool import Database
from app.db.rows import as_int, as_str
from app.intake.jobs import SEND_INITIAL_INVITATION
from app.intake.routing import UNSUPPORTED_FLOW, RoutingDependencies, route_intake_item
from app.intake.settings import get_intake_settings
from tests.supabase_auth import SignedInUser
from tests.workstream import (
    Participant,
    WorkstreamFixture,
    attribute_author,
    create_workstream_client,
    discord_connector,
    ingest_receipt,
    post_fixture_message,
    receipt_for,
)

CANONICAL_REQUEST = (
    "I keep rebuilding the same conversion filters every Monday. Can Shopalytics let "
    "me save a view for TikTok traffic from women 25-34 on products with negative "
    "sizing reviews, then reopen it later?"
)
SAME_CONCERN_FROM_COLLEAGUE = (
    "Same problem here on the Dudulemon account: every Monday I set the TikTok "
    "channel, the women 25-34 segment and the negative sizing review filters again "
    "from scratch, because the dashboard forgets them. We need to save that view and "
    "reopen it."
)
PRODUCT_FAILURE = (
    "The conversion dashboard crashes with a blank white screen every time I apply "
    "the TikTok channel filter. It worked last week and now it errors out completely."
)
NOISE = (
    "Morning everyone! Congratulations to the Shopalytics team on the new logo, it "
    "looks great. Have a good week."
)
AMBIGUOUS_FOLLOW_UP = "same as what they said above, still the same for us"


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


async def _route_as(
    database: Database,
    fixture: WorkstreamFixture,
    *,
    reporter: Participant,
    content: str,
) -> UUID:
    """Post one message as a reporter, ingest it, and route the Intake Item."""
    await attribute_author(
        database,
        client_id=fixture.client_id,
        author_provider_identity=fixture.bot_user_id,
        participant=reporter,
    )
    message_id = await post_fixture_message(
        channel_id=fixture.channel_id, content=content
    )
    receipt = await receipt_for(
        guild_id=fixture.guild_id,
        channel_id=fixture.channel_id,
        message_id=message_id,
    )
    return await ingest_receipt(database, fixture, receipt=receipt)


async def _dependencies(fixture: WorkstreamFixture) -> RoutingDependencies:
    """Open the real providers routing calls."""
    settings = get_intake_settings()
    connector = discord_connector(
        guild_id=fixture.guild_id, channel_id=fixture.channel_id
    )
    await connector.open()
    jev = JevClient(
        api_key=settings.jev_api_key,
        model_id=settings.jev_model_id,
        timeout_seconds=settings.jev_timeout_seconds,
    )
    await jev.__aenter__()
    return RoutingDependencies(
        jev=jev,
        surface=connector,
        reasoning=openai_gateway_for(OpenAIRole.INTAKE_ASSISTANCE),
    )


async def _close(dependencies: RoutingDependencies) -> None:
    """Close the provider clients one routing run opened."""
    await dependencies.jev.__aexit__(None, None, None)
    await dependencies.reasoning.__aexit__(None, None, None)
    surface = dependencies.surface
    if surface is not None and hasattr(surface, "close"):
        await surface.close()


async def _invitation_jobs(database: Database, *, client_id: UUID) -> int:
    """Count the initial-invitation Jobs committed for one client."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as total from public.jobs "
            "where client_id = %s and job_type = %s",
            (client_id, SEND_INITIAL_INVITATION),
        )
        row = await cur.fetchone()
    return as_int(row, "total") if row is not None else 0


async def test_each_report_opens_its_own_product_signal(
    database: Database, fixture: WorkstreamFixture
) -> None:
    dependencies = await _dependencies(fixture)
    try:
        first_item = await _route_as(
            database, fixture, reporter=fixture.maya, content=CANONICAL_REQUEST
        )
        created = await route_intake_item(
            database, dependencies, intake_item_id=first_item
        )
        assert created.outcome is RoutingOutcome.CREATE_PRODUCT_SIGNAL
        assert created.product_signal_id is not None
        signal_id = created.product_signal_id

        async with database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "select state, initial_flow_type, originating_client_user_id, "
                "pinned_configuration_release_id from public.product_signals "
                "where id = %s",
                (signal_id,),
            )
            signal = await cur.fetchone()
        assert signal is not None
        assert as_str(signal, "state") == "awaiting_interview"
        assert as_str(signal, "initial_flow_type") == FlowType.FEATURE_REQUEST_FLOW.value
        assert signal["originating_client_user_id"] == fixture.maya.client_user_id
        assert signal["pinned_configuration_release_id"] == fixture.release.id
        assert await _invitation_jobs(database, client_id=fixture.client_id) == 1

        # Replaying a routed Intake Item returns its committed outcome.
        replay = await route_intake_item(
            database, dependencies, intake_item_id=first_item
        )
        assert replay.outcome is RoutingOutcome.CREATE_PRODUCT_SIGNAL
        assert replay.product_signal_id == signal_id

        # A later report about the same concern opens its own Product Signal,
        # with its own originating user and invitation: intake never matches.
        colleague_item = await _route_as(
            database,
            fixture,
            reporter=fixture.colleague,
            content=SAME_CONCERN_FROM_COLLEAGUE,
        )
        second = await route_intake_item(
            database, dependencies, intake_item_id=colleague_item
        )
        assert second.outcome is RoutingOutcome.CREATE_PRODUCT_SIGNAL
        assert second.product_signal_id is not None
        assert second.product_signal_id != signal_id
        assert await _invitation_jobs(database, client_id=fixture.client_id) == 2

        async with database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "select count(*) as total from public.source_reports "
                "where product_signal_id = %s",
                (signal_id,),
            )
            reports = await cur.fetchone()
            await cur.execute(
                "select product_overview_version_id, decision_model_id "
                "from public.routing_decisions where intake_item_id = %s",
                (first_item,),
            )
            decision = await cur.fetchone()
        assert reports is not None
        assert as_int(reports, "total") == 1
        assert decision is not None
        assert decision["product_overview_version_id"] == fixture.overview.id
        assert as_str(decision, "decision_model_id") == get_intake_settings().jev_model_id
    finally:
        await _close(dependencies)


async def test_a_product_failure_is_held_as_an_unsupported_flow(
    database: Database, fixture: WorkstreamFixture
) -> None:
    """Bugfix Flow is not in the demo, so a failure report stays routing_blocked."""
    dependencies = await _dependencies(fixture)
    try:
        item_id = await _route_as(
            database, fixture, reporter=fixture.maya, content=PRODUCT_FAILURE
        )
        held = await route_intake_item(database, dependencies, intake_item_id=item_id)
    finally:
        await _close(dependencies)

    assert held.outcome is None
    assert held.product_signal_id is None
    assert held.blocked_reason == UNSUPPORTED_FLOW
    assert await _invitation_jobs(database, client_id=fixture.client_id) == 0

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state, blocked_reason from public.intake_items where id = %s",
            (item_id,),
        )
        item = await cur.fetchone()
    assert item is not None
    assert as_str(item, "state") == "routing_blocked"
    assert as_str(item, "blocked_reason") == UNSUPPORTED_FLOW


async def test_noise_closes_without_a_product_signal(
    database: Database, fixture: WorkstreamFixture
) -> None:
    dependencies = await _dependencies(fixture)
    try:
        item_id = await _route_as(
            database, fixture, reporter=fixture.maya, content=NOISE
        )
        closed = await route_intake_item(
            database, dependencies, intake_item_id=item_id
        )
    finally:
        await _close(dependencies)

    assert closed.outcome is RoutingOutcome.CLOSE_AS_NOISE
    assert closed.product_signal_id is None

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as total from public.product_signals where client_id = %s",
            (fixture.client_id,),
        )
        signals = await cur.fetchone()
        await cur.execute(
            "select state, routing_outcome from public.intake_items where id = %s",
            (item_id,),
        )
        item = await cur.fetchone()
    assert signals is not None
    assert as_int(signals, "total") == 0
    assert item is not None
    assert as_str(item, "state") == "routed"
    assert as_str(item, "routing_outcome") == RoutingOutcome.CLOSE_AS_NOISE.value


async def test_unresolved_uncertainty_blocks_routing_and_preserves_the_item(
    database: Database, fixture: WorkstreamFixture
) -> None:
    """A report that depends on context nobody can supply blocks, never invents noise."""
    settings = get_intake_settings()
    jev = JevClient(
        api_key=settings.jev_api_key,
        model_id=settings.jev_model_id,
        timeout_seconds=settings.jev_timeout_seconds,
    )
    await jev.__aenter__()
    # No surface: conditional assistance can add only the rest of the Product
    # Overview, and GPT-6.1 Sol's interpretation cannot supply the missing facts.
    dependencies = RoutingDependencies(
        jev=jev,
        surface=None,
        reasoning=openai_gateway_for(OpenAIRole.INTAKE_ASSISTANCE),
    )
    try:
        item_id = await _route_as(
            database, fixture, reporter=fixture.maya, content=AMBIGUOUS_FOLLOW_UP
        )
        blocked = await route_intake_item(
            database, dependencies, intake_item_id=item_id
        )
    finally:
        await jev.__aexit__(None, None, None)
        await dependencies.reasoning.__aexit__(None, None, None)

    assert blocked.outcome is None
    assert blocked.blocked_reason is not None

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state, routing_outcome, blocked_reason "
            "from public.intake_items where id = %s",
            (item_id,),
        )
        item = await cur.fetchone()
        await cur.execute(
            "select count(*) as total from public.product_signals where client_id = %s",
            (fixture.client_id,),
        )
        signals = await cur.fetchone()
    assert item is not None
    assert as_str(item, "state") == "routing_blocked"
    assert item["routing_outcome"] is None
    assert as_str(item, "blocked_reason")
    assert signals is not None
    assert as_int(signals, "total") == 0
