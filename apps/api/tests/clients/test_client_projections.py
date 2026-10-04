"""The client, configuration, and customer-account projections.

These three reads are what a caller holding nothing but a Supabase Auth session
needs to find its client, check what the active configuration release pins, and
see which accounts are eligible and how they map to billing. Every request goes
through the assembled application with a real access token against a real
configured client, so the membership join and the typed policy body are both
exercised rather than assumed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from utrace_contracts.domain.lifecycle import ClientMemberRole

from app.db.pool import Database
from app.main import create_app
from app.ports import PortRegistry
from app.settings import Settings
from tests.conftest import require_env
from tests.supabase_auth import SignedInUser
from tests.workstream import (
    STRIPE_CUSTOMER_REF,
    WorkstreamFixture,
    create_workstream_client,
)

BASE_URL = "http://control-plane.test"
DUDULEMON_KEY = "dudulemon"


@pytest_asyncio.fixture
async def client(settings: Settings, database: Database) -> AsyncIterator[httpx.AsyncClient]:
    """The assembled application with no provider adapters installed."""
    app: FastAPI = create_app(
        settings,
        PortRegistry(voice=None, receipts=None, observation=None, evidence=None),
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http:
            yield http


@pytest_asyncio.fixture
async def fixture(
    database: Database, auth_users: dict[str, SignedInUser]
) -> WorkstreamFixture:
    """One configured client whose owner is ``owner_a``."""
    for name in (
        "UTRACE_DISCORD_SERVER_ID",
        "UTRACE_DISCORD_FEATURE_REQUEST_CHANNEL_ID",
        "UTRACE_DEMO_MAYA_DISCORD_USER_ID",
    ):
        require_env(name)
    participant = auth_users["participant"]
    return await create_workstream_client(
        database,
        owner_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_sign_in=(
            participant.identity_provider,
            participant.identity_provider_id,
        ),
        offers_enabled=True,
    )


def _staff(user: SignedInUser) -> dict[str, str]:
    """Return the Authorization header for one signed-in identity."""
    return {"Authorization": f"Bearer {user.access_token}"}


async def test_the_client_list_returns_only_the_callers_own_memberships(
    client: httpx.AsyncClient,
    fixture: WorkstreamFixture,
    auth_users: dict[str, SignedInUser],
) -> None:
    mine = await client.get("/v1/clients", headers=_staff(auth_users["owner_a"]))
    assert mine.status_code == 200
    roles = {entry["client"]["id"]: entry["membership"]["role"] for entry in mine.json()["clients"]}
    assert roles[str(fixture.client_id)] == ClientMemberRole.OWNER.value

    stranger = await client.get("/v1/clients", headers=_staff(auth_users["owner_b"]))
    assert stranger.status_code == 200
    assert str(fixture.client_id) not in {
        entry["client"]["id"] for entry in stranger.json()["clients"]
    }


async def test_the_client_list_refuses_an_unauthenticated_request(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/v1/clients")
    assert response.status_code == 403
    assert response.json()["error"] == "AuthorizationError"


async def test_the_configuration_read_returns_the_pinned_release(
    client: httpx.AsyncClient,
    fixture: WorkstreamFixture,
    auth_users: dict[str, SignedInUser],
) -> None:
    response = await client.get(
        f"/v1/clients/{fixture.client_id}/configuration", headers=_staff(auth_users["owner_a"])
    )
    assert response.status_code == 200
    release = response.json()["release"]
    assert release["id"] == str(fixture.release.id)
    assert release["release_number"] == fixture.release.release_number
    policy = release["policy"]
    assert policy["customer_surface"]["installation_ref"] == fixture.guild_id
    assert [channel["channel_ref"] for channel in policy["customer_surface"]["watched_channels"]] == [
        fixture.channel_id
    ]
    assert policy["implementation"]["repository_ref"] == "ShopalyticsStore/shopalytics"
    assert policy["implementation"]["required_checks"]
    assert policy["outreach_offers"]["fixed_amount_minor_units"] == 25_000
    assert policy["data_seeding"]["environment_template_id"]


async def test_the_configuration_read_refuses_a_nonmember(
    client: httpx.AsyncClient,
    fixture: WorkstreamFixture,
    auth_users: dict[str, SignedInUser],
) -> None:
    response = await client.get(
        f"/v1/clients/{fixture.client_id}/configuration", headers=_staff(auth_users["owner_b"])
    )
    assert response.status_code == 403
    assert response.json()["error"] == "AuthorizationError"


async def test_the_customer_account_read_carries_the_billing_mapping(
    client: httpx.AsyncClient,
    fixture: WorkstreamFixture,
    auth_users: dict[str, SignedInUser],
) -> None:
    response = await client.get(
        f"/v1/clients/{fixture.client_id}/customer-accounts",
        headers=_staff(auth_users["owner_a"]),
    )
    assert response.status_code == 200
    accounts = {
        account["external_account_key"]: account
        for account in response.json()["customer_accounts"]
    }
    assert accounts[DUDULEMON_KEY]["id"] == str(fixture.dudulemon_account_id)
    assert accounts[DUDULEMON_KEY]["stripe_customer_ref"] == STRIPE_CUSTOMER_REF


async def test_the_customer_account_read_refuses_a_nonmember(
    client: httpx.AsyncClient,
    fixture: WorkstreamFixture,
    auth_users: dict[str, SignedInUser],
) -> None:
    response = await client.get(
        f"/v1/clients/{fixture.client_id}/customer-accounts",
        headers=_staff(auth_users["owner_b"]),
    )
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["configuration", "customer-accounts"])
async def test_an_unknown_client_is_refused_rather_than_disclosed(
    client: httpx.AsyncClient,
    fixture: WorkstreamFixture,
    auth_users: dict[str, SignedInUser],
    path: str,
) -> None:
    unknown = fixture.dudulemon_account_id
    response = await client.get(
        f"/v1/clients/{unknown}/{path}", headers=_staff(auth_users["owner_a"])
    )
    assert response.status_code == 403
