"""Durable fixtures for the intake, outreach, and client-configuration tests.

Rows are written through the same modules the control plane uses, so a test
exercises the production schema, constraints, and policy body. Discord and Jev
are the real providers; nothing here is mocked.

The Customer Surface fixture posts a message as the uTrace bot and then reads it
back through the connector, which produces a genuine `MessageReceipt`. Intake
refuses a bot-authored message outright, so the test inserts the client-scoped
attribution row that names the reporting client user. Production configuration
writes no such row.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import cast
from uuid import UUID

import discord
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import ClientMemberRole

from app.clients.configuration import (
    ActiveRelease,
    ClientConfigurationPolicy,
    ContactBudget,
    CustomerSurfaceConfig,
    DataSeedingContractRef,
    OutreachOfferPolicy,
    ProductOverviewRef,
    WatchedChannel,
    load_active_release,
    release_digest,
)
from app.clients.overview import (
    ActiveProductOverview,
    activate_overview,
)
from app.clients.provider_map import (
    PROVIDER_DISCORD,
    RESOURCE_CHANNEL,
    RESOURCE_GUILD,
    RESOURCE_SIGN_IN_IDENTITY,
    RESOURCE_STRIPE_CUSTOMER,
    RESOURCE_USER,
    upsert_provider_ref,
)
from app.clients.shopalytics_overview import (
    SHOPALYTICS_MANIFEST_HASH,
    SHOPALYTICS_OVERVIEW,
)
from app.clients.shopalytics_release import shopalytics_implementation_policy
from app.connectors.discord_connector import DiscordConnector
from app.connectors.ports import MessageReceipt
from app.db.pool import Database
from app.envelope import JsonValue
from app.ids import uuid7
from app.ingestion.server_activation import sign_server_manifest
from app.intake.messages import DebounceWindow, record_message, seal_batch
from app.offers.budget import authorize_allowance
from tests.conftest import require_env
from tests.ingestion.factories import annotation_manifest, server_instrumentation_manifest

__all__ = [
    "WorkstreamFixture",
    "attribute_author",
    "create_workstream_client",
    "discord_connector",
    "fixture_policy",
    "ingest_receipt",
    "post_fixture_message",
    "receipt_for",
    "require_dm_opt_in",
]

DUDULEMON_KEY = "dudulemon"
SECOND_ACCOUNT_KEY = "brightpath"
STRIPE_CUSTOMER_REF = "cus_workstream_dudulemon"
BILLING_ACCOUNT_REF = "stripe_sandbox_shopalytics"
OFFER_AMOUNT_MINOR_UNITS = 25_000

_NO_DEBOUNCE = DebounceWindow(quiet_seconds=1, maximum_seconds=1)


@dataclass(frozen=True, slots=True)
class Participant:
    """One client-scoped participant with its provider references."""

    client_user_id: UUID
    utrace_user_id: UUID
    customer_account_id: UUID
    discord_user_id: str


@dataclass(frozen=True, slots=True)
class WorkstreamFixture:
    """One configured client with two customer accounts and three participants."""

    client_id: UUID
    owner_auth_user_id: UUID
    release: ActiveRelease
    overview: ActiveProductOverview
    guild_id: str
    channel_id: str
    bot_user_id: str
    dudulemon_account_id: UUID
    second_account_id: UUID
    maya: Participant
    colleague: Participant
    other_account_reporter: Participant


def fixture_policy(
    *, guild_id: str, channel_id: str, offers_enabled: bool
) -> ClientConfigurationPolicy:
    """Build the pinned policy body the fixture client runs under.

    Intake and outreach never activate telemetry, so the manifests are the
    ingestion fixtures' own, signed with a key no control plane trusts.
    """
    return ClientConfigurationPolicy(
        customer_surface=CustomerSurfaceConfig(
            provider="discord",
            installation_ref=guild_id,
            watched_channels=(
                WatchedChannel(channel_ref=channel_id, name="feature-requests"),
            ),
        ),
        implementation=shopalytics_implementation_policy(
            base_revision="workstream-base",
            workspace_snapshot_id="workstream-snapshot",
            callback_hosts=("api.utrace.dev",),
        ),
        data_seeding=DataSeedingContractRef(
            manifest_hash=SHOPALYTICS_MANIFEST_HASH,
            fixture_clock="2026-09-14T09:00:00Z",
            environment_template_id="shopalytics-dudulemon-preview",
            environment_template_revision=1,
        ),
        outreach_offers=OutreachOfferPolicy(
            enabled=offers_enabled,
            currency="USD",
            fixed_amount_minor_units=OFFER_AMOUNT_MINOR_UNITS,
            authorized_ceiling_minor_units=OFFER_AMOUNT_MINOR_UNITS * 4,
            billing_account_ref=BILLING_ACCOUNT_REF,
            billing_use="invoice credit toward future Shopalytics bills",
            completion_terms=("a completed interview, and validation of any change",),
        ),
        contact_budget=ContactBudget(
            per_person=2, per_client=10, invitation_ttl_seconds=72 * 3600
        ),
        product_overview=ProductOverviewRef(
            content_digest=SHOPALYTICS_OVERVIEW.content_digest(),
        ),
        annotation_manifest=cast("dict[str, JsonValue]", annotation_manifest()),
        server_instrumentation_manifest=sign_server_manifest(
            cast("dict[str, JsonValue]", server_instrumentation_manifest()),
            private_key=Ed25519PrivateKey.generate(),
            key_id="workstream-untrusted",
        ),
        dashboard_base_url="https://app.utrace.dev",
    )


async def _account(
    database: Database,
    *,
    client_id: UUID,
    key: str,
    name: str,
) -> UUID:
    """Insert one customer organization."""
    account_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.customer_accounts (id, client_id, external_account_key, name)
            values (%s, %s, %s, %s)
            """,
            (account_id, client_id, key, name),
        )
    return account_id


async def _participant(
    database: Database,
    *,
    client_id: UUID,
    guild_id: str,
    customer_account_id: UUID,
    discord_user_id: str,
    display_name: str,
    sign_in_provider: str | None,
    sign_in_identity: str | None,
) -> Participant:
    """Insert one participant with its Discord and optional sign-in bindings."""
    utrace_user_id = uuid7()
    client_user_id = uuid7()
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "insert into public.users (id, display_name) values (%s, %s)",
                (utrace_user_id, display_name),
            )
            await cur.execute(
                "insert into public.client_users (id, client_id, utrace_user_id) "
                "values (%s, %s, %s)",
                (client_user_id, client_id, utrace_user_id),
            )
            await cur.execute(
                """
                insert into public.customer_memberships (
                    id, client_id, customer_account_id, client_user_id
                )
                values (%s, %s, %s, %s)
                """,
                (uuid7(), client_id, customer_account_id, client_user_id),
            )
        await upsert_provider_ref(
            conn,
            ref_id=uuid7(),
            client_id=client_id,
            provider=PROVIDER_DISCORD,
            installation_ref=guild_id,
            resource_type=RESOURCE_USER,
            provider_resource_id=discord_user_id,
            owner_kind="client_user",
            owner_id=client_user_id,
        )
        if sign_in_provider is not None and sign_in_identity is not None:
            await upsert_provider_ref(
                conn,
                ref_id=uuid7(),
                client_id=client_id,
                provider=sign_in_provider,
                installation_ref=guild_id,
                resource_type=RESOURCE_SIGN_IN_IDENTITY,
                provider_resource_id=sign_in_identity,
                owner_kind="client_user",
                owner_id=client_user_id,
            )
    return Participant(
        client_user_id=client_user_id,
        utrace_user_id=utrace_user_id,
        customer_account_id=customer_account_id,
        discord_user_id=discord_user_id,
    )


async def create_workstream_client(
    database: Database,
    *,
    owner_auth_user_id: UUID,
    participant_sign_in: tuple[str, str],
    offers_enabled: bool,
) -> WorkstreamFixture:
    """Create one fully configured client against the real Discord surface."""
    guild_id = require_env("UTRACE_DISCORD_SERVER_ID")
    channel_id = require_env("UTRACE_DISCORD_FEATURE_REQUEST_CHANNEL_ID")
    maya_discord_id = require_env("UTRACE_DEMO_MAYA_DISCORD_USER_ID")

    client_id = uuid7()
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "insert into public.clients (id, name, product_name) "
                "values (%s, %s, %s)",
                (client_id, "Shopalytics", "Shopalytics"),
            )
            await cur.execute(
                """
                insert into public.client_members (
                    id, client_id, auth_user_id, role, status
                )
                values (%s, %s, %s, %s, 'active')
                """,
                (uuid7(), client_id, owner_auth_user_id, ClientMemberRole.OWNER.value),
            )
        await upsert_provider_ref(
            conn,
            ref_id=uuid7(),
            client_id=client_id,
            provider=PROVIDER_DISCORD,
            installation_ref=guild_id,
            resource_type=RESOURCE_GUILD,
            provider_resource_id=guild_id,
            owner_kind="client",
            owner_id=client_id,
        )
        await upsert_provider_ref(
            conn,
            ref_id=uuid7(),
            client_id=client_id,
            provider=PROVIDER_DISCORD,
            installation_ref=guild_id,
            resource_type=RESOURCE_CHANNEL,
            provider_resource_id=channel_id,
            owner_kind="client",
            owner_id=client_id,
        )

    dudulemon = await _account(
        database,
        client_id=client_id,
        key=DUDULEMON_KEY,
        name="Dudulemon",
    )
    second = await _account(
        database,
        client_id=client_id,
        key=SECOND_ACCOUNT_KEY,
        name="Brightpath",
    )
    async with database.transaction() as conn:
        await upsert_provider_ref(
            conn,
            ref_id=uuid7(),
            client_id=client_id,
            provider="stripe",
            installation_ref=BILLING_ACCOUNT_REF,
            resource_type=RESOURCE_STRIPE_CUSTOMER,
            provider_resource_id=STRIPE_CUSTOMER_REF,
            owner_kind="customer_account",
            owner_id=dudulemon,
        )

    maya = await _participant(
        database,
        client_id=client_id,
        guild_id=guild_id,
        customer_account_id=dudulemon,
        discord_user_id=maya_discord_id,
        display_name="Maya Okonkwo",
        sign_in_provider=participant_sign_in[0],
        sign_in_identity=participant_sign_in[1],
    )
    colleague = await _participant(
        database,
        client_id=client_id,
        guild_id=guild_id,
        customer_account_id=dudulemon,
        discord_user_id="900000000000000001",
        display_name="Dudulemon colleague",
        sign_in_provider=None,
        sign_in_identity=None,
    )
    other = await _participant(
        database,
        client_id=client_id,
        guild_id=guild_id,
        customer_account_id=second,
        discord_user_id="900000000000000002",
        display_name="Brightpath analyst",
        sign_in_provider=None,
        sign_in_identity=None,
    )


    overview = await activate_overview(
        database, client_id=client_id, overview=SHOPALYTICS_OVERVIEW
    )

    policy = fixture_policy(
        guild_id=guild_id, channel_id=channel_id, offers_enabled=offers_enabled
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.configuration_releases (
                id, client_id, release_number, policy, digest,
                authorized_by_auth_user_id
            )
            values (%s, %s, 1, %s, %s, %s)
            """,
            (
                uuid7(),
                client_id,
                Jsonb(policy.model_dump(mode="json")),
                release_digest(policy),
                owner_auth_user_id,
            ),
        )
    if offers_enabled:
        await authorize_allowance(
            database,
            client_id=client_id,
            currency=policy.outreach_offers.currency,
            authorized_minor_units=policy.outreach_offers.authorized_ceiling_minor_units,
            authorized_by_auth_user_id=owner_auth_user_id,
        )

    release = await load_active_release(database, client_id=client_id)
    bot_user_id = await _bot_user_id(guild_id=guild_id, channel_id=channel_id)
    return WorkstreamFixture(
        client_id=client_id,
        owner_auth_user_id=owner_auth_user_id,
        release=release,
        overview=overview,
        guild_id=guild_id,
        channel_id=channel_id,
        bot_user_id=bot_user_id,
        dudulemon_account_id=dudulemon,
        second_account_id=second,
        maya=maya,
        colleague=colleague,
        other_account_reporter=other,
    )


def discord_connector(*, guild_id: str, channel_id: str) -> DiscordConnector:
    """Build the connector the tests read receipts and send direct messages with."""
    return DiscordConnector(
        bot_token=require_env("UTRACE_DISCORD_BOT_TOKEN"),
        guild_id=guild_id,
        watched_channel_ids=[channel_id],
        direct_message_allowlist=[require_env("UTRACE_DISCORD_DM_TEST_USER_ID")],
    )


async def _bot_user_id(*, guild_id: str, channel_id: str) -> str:
    """Return the authenticated bot's Discord account id."""
    connector = discord_connector(guild_id=guild_id, channel_id=channel_id)
    async with connector:
        bot = await connector.fetch_bot_user()
    return bot.user_id


async def post_fixture_message(*, channel_id: str, content: str) -> str:
    """Post one message in the watched channel and return its provider id.

    The bot is the only account the test can speak as. Intake never treats a
    bot-authored message as a customer report, so the caller pairs this with
    `attribute_author`.
    """
    intents = discord.Intents.none()
    client = discord.Client(intents=intents)
    await client.login(require_env("UTRACE_DISCORD_BOT_TOKEN"))
    try:
        channel = await client.fetch_channel(int(channel_id))
        if not isinstance(channel, discord.abc.Messageable):
            raise TypeError(f"channel {channel_id} carries no messages")
        message = await channel.send(content)
    finally:
        await client.close()
    return str(message.id)


async def receipt_for(
    *, guild_id: str, channel_id: str, message_id: str
) -> MessageReceipt:
    """Read one posted message back through the connector as a real receipt."""
    connector = discord_connector(guild_id=guild_id, channel_id=channel_id)
    async with connector:
        return await connector.fetch_message(
            channel_id=channel_id, message_id=message_id
        )


async def attribute_author(
    database: Database,
    *,
    client_id: UUID,
    author_provider_identity: str,
    participant: Participant,
) -> None:
    """Attribute a bot-authored provider identity to one reporting client user.

    Test-database configuration only: without this row intake refuses the
    message, which is what stops the uTrace bot's own posts becoming reports.
    """
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.intake_author_attributions (
                id, client_id, provider, author_provider_identity, client_user_id,
                customer_account_id, reason
            )
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (client_id, provider, author_provider_identity)
            do update set client_user_id = excluded.client_user_id,
                          customer_account_id = excluded.customer_account_id
            """,
            (
                uuid7(),
                client_id,
                PROVIDER_DISCORD,
                author_provider_identity,
                participant.client_user_id,
                participant.customer_account_id,
                "workstream test fixture",
            ),
        )


async def ingest_receipt(
    database: Database, fixture: WorkstreamFixture, *, receipt: MessageReceipt
) -> UUID:
    """Record one receipt and seal its batch, returning the Intake Item id."""
    recorded = await record_message(
        database,
        client_id=fixture.client_id,
        release=fixture.release,
        receipt=receipt,
        window=_NO_DEBOUNCE,
    )
    sealed = await seal_batch(database, intake_batch_id=recorded.intake_batch_id)
    return sealed.intake_item_id


def require_dm_opt_in() -> None:
    """Skip unless this run opted in to delivering a real direct message."""
    if os.environ.get("UTRACE_DISCORD_ALLOW_TEST_DM") != "1":
        pytest.skip(
            "sending a real direct message is opt-in; set "
            "UTRACE_DISCORD_ALLOW_TEST_DM=1 to run it"
        )

