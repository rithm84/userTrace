"""Seed the Shopalytics demo client configuration.

Idempotent: every row is keyed on a natural identifier, so a second run rebinds
what exists rather than creating a second client, a second membership, or a
second configuration release. Re-running after an edit publishes a new immutable
release, which is what activation means.

What it configures:

- the Shopalytics client and its owner membership for a configurable staff email
- the Dudulemon customer account, Maya's client-scoped identity, her customer
  membership, and her Discord and sign-in provider references
- provider references for the Discord guild and its watched channels, the
  authorized GitHub repository, and the mapped Stripe Customer when one exists
- the active Product Overview Version
- one immutable configuration release pinning the watched channels, the
  implementation policy the Implementation Gate narrows, the Data Seeding
  Contract reference, Outreach Offers policy, contact budgets, the Product
  Overview revision, both SDK manifests, and the dashboard origin
- the owner-authorized Outreach Offers issuance ceiling

The annotation and server instrumentation manifests are the fixture checkout's
own compiled output (`npm run utrace:manifest`). The server manifest is bound to
this client and signed with the release key, whose public half must be the
configured trust anchor, so activation verifies it exactly as it will in use.

Run it from the repository root::

    set -a; source .env.local; set +a
    uv run --project apps/api python apps/api/scripts/seed_shopalytics_client.py

Contract: docs/arch-components/client-configuration.md
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import UUID

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import ClientMemberRole

from app.clients.configuration import (
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
from app.clients.overview import activate_overview
from app.clients.provider_map import (
    PROVIDER_DISCORD,
    RESOURCE_CHANNEL,
    RESOURCE_GITHUB_REPOSITORY,
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
from app.clients.shopalytics_release import (
    SHOPALYTICS_REPOSITORY,
    shopalytics_implementation_policy,
)
from app.connectors.discord_connector import DiscordConnector
from app.db.pool import Database
from app.db.rows import as_uuid
from app.envelope import JsonValue
from app.ids import uuid7
from app.ingestion.server_activation import sign_server_manifest
from app.ingestion.signing import b64url_decode, public_key_b64url
from app.offers.budget import authorize_allowance, read_allowance
from app.preview.recipe import TemplateRecipe

CLIENT_NAME: Final[str] = "Shopalytics"
PRODUCT_NAME: Final[str] = "Shopalytics"
CUSTOMER_ACCOUNT_KEY: Final[str] = "dudulemon"
CUSTOMER_ACCOUNT_NAME: Final[str] = "Dudulemon"
MAYA_DISPLAY_NAME: Final[str] = "Maya Okonkwo"

WATCHED_CHANNEL_NAMES: Final[tuple[str, ...]] = (
    "support",
    "growth-insights",
    "feature-requests",
)
CALLBACK_HOSTS: Final[tuple[str, ...]] = ("api.utrace.dev",)
"""uTrace endpoints an implementation workspace may reach beyond the registries."""
DASHBOARD_BASE_URL: Final[str] = "https://utrace.dev"
"""The authenticated dashboard a draft PR links its Case File from."""
FIXTURE_CLOCK: Final[str] = "2026-09-14T09:00:00Z"
ENVIRONMENT_TEMPLATE: Final[str] = "shopalytics-dudulemon-preview"
OFFER_CURRENCY: Final[str] = "USD"
OFFER_AMOUNT_MINOR_UNITS: Final[int] = 25_000
OFFER_CEILING_MINOR_UNITS: Final[int] = 250_000
BILLING_ACCOUNT_REF: Final[str] = "stripe_sandbox_shopalytics"

_HTTP_TIMEOUT: Final[float] = 30.0
_OWNER_CLIENT: Final[str] = "client"
_OWNER_CLIENT_USER: Final[str] = "client_user"
_OWNER_CUSTOMER_ACCOUNT: Final[str] = "customer_account"


class SeedError(RuntimeError):
    """Configuration the seed needs is missing or inconsistent."""


def _env(name: str) -> str:
    """Return a required environment variable or refuse, naming it."""
    value = os.environ.get(name)
    if not value:
        raise SeedError(f"{name} is not set; source the repository .env.local first")
    return value


def _optional_env(name: str) -> str | None:
    """Return an optional environment variable."""
    value = os.environ.get(name)
    return value if value else None


@dataclass(frozen=True, slots=True)
class SeedInputs:
    """Everything the seed reads from the environment, resolved once."""

    supabase_url: str
    supabase_secret_key: str
    database_url: str
    client_id: UUID
    discord_bot_token: str
    discord_guild_id: str
    feature_request_channel_id: str
    maya_discord_user_id: str
    staff_email: str
    staff_password: str
    stripe_customer_id: str | None
    base_revision: str
    workspace_snapshot_id: str
    fixture_repository: Path
    manifest_signing_key: str
    manifest_key_id: str
    manifest_trust_anchor: str


def read_inputs() -> SeedInputs:
    """Read and validate the seed's configuration."""
    return SeedInputs(
        supabase_url=_env("SUPABASE_URL"),
        supabase_secret_key=_env("UTRACE_SUPABASE_SECRET_KEY"),
        database_url=_env("UTRACE_DATABASE_URL"),
        client_id=UUID(_env("UTRACE_CLIENT_ID")),
        discord_bot_token=_env("UTRACE_DISCORD_BOT_TOKEN"),
        discord_guild_id=_env("UTRACE_DISCORD_SERVER_ID"),
        feature_request_channel_id=_env("UTRACE_DISCORD_FEATURE_REQUEST_CHANNEL_ID"),
        maya_discord_user_id=_env("UTRACE_DEMO_MAYA_DISCORD_USER_ID"),
        staff_email=_env("UTRACE_DEMO_STAFF_EMAIL"),
        staff_password=_env("UTRACE_DEMO_STAFF_PASSWORD"),
        stripe_customer_id=_optional_env("UTRACE_STRIPE_DUDULEMON_CUSTOMER_ID"),
        # Implementation starts from the exact fixture commit the participant's
        # base preview was built from, so the candidate differs from it only by
        # the attempt's own change.
        base_revision=TemplateRecipe.load(
            Path(_env("UTRACE_PREVIEW_TEMPLATE_RECIPE"))
        ).fixture_revision,
        workspace_snapshot_id=_env("UTRACE_IMPLEMENTATION_WORKSPACE_SNAPSHOT_ID"),
        fixture_repository=Path(_env("UTRACE_PREVIEW_FIXTURE_REPOSITORY")),
        manifest_signing_key=_env("UTRACE_SERVER_MANIFEST_SIGNING_KEY"),
        manifest_key_id=_env("UTRACE_SERVER_MANIFEST_TRUST_ANCHOR_KEY_ID"),
        manifest_trust_anchor=_env("UTRACE_SERVER_MANIFEST_TRUST_ANCHOR"),
    )


def ensure_auth_user(inputs: SeedInputs) -> UUID:
    """Return the staff Supabase Auth identity, creating it when absent."""
    headers = {
        "apikey": inputs.supabase_secret_key,
        "Authorization": f"Bearer {inputs.supabase_secret_key}",
    }
    base = inputs.supabase_url.rstrip("/")
    listing = httpx.get(
        f"{base}/auth/v1/admin/users",
        headers=headers,
        params={"page": 1, "per_page": 200},
        timeout=_HTTP_TIMEOUT,
    )
    if listing.status_code >= 300:
        raise SeedError(
            f"Supabase admin user listing failed with {listing.status_code}"
        )
    for user in listing.json().get("users", []):
        if str(user.get("email", "")).casefold() == inputs.staff_email.casefold():
            return UUID(str(user["id"]))

    created = httpx.post(
        f"{base}/auth/v1/admin/users",
        headers=headers,
        json={
            "email": inputs.staff_email,
            "password": inputs.staff_password,
            "email_confirm": True,
        },
        timeout=_HTTP_TIMEOUT,
    )
    if created.status_code >= 300:
        raise SeedError(
            f"Supabase admin user creation failed with {created.status_code}"
        )
    return UUID(str(created.json()["id"]))


async def discover_watched_channels(inputs: SeedInputs) -> tuple[WatchedChannel, ...]:
    """Resolve the watched channels by name in the configured guild.

    Discovery is the single path: the channel set is what the guild actually
    contains, and a missing channel is an explicit setup error rather than a
    silently narrower intake surface.
    """
    connector = DiscordConnector(
        bot_token=inputs.discord_bot_token,
        guild_id=inputs.discord_guild_id,
        watched_channel_ids=[inputs.feature_request_channel_id],
        direct_message_allowlist=None,
    )
    async with connector:
        channels = await connector.list_guild_channels()
    by_name = {channel.name: channel for channel in channels}
    resolved: list[WatchedChannel] = []
    for name in WATCHED_CHANNEL_NAMES:
        found = by_name.get(name)
        if found is None:
            raise SeedError(
                f"guild {inputs.discord_guild_id} has no channel named '{name}'; "
                f"found {sorted(by_name)}"
            )
        resolved.append(WatchedChannel(channel_ref=found.channel_id, name=name))
    feature_requests = next(
        channel for channel in resolved if channel.name == "feature-requests"
    )
    if feature_requests.channel_ref != inputs.feature_request_channel_id:
        raise SeedError(
            "UTRACE_DISCORD_FEATURE_REQUEST_CHANNEL_ID does not match the "
            f"'feature-requests' channel in guild {inputs.discord_guild_id}"
        )
    return tuple(resolved)


def build_policy(
    *, watched: tuple[WatchedChannel, ...], inputs: SeedInputs, client_id: UUID
) -> ClientConfigurationPolicy:
    """Build the configuration body this release pins."""
    annotation, server = _compiled_manifests(inputs.fixture_repository)
    # The compiled manifest names the fixture's own pinned client; activation
    # refuses a manifest for any client but the one it activates.
    server["client_id"] = str(client_id)
    return ClientConfigurationPolicy(
        customer_surface=CustomerSurfaceConfig(
            provider="discord",
            installation_ref=inputs.discord_guild_id,
            watched_channels=watched,
        ),
        implementation=shopalytics_implementation_policy(
            base_revision=inputs.base_revision,
            workspace_snapshot_id=inputs.workspace_snapshot_id,
            callback_hosts=CALLBACK_HOSTS,
        ),
        data_seeding=DataSeedingContractRef(
            manifest_hash=SHOPALYTICS_MANIFEST_HASH,
            fixture_clock=FIXTURE_CLOCK,
            environment_template_id=ENVIRONMENT_TEMPLATE,
            environment_template_revision=1,
        ),
        outreach_offers=OutreachOfferPolicy(
            enabled=inputs.stripe_customer_id is not None,
            currency=OFFER_CURRENCY,
            fixed_amount_minor_units=OFFER_AMOUNT_MINOR_UNITS,
            authorized_ceiling_minor_units=OFFER_CEILING_MINOR_UNITS,
            billing_account_ref=BILLING_ACCOUNT_REF,
            billing_use="invoice credit toward future Shopalytics bills",
            completion_terms=(
                "a completed interview, and validation of any change uTrace builds",
                "participation may span more than one session",
                "honest negative feedback still earns the credit",
            ),
        ),
        contact_budget=ContactBudget(
            per_person=3, per_client=50, invitation_ttl_seconds=72 * 3600
        ),
        product_overview=ProductOverviewRef(
            content_digest=SHOPALYTICS_OVERVIEW.content_digest(),
        ),
        annotation_manifest=annotation,
        server_instrumentation_manifest=sign_server_manifest(
            server,
            private_key=_release_key(inputs),
            key_id=inputs.manifest_key_id,
        ),
        dashboard_base_url=DASHBOARD_BASE_URL,
    )


def _compiled_manifests(
    fixture_repository: Path,
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    """Read the fixture release's compiled annotation and server manifests."""
    compiled = fixture_repository / "build" / "utrace"
    documents: list[dict[str, JsonValue]] = []
    for name in ("annotation-manifest.json", "server-manifest.json"):
        path = compiled / name
        if not path.is_file():
            raise SeedError(
                f"{path} does not exist; run `npm run utrace:manifest` in the "
                "Shopalytics checkout first"
            )
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise SeedError(f"{path} is not a JSON object")
        documents.append(document)
    return documents[0], documents[1]


def _release_key(inputs: SeedInputs) -> Ed25519PrivateKey:
    """Load the release signing key and prove it is the configured trust anchor's pair."""
    key = Ed25519PrivateKey.from_private_bytes(
        b64url_decode(inputs.manifest_signing_key, "UTRACE_SERVER_MANIFEST_SIGNING_KEY")
    )
    if public_key_b64url(key.public_key()) != inputs.manifest_trust_anchor.rstrip("="):
        raise SeedError(
            "UTRACE_SERVER_MANIFEST_SIGNING_KEY is not the private half of "
            "UTRACE_SERVER_MANIFEST_TRUST_ANCHOR, so activation would refuse the manifest"
        )
    return key


async def _upsert_client(conn: AsyncConnection[DictRow], *, client_id: UUID) -> UUID:
    """Return the Shopalytics client row, creating it on the first run.

    The id is the configured `UTRACE_CLIENT_ID`, which the web dashboard is also
    bound to, so a demo reset and reseed keep the dashboard pointing at the client.
    """
    async with conn.cursor() as cur:
        await cur.execute("select id from public.clients where name = %s", (CLIENT_NAME,))
        row = await cur.fetchone()
        if row is not None:
            existing = as_uuid(row, "id")
            if existing != client_id:
                raise SeedError(
                    f"the {CLIENT_NAME} client exists as {existing}, not the configured "
                    f"UTRACE_CLIENT_ID {client_id}; reset the target or correct the variable"
                )
            return existing
        await cur.execute(
            "insert into public.clients (id, name, product_name) values (%s, %s, %s)",
            (client_id, CLIENT_NAME, PRODUCT_NAME),
        )
    return client_id


async def _upsert_owner(
    conn: AsyncConnection[DictRow], *, client_id: UUID, auth_user_id: UUID
) -> UUID:
    """Return the client's active owner membership for the staff identity."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, role from public.client_members
            where client_id = %s and auth_user_id = %s and status = 'active'
            """,
            (client_id, auth_user_id),
        )
        row = await cur.fetchone()
        if row is not None:
            return as_uuid(row, "id")
        member_id = uuid7()
        await cur.execute(
            """
            insert into public.client_members (
                id, client_id, auth_user_id, role, status
            )
            values (%s, %s, %s, %s, 'active')
            """,
            (member_id, client_id, auth_user_id, ClientMemberRole.OWNER.value),
        )
    return member_id


async def _upsert_customer_account(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    external_account_key: str,
    name: str,
) -> UUID:
    """Return one customer organization, creating or rebinding it."""
    account_id = uuid7()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.customer_accounts (id, client_id, external_account_key, name)
            values (%s, %s, %s, %s)
            on conflict (client_id, external_account_key)
            do update set name = excluded.name, updated_at = now()
            returning id
            """,
            (account_id, client_id, external_account_key, name),
        )
        row = await cur.fetchone()
    if row is None:
        raise SeedError(f"customer account {external_account_key} could not be written")
    return as_uuid(row, "id")


async def _upsert_participant(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    customer_account_id: UUID,
    discord_user_id: str,
    display_name: str,
) -> UUID:
    """Return Maya's client-scoped identity with her customer membership."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select client_users.id from public.client_users as client_users
            join public.provider_refs as refs
              on refs.owner_kind = 'client_user' and refs.owner_id = client_users.id
            where client_users.client_id = %s and refs.provider = %s
              and refs.resource_type = %s and refs.provider_resource_id = %s
            """,
            (client_id, PROVIDER_DISCORD, RESOURCE_USER, discord_user_id),
        )
        existing = await cur.fetchone()
        if existing is not None:
            client_user_id = as_uuid(existing, "id")
        else:
            utrace_user_id = uuid7()
            client_user_id = uuid7()
            await cur.execute(
                "insert into public.users (id, display_name) values (%s, %s)",
                (utrace_user_id, display_name),
            )
            await cur.execute(
                """
                insert into public.client_users (id, client_id, utrace_user_id)
                values (%s, %s, %s)
                """,
                (client_user_id, client_id, utrace_user_id),
            )
        await cur.execute(
            """
            insert into public.customer_memberships (
                id, client_id, customer_account_id, client_user_id
            )
            values (%s, %s, %s, %s)
            on conflict (client_id, customer_account_id, client_user_id) do nothing
            """,
            (uuid7(), client_id, customer_account_id, client_user_id),
        )
    return client_user_id


async def _write_provider_refs(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    client_user_id: UUID,
    customer_account_id: UUID,
    inputs: SeedInputs,
    watched: tuple[WatchedChannel, ...],
) -> int:
    """Record every provider reference this client's configuration depends on."""
    written = 0
    references: list[tuple[str, str, str, str, str, UUID]] = [
        (
            PROVIDER_DISCORD,
            inputs.discord_guild_id,
            RESOURCE_GUILD,
            inputs.discord_guild_id,
            _OWNER_CLIENT,
            client_id,
        ),
        (
            "github",
            SHOPALYTICS_REPOSITORY,
            RESOURCE_GITHUB_REPOSITORY,
            SHOPALYTICS_REPOSITORY,
            _OWNER_CLIENT,
            client_id,
        ),
        (
            PROVIDER_DISCORD,
            inputs.discord_guild_id,
            RESOURCE_USER,
            inputs.maya_discord_user_id,
            _OWNER_CLIENT_USER,
            client_user_id,
        ),
        (
            PROVIDER_DISCORD,
            inputs.discord_guild_id,
            RESOURCE_SIGN_IN_IDENTITY,
            inputs.maya_discord_user_id,
            _OWNER_CLIENT_USER,
            client_user_id,
        ),
    ]
    for channel in watched:
        references.append(
            (
                PROVIDER_DISCORD,
                inputs.discord_guild_id,
                RESOURCE_CHANNEL,
                channel.channel_ref,
                _OWNER_CLIENT,
                client_id,
            )
        )
    if inputs.stripe_customer_id is not None:
        references.append(
            (
                "stripe",
                BILLING_ACCOUNT_REF,
                RESOURCE_STRIPE_CUSTOMER,
                inputs.stripe_customer_id,
                _OWNER_CUSTOMER_ACCOUNT,
                customer_account_id,
            )
        )
    for provider, installation, resource_type, resource_id, owner_kind, owner_id in (
        references
    ):
        await upsert_provider_ref(
            conn,
            ref_id=uuid7(),
            client_id=client_id,
            provider=provider,
            installation_ref=installation,
            resource_type=resource_type,
            provider_resource_id=resource_id,
            owner_kind=owner_kind,
            owner_id=owner_id,
        )
        written += 1
    return written


async def _publish_release(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    policy: ClientConfigurationPolicy,
    authorized_by_auth_user_id: UUID,
) -> tuple[UUID, bool]:
    """Publish the immutable release, unless an identical one is already active."""
    digest = release_digest(policy)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, release_number, digest from public.configuration_releases
            where client_id = %s order by release_number desc limit 1
            """,
            (client_id,),
        )
        current = await cur.fetchone()
        if current is not None and str(current["digest"]) == digest:
            return as_uuid(current, "id"), False
        number = int(str(current["release_number"])) + 1 if current is not None else 1
        release_id = uuid7()
        await cur.execute(
            """
            insert into public.configuration_releases (
                id, client_id, release_number, policy, digest,
                authorized_by_auth_user_id
            )
            values (%s, %s, %s, %s, %s, %s)
            """,
            (
                release_id,
                client_id,
                number,
                Jsonb(policy.model_dump(mode="json")),
                digest,
                authorized_by_auth_user_id,
            ),
        )
    return release_id, True


async def _prepare_overview(database: Database, *, client_id: UUID) -> tuple[UUID, bool]:
    """Activate the Product Overview, reusing the version that already holds it."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.product_overview_versions
            where client_id = %s and content_digest = %s
            """,
            (client_id, SHOPALYTICS_OVERVIEW.content_digest()),
        )
        existing = await cur.fetchone()
    if existing is not None:
        return as_uuid(existing, "id"), False
    active = await activate_overview(
        database, client_id=client_id, overview=SHOPALYTICS_OVERVIEW
    )
    return active.id, True


async def _authorize_ceiling(
    database: Database,
    *,
    client_id: UUID,
    offers: OutreachOfferPolicy,
    authorized_by_auth_user_id: UUID,
) -> bool:
    """Authorize the Outreach Offers issuance ceiling, and report whether it moved.

    `authorize_allowance` is the one writer of the allowance: it alone refuses a
    ceiling below what is already reserved or earned. It appends on every call, so
    the seed reads the authorized ceiling first and a re-run leaves the
    append-only record alone when the policy already authorizes that amount.
    """
    allowance = await read_allowance(
        database, client_id=client_id, currency=offers.currency
    )
    if allowance.authorized_minor_units == offers.authorized_ceiling_minor_units:
        return False
    await authorize_allowance(
        database,
        client_id=client_id,
        currency=offers.currency,
        authorized_minor_units=offers.authorized_ceiling_minor_units,
        authorized_by_auth_user_id=authorized_by_auth_user_id,
    )
    return True


@dataclass(frozen=True, slots=True)
class SeedResult:
    """What one seed run configured."""

    client_id: UUID
    owner_member_id: UUID
    customer_account_id: UUID
    client_user_id: UUID
    configuration_release_id: UUID
    product_overview_version_id: UUID
    provider_refs_written: int
    release_published: bool
    overview_activated: bool
    offers_enabled: bool
    ceiling_authorized: bool


async def seed(database: Database, inputs: SeedInputs) -> SeedResult:
    """Configure the Shopalytics demo client. Safe to run repeatedly."""
    watched = await discover_watched_channels(inputs)
    auth_user_id = ensure_auth_user(inputs)

    async with database.transaction() as conn:
        client_id = await _upsert_client(conn, client_id=inputs.client_id)
        owner_member_id = await _upsert_owner(
            conn, client_id=client_id, auth_user_id=auth_user_id
        )
        customer_account_id = await _upsert_customer_account(
            conn,
            client_id=client_id,
            external_account_key=CUSTOMER_ACCOUNT_KEY,
            name=CUSTOMER_ACCOUNT_NAME,
        )
        client_user_id = await _upsert_participant(
            conn,
            client_id=client_id,
            customer_account_id=customer_account_id,
            discord_user_id=inputs.maya_discord_user_id,
            display_name=MAYA_DISPLAY_NAME,
        )
        provider_refs_written = await _write_provider_refs(
            conn,
            client_id=client_id,
            client_user_id=client_user_id,
            customer_account_id=customer_account_id,
            inputs=inputs,
            watched=watched,
        )

    overview_id, overview_activated = await _prepare_overview(database, client_id=client_id)

    policy = build_policy(watched=watched, inputs=inputs, client_id=client_id)
    async with database.transaction() as conn:
        release_id, release_published = await _publish_release(
            conn,
            client_id=client_id,
            policy=policy,
            authorized_by_auth_user_id=auth_user_id,
        )

    ceiling_authorized = False
    if policy.outreach_offers.enabled:
        ceiling_authorized = await _authorize_ceiling(
            database,
            client_id=client_id,
            offers=policy.outreach_offers,
            authorized_by_auth_user_id=auth_user_id,
        )

    return SeedResult(
        client_id=client_id,
        owner_member_id=owner_member_id,
        customer_account_id=customer_account_id,
        client_user_id=client_user_id,
        configuration_release_id=release_id,
        product_overview_version_id=overview_id,
        provider_refs_written=provider_refs_written,
        release_published=release_published,
        overview_activated=overview_activated,
        offers_enabled=policy.outreach_offers.enabled,
        ceiling_authorized=ceiling_authorized,
    )


async def _run() -> int:
    """Open the database, seed, and report what the run configured."""
    inputs = read_inputs()
    database = Database(dsn=inputs.database_url, min_size=1, max_size=4)
    await database.open()
    try:
        result = await seed(database, inputs)
        release = await load_active_release(database, client_id=result.client_id)
    finally:
        await database.close()
    print(f"client                    {result.client_id}")
    print(f"owner membership          {result.owner_member_id}")
    print(f"Dudulemon account         {result.customer_account_id}")
    print(f"Maya client user          {result.client_user_id}")
    print(f"provider references       {result.provider_refs_written}")
    print(
        f"Product Overview Version  {result.product_overview_version_id} "
        f"({'activated' if result.overview_activated else 'already active'})"
    )
    print(
        f"configuration release     {release.id} number {release.release_number} "
        f"({'published' if result.release_published else 'unchanged'})"
    )
    print(
        f"Outreach Offers enabled   {result.offers_enabled} "
        f"({'ceiling authorized' if result.ceiling_authorized else 'ceiling unchanged'})"
    )
    if not result.offers_enabled:
        print(
            "  UTRACE_STRIPE_DUDULEMON_CUSTOMER_ID is unset, so no Stripe Customer "
            "mapping was written and rewarded invitations stay disabled."
        )
    return 0


def main() -> int:
    """Entry point."""
    try:
        return asyncio.run(_run())
    except SeedError as error:
        print(f"seed refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
