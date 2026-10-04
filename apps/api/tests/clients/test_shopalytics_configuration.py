"""The Shopalytics client configuration seed and its Product Overview.

The seed runs against the real Supabase Auth admin API and the real Discord
guild, then runs a second time to prove it rebinds rather than duplicating, and
the Product Overview it activated is the one routing and interviews read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from app.clients.configuration import load_active_release, release_digest
from app.clients.overview import load_active_overview
from app.clients.shopalytics_overview import (
    SHOPALYTICS_MANIFEST_HASH,
    SHOPALYTICS_OVERVIEW,
)
from app.clients.shopalytics_release import SHOPALYTICS_REPOSITORY, SHOPALYTICS_REQUIRED_CHECKS
from app.db.pool import Database
from app.db.rows import as_int
from app.gate.scope import DependencyPolicy
from app.ingestion.server_activation import verify_signed_manifest
from app.ingestion.signing import b64url_decode, b64url_encode, public_key_b64url
from app.preview.recipe import TemplateRecipe
from scripts.seed_shopalytics_client import (
    DASHBOARD_BASE_URL,
    OFFER_AMOUNT_MINOR_UNITS,
    SeedResult,
    read_inputs,
    seed,
)
from tests.conftest import require_env

RELEASE_KEY_ID = "seed-test-release"


@pytest_asyncio.fixture
async def seeded(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[SeedResult]:
    """Seed the Shopalytics client once.

    The run signs with a release key pair of its own, so the published manifest
    can be verified here without the deployment's private key.
    """
    for name in (
        "UTRACE_SUPABASE_SECRET_KEY",
        "UTRACE_DISCORD_BOT_TOKEN",
        "UTRACE_DISCORD_SERVER_ID",
        "UTRACE_DEMO_MAYA_DISCORD_USER_ID",
        "UTRACE_DEMO_STAFF_EMAIL",
        "UTRACE_DEMO_STAFF_PASSWORD",
        "UTRACE_PREVIEW_TEMPLATE_RECIPE",
        "UTRACE_IMPLEMENTATION_WORKSPACE_SNAPSHOT_ID",
        "UTRACE_PREVIEW_FIXTURE_REPOSITORY",
    ):
        require_env(name)
    release_key = Ed25519PrivateKey.generate()
    monkeypatch.setenv(
        "UTRACE_SERVER_MANIFEST_SIGNING_KEY",
        b64url_encode(release_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())),
    )
    monkeypatch.setenv(
        "UTRACE_SERVER_MANIFEST_TRUST_ANCHOR", public_key_b64url(release_key.public_key())
    )
    monkeypatch.setenv("UTRACE_SERVER_MANIFEST_TRUST_ANCHOR_KEY_ID", RELEASE_KEY_ID)
    inputs = read_inputs()
    yield await seed(database, inputs)


async def test_the_seed_is_idempotent(
    database: Database, seeded: SeedResult
) -> None:
    assert seeded.release_published
    assert seeded.overview_activated
    assert seeded.ceiling_authorized

    second = await seed(database, read_inputs())
    assert second.client_id == seeded.client_id
    assert second.owner_member_id == seeded.owner_member_id
    assert second.customer_account_id == seeded.customer_account_id
    assert second.client_user_id == seeded.client_user_id
    assert second.configuration_release_id == seeded.configuration_release_id
    assert second.product_overview_version_id == seeded.product_overview_version_id
    assert not second.release_published
    assert not second.overview_activated
    assert not second.ceiling_authorized

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as total from public.configuration_releases "
            "where client_id = %s",
            (seeded.client_id,),
        )
        releases = await cur.fetchone()
        await cur.execute(
            "select count(*) as total from public.clients where name = 'Shopalytics'"
        )
        clients = await cur.fetchone()
        await cur.execute(
            "select count(*) as total from public.provider_refs where client_id = %s",
            (seeded.client_id,),
        )
        refs = await cur.fetchone()
        await cur.execute(
            "select count(*) as total from public.offer_budgets where client_id = %s",
            (seeded.client_id,),
        )
        budgets = await cur.fetchone()
    assert releases is not None
    assert as_int(releases, "total") == 1
    assert clients is not None
    assert as_int(clients, "total") == 1
    assert refs is not None
    assert as_int(refs, "total") == seeded.provider_refs_written
    assert budgets is not None
    assert as_int(budgets, "total") == 1


async def test_the_release_pins_the_demo_configuration(
    database: Database, seeded: SeedResult
) -> None:
    release = await load_active_release(database, client_id=seeded.client_id)
    policy = release.policy

    assert release.release_number == 1
    assert release.digest == release_digest(policy)
    assert policy.customer_surface.installation_ref == require_env(
        "UTRACE_DISCORD_SERVER_ID"
    )
    assert {channel.name for channel in policy.customer_surface.watched_channels} == {
        "support",
        "growth-insights",
        "feature-requests",
    }
    assert policy.customer_surface.watches(
        require_env("UTRACE_DISCORD_FEATURE_REQUEST_CHANNEL_ID")
    )

    implementation = policy.implementation
    assert implementation.repository_ref == SHOPALYTICS_REPOSITORY
    assert implementation.required_checks == SHOPALYTICS_REQUIRED_CHECKS
    assert implementation.dependency_policy is DependencyPolicy.NONE
    assert implementation.live_implementation_allowed is True
    assert (
        implementation.base_revision
        == TemplateRecipe.load(Path(require_env("UTRACE_PREVIEW_TEMPLATE_RECIPE"))).fixture_revision
    )
    assert policy.dashboard_base_url == DASHBOARD_BASE_URL

    # The server manifest is bound to this client and verifies exactly as
    # activation will check it.
    manifest = verify_signed_manifest(
        policy.server_instrumentation_manifest,
        trust_anchor=b64url_decode(
            require_env("UTRACE_SERVER_MANIFEST_TRUST_ANCHOR"), "trust anchor"
        ),
        trust_anchor_key_id=RELEASE_KEY_ID,
    )
    assert manifest["client_id"] == str(seeded.client_id)
    assert policy.annotation_manifest["manifest_version"]

    assert policy.data_seeding.manifest_hash == SHOPALYTICS_MANIFEST_HASH
    assert policy.data_seeding.fixture_clock == "2026-09-14T09:00:00Z"
    assert policy.outreach_offers.currency == "USD"
    assert policy.outreach_offers.fixed_amount_minor_units == OFFER_AMOUNT_MINOR_UNITS
    assert policy.contact_budget.per_person > 0
    assert policy.product_overview.content_digest == (
        SHOPALYTICS_OVERVIEW.content_digest()
    )


async def test_the_product_overview_is_active(database: Database, seeded: SeedResult) -> None:
    active = await load_active_overview(database, client_id=seeded.client_id)
    assert active.id == seeded.product_overview_version_id
    assert active.content_digest == SHOPALYTICS_OVERVIEW.content_digest()
    assert active.overview == SHOPALYTICS_OVERVIEW
