"""The watched-channel consumer as a process, and operator re-ingestion.

Everything here runs against the real configured Discord server, with a real
gateway session and real messages.

One step a test cannot trigger is the websocket frame itself. The bot is the
only account a test can speak as, and the connector drops the bot's own posts at
the gateway boundary, which is exactly what stops uTrace's own messages becoming
customer reports. So the customer message a real person already posted in a
watched channel is fetched and handed to `_WatchingClient.on_message`, the
handler `discord.py` calls when a MESSAGE_CREATE frame arrives. Everything from
there is the production path, including the receipt queue the consumer reads.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, MutableMapping, Sequence
from uuid import UUID

import discord
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from structlog.testing import capture_logs

from app.clients.configuration import ActiveRelease, WatchedChannel
from app.clients.provider_map import PROVIDER_DISCORD, RESOURCE_USER, upsert_provider_ref
from app.connectors.ports import MessageReceipt
from app.db.pool import Database
from app.db.rows import as_int, as_str
from app.ids import uuid7
from app.intake.jobs import SEAL_INTAKE_BATCH
from app.intake.messages import DebounceWindow, record_message
from app.intake.runtime import (
    IntakeConsumerSettings,
    WatchedChannelConsumers,
    intake_client_ids,
)
from app.intake.settings import get_intake_settings
from app.intake.worker import connector_for_release
from app.main import create_app
from app.ports import PortRegistry
from app.settings import Settings
from tests.conftest import require_env
from tests.supabase_auth import SignedInUser
from tests.workstream import (
    Participant,
    WorkstreamFixture,
    attribute_author,
    create_workstream_client,
    post_fixture_message,
)

BASE_URL = "http://control-plane.test"

_SUPERVISION = IntakeConsumerSettings(
    maximum_attempts=1,
    backoff_seconds=1.0,
    healthy_session_seconds=60.0,
    shutdown_seconds=10.0,
)
"""One attempt, so a gateway that cannot connect fails the test instead of
retrying quietly behind it."""

_SUBSCRIBE_TIMEOUT_SECONDS = 30.0
_MESSAGE_TIMEOUT_SECONDS = 30.0


@pytest_asyncio.fixture
async def fixture(
    database: Database, auth_users: dict[str, SignedInUser]
) -> WorkstreamFixture:
    """One configured client on the real Discord server, with the bot attributed."""
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
    await attribute_author(
        database,
        client_id=built.client_id,
        author_provider_identity=built.bot_user_id,
        participant=built.maya,
    )
    return built


@pytest_asyncio.fixture
async def api(settings: Settings, database: Database) -> AsyncIterator[httpx.AsyncClient]:
    """The assembled application, so the staff route runs behind real authority."""
    app: FastAPI = create_app(
        settings,
        PortRegistry(voice=None, observation=None, evidence=None),
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http:
            yield http


_CUSTOMER_CHANNELS = ("growth-insights", "support")
"""Watched channels of the seeded Shopalytics server that real people posted in."""


async def _customer_message(*, guild_id: str) -> tuple[discord.Message, str, str]:
    """Return the newest message a real person posted in a watched channel.

    Answers the message, its channel reference, and that channel's name. The bot
    cannot post as a person, so the test uses a message a person already wrote.
    """
    client = discord.Client(intents=discord.Intents.none())
    await client.login(require_env("UTRACE_DISCORD_BOT_TOKEN"))
    try:
        guild = await client.fetch_guild(int(guild_id))
        for channel in await guild.fetch_channels():
            if channel.name not in _CUSTOMER_CHANNELS:
                continue
            if not isinstance(channel, discord.abc.Messageable):
                continue
            async for message in channel.history(limit=50):
                if not message.author.bot and message.content:
                    return message, str(channel.id), channel.name
    finally:
        await client.close()
    pytest.skip(
        f"no person-authored message exists in {_CUSTOMER_CHANNELS} of guild "
        f"{guild_id}; the bot cannot post one"
    )


def _also_watching(
    release: ActiveRelease, *, channel_ref: str, name: str
) -> ActiveRelease:
    """Return the release the seeded client publishes: this channel watched too."""
    surface = release.policy.customer_surface
    if surface.watches(channel_ref):
        return release
    widened = surface.model_copy(
        update={
            "watched_channels": (
                *surface.watched_channels,
                WatchedChannel(channel_ref=channel_ref, name=name),
            )
        }
    )
    return release.model_copy(
        update={
            "policy": release.policy.model_copy(update={"customer_surface": widened})
        }
    )


async def _map_reporter(
    database: Database, *, client_id: UUID, guild_id: str, author_id: str, reporter: Participant
) -> None:
    """Map one real Discord account to the reporting client user in this database."""
    async with database.transaction() as conn:
        await upsert_provider_ref(
            conn,
            ref_id=uuid7(),
            client_id=client_id,
            provider=PROVIDER_DISCORD,
            installation_ref=guild_id,
            resource_type=RESOURCE_USER,
            provider_resource_id=author_id,
            owner_kind="client_user",
            owner_id=reporter.client_user_id,
        )


async def _first_receipt(receipts: AsyncIterator[MessageReceipt]) -> MessageReceipt:
    """Return the first receipt the connector's own iterator yields."""
    async for receipt in receipts:
        return receipt
    raise AssertionError("the gateway iterator ended before yielding a receipt")


async def _await_event(
    events: Sequence[MutableMapping[str, object]], name: str, seconds: float
) -> MutableMapping[str, object]:
    """Wait for one structured log event the runtime emits, or fail naming it."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        for event in events:
            if event.get("event") == name:
                return event
        await asyncio.sleep(0.2)
    raise AssertionError(f"the runtime never logged {name} within {seconds:.0f}s")


async def test_the_supervisor_connects_the_seeded_client_and_stops_on_request(
    database: Database, fixture: WorkstreamFixture
) -> None:
    """The consumer a deployed worker starts reaches the client's watched channels."""
    assert await intake_client_ids(database) == (fixture.client_id,)

    consumers = WatchedChannelConsumers(
        database, get_intake_settings(), _SUPERVISION
    )
    with capture_logs() as events:
        run = asyncio.create_task(consumers.run())
        subscribed = await _await_event(
            events, "intake_consumer_subscribed", _SUBSCRIBE_TIMEOUT_SECONDS
        )
        assert subscribed["client_id"] == str(fixture.client_id)
        assert fixture.channel_id in subscribed["channel_refs"]  # type: ignore[operator]

        consumers.request_stop()
        await asyncio.wait_for(run, timeout=_SUPERVISION.shutdown_seconds + 5)

    assert any(event.get("event") == "intake_consumer_stopped" for event in events)


async def test_a_customer_message_reaches_the_durable_record(
    database: Database, fixture: WorkstreamFixture
) -> None:
    """A real person's watched-channel message becomes an Intake Item batch.

    This is the consumer's whole job. The gateway session is live, the message is
    one a person actually wrote in a watched channel, and the handler that turns
    it into a receipt is the one a MESSAGE_CREATE frame calls.
    """
    settings = get_intake_settings()
    message, channel_ref, channel_name = await _customer_message(
        guild_id=fixture.guild_id
    )
    release = _also_watching(fixture.release, channel_ref=channel_ref, name=channel_name)
    await _map_reporter(
        database,
        client_id=fixture.client_id,
        guild_id=fixture.guild_id,
        author_id=str(message.author.id),
        reporter=fixture.maya,
    )

    connector = connector_for_release(settings, release=release)
    await connector.open()
    try:
        async with connector.consume() as receipts:
            # The handler discord.py calls for a MESSAGE_CREATE frame.
            await connector._client.on_message(message)
            receipt = await asyncio.wait_for(
                _first_receipt(receipts), timeout=_MESSAGE_TIMEOUT_SECONDS
            )
    finally:
        await connector.close()

    assert receipt.message_id == str(message.id)
    assert receipt.channel_id == channel_ref
    assert not receipt.author_is_bot

    recorded = await record_message(
        database,
        client_id=fixture.client_id,
        release=release,
        receipt=receipt,
        window=DebounceWindow(quiet_seconds=30, maximum_seconds=120),
    )
    assert not recorded.duplicate
    assert recorded.client_user_id == fixture.maya.client_user_id

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select provider_message_id, ordinal, content from public.intake_messages "
            "where client_id = %s",
            (fixture.client_id,),
        )
        row = await cur.fetchone()
        await cur.execute(
            "select state from public.jobs where job_type = %s", (SEAL_INTAKE_BATCH,)
        )
        job = await cur.fetchone()
    assert row is not None
    assert as_str(row, "provider_message_id") == str(message.id)
    assert as_int(row, "ordinal") == 1
    assert as_str(row, "content") == message.content
    assert job is not None
    assert as_str(job, "state") == "ready"


async def test_the_gateway_handler_drops_the_bots_own_post(
    fixture: WorkstreamFixture
) -> None:
    """uTrace's own message in a watched channel never becomes a receipt.

    This filter is why the staff route exists: a message uTrace posted, or one
    that arrived while no consumer was connected, reaches intake only by being
    re-read deliberately.
    """
    message_id = await post_fixture_message(
        channel_id=fixture.channel_id,
        content="uTrace runtime fixture: the gateway drops this post.",
    )
    connector = connector_for_release(get_intake_settings(), release=fixture.release)
    await connector.open()
    try:
        message = await connector._client.fetch_channel(int(fixture.channel_id))
        assert isinstance(message, discord.abc.Messageable)
        posted = await message.fetch_message(int(message_id))
        async with connector.consume() as receipts:
            await connector._client.on_message(posted)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(_first_receipt(receipts), timeout=2.0)
    finally:
        await connector.close()


async def test_the_staff_route_reingests_a_missed_message_idempotently(
    api: httpx.AsyncClient,
    database: Database,
    fixture: WorkstreamFixture,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Re-ingesting a message the consumer never saw commits one Intake Item."""
    message_id = await post_fixture_message(
        channel_id=fixture.channel_id,
        content="uTrace runtime fixture: the staff route re-ingests this message.",
    )
    staff = {"Authorization": f"Bearer {auth_users['owner_a'].access_token}"}
    path = f"/v1/clients/{fixture.client_id}/intake/messages"
    body = {"channel_ref": fixture.channel_id, "provider_message_id": message_id}

    first = await api.post(path, json=body, headers=staff)
    assert first.status_code == 200, first.text
    ingested = first.json()
    assert ingested["duplicate"] is False
    assert ingested["provider_message_id"] == message_id

    repeat = await api.post(path, json=body, headers=staff)
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["duplicate"] is True
    assert repeat.json()["intake_item_id"] == ingested["intake_item_id"]

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as n from public.intake_items where client_id = %s",
            (fixture.client_id,),
        )
        items = await cur.fetchone()
        await cur.execute(
            "select client_user_id from public.intake_items where client_id = %s",
            (fixture.client_id,),
        )
        reporter = await cur.fetchone()
    assert items is not None
    assert as_int(items, "n") == 1
    assert reporter is not None
    assert reporter["client_user_id"] == fixture.maya.client_user_id


async def test_the_staff_route_refuses_an_unwatched_channel(
    api: httpx.AsyncClient,
    fixture: WorkstreamFixture,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A channel the client's release does not watch is refused before Discord is read."""
    response = await api.post(
        f"/v1/clients/{fixture.client_id}/intake/messages",
        json={"channel_ref": "999999999999999999", "provider_message_id": "1"},
        headers={"Authorization": f"Bearer {auth_users['owner_a'].access_token}"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"] == "AuthorizationError"
    assert "watched intake surface" in response.json()["detail"]


async def test_the_staff_route_refuses_an_unattributed_bot_author(
    api: httpx.AsyncClient,
    database: Database,
    fixture: WorkstreamFixture,
    auth_users: dict[str, SignedInUser],
) -> None:
    """uTrace's own posts stay out of intake unless a client attributes them."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "delete from public.intake_author_attributions where client_id = %s",
            (fixture.client_id,),
        )
    message_id = await post_fixture_message(
        channel_id=fixture.channel_id,
        content="uTrace runtime fixture: an unattributed bot post stays out of intake.",
    )

    response = await api.post(
        f"/v1/clients/{fixture.client_id}/intake/messages",
        json={"channel_ref": fixture.channel_id, "provider_message_id": message_id},
        headers={"Authorization": f"Bearer {auth_users['owner_a'].access_token}"},
    )
    assert response.status_code == 403, response.text
    assert "no reporter attribution" in response.json()["detail"]


async def test_the_staff_route_refuses_a_client_the_caller_does_not_staff(
    api: httpx.AsyncClient,
    fixture: WorkstreamFixture,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Authentication alone never authorizes re-ingestion for a client."""
    response = await api.post(
        f"/v1/clients/{fixture.client_id}/intake/messages",
        json={"channel_ref": fixture.channel_id, "provider_message_id": "1"},
        headers={"Authorization": f"Bearer {auth_users['owner_b'].access_token}"},
    )
    assert response.status_code == 403, response.text
