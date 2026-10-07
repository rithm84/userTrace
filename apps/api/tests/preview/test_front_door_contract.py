"""The two front-door endpoints, and the session tab's claim.

These run through the assembled application against the real database. The route
records here point at a placeholder upstream because what is under test is the
contract the door consumes: the claim's shape, the one-time exchange, the record
the door validates, and the refusals it maps to participant surfaces. The live
tests prove the same records against a real runtime.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest_asyncio
from fastapi import FastAPI
from utrace_contracts.domain.lifecycle import (
    InterviewSessionState,
    ProductSignalState,
)

from app.db.pool import Database
from app.extension.bindings import served_allocation
from app.main import create_app
from app.ports import PortRegistry
from app.preview.pool import mint_secret
from app.preview.recipe import TemplateRecipe
from app.preview.records import PreviewRouteRecord, RouteReadinessState
from app.preview.routing import (
    BASE_PREVIEW_VERSION_ID,
    commit_route,
    mint_origin_host,
    set_route_readiness,
)
from app.settings import Settings
from tests.factories import CaseFixture, create_case, create_interview_session
from tests.preview.conftest import install_template
from tests.supabase_auth import SignedInUser

BASE_URL = "http://control-plane.test"
UPSTREAM = "https://sb-contracttest.vercel.run"


@pytest_asyncio.fixture
async def client(settings: Settings, database: Database) -> AsyncIterator[httpx.AsyncClient]:
    """The assembled application with no provider adapters installed."""
    app: FastAPI = create_app(settings, PortRegistry(voice=None, observation=None, evidence=None))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http:
            yield http


class RoutedSession:
    """A case, its Interview Session, and one committed route revision."""

    __slots__ = ("allocation_id", "case", "route", "session_id")

    def __init__(
        self,
        *,
        case: CaseFixture,
        session_id: UUID,
        allocation_id: UUID,
        route: PreviewRouteRecord,
    ) -> None:
        self.case = case
        self.session_id = session_id
        self.allocation_id = allocation_id
        self.route = route


async def _routed_session(
    database: Database,
    *,
    name: str,
    owner: SignedInUser,
    participant: SignedInUser,
    recipe: TemplateRecipe,
    template_snapshot_id: str,
    template_image: str,
) -> RoutedSession:
    case = await create_case(
        database,
        name=name,
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=owner.auth_user_id,
        participant_auth_user_id=participant.auth_user_id,
    )
    template = await install_template(
        database,
        client_id=case.client_id,
        configuration_release_id=case.configuration_release_id,
        recipe=recipe,
        template_snapshot_id=template_snapshot_id,
        template_image=template_image,
        warm_pool_target=1,
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    allocation_id = await _bound_allocation(
        database, case=case, session_id=session_id, template_id=template.id
    )
    route = await commit_route(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        preview_runtime_allocation_id=allocation_id,
        preview_version_id=None,
        public_preview_host=mint_origin_host(origin_suffix="preview.utrace.dev"),
        upstream_port_url=UPSTREAM,
        application_port=recipe.application_port,
        route_context_signing_secret=mint_secret(),
        ttl_seconds=3600,
    )
    return RoutedSession(
        case=case, session_id=session_id, allocation_id=allocation_id, route=route
    )


async def _bound_allocation(
    database: Database, *, case: CaseFixture, session_id: UUID, template_id: UUID
) -> UUID:
    return await _allocation(
        database,
        case=case,
        session_id=session_id,
        template_id=template_id,
        kind="base",
        pool_role="session_base",
    )


async def _allocation(
    database: Database,
    *,
    case: CaseFixture,
    session_id: UUID,
    template_id: UUID,
    kind: str,
    pool_role: str,
) -> UUID:
    from app.ids import uuid7

    allocation_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.preview_runtime_allocations (
                id, client_id, product_signal_id, interview_session_id,
                allocation_kind, state, seed_manifest_digest, runtime_ref,
                preview_runtime_template_id, pool_role,
                pinned_configuration_release_id, image_digest, recipe_digest,
                fixture_clock, authenticated_identity, sdk_versions,
                application_port, handoff_secret, route_context_signing_secret,
                fencing_token
            )
            select %s, templates.client_id, %s, %s, %s, 'bound',
                   templates.seed_manifest_digest, %s, templates.id, %s,
                   templates.pinned_configuration_release_id, templates.image_digest,
                   templates.recipe_digest, templates.fixture_clock,
                   templates.authenticated_identity, templates.sdk_versions,
                   templates.application_port, %s, %s, utrace.next_fencing_token()
            from public.preview_runtime_templates as templates
            where templates.id = %s
            """,
            (
                allocation_id,
                case.product_signal_id,
                session_id,
                kind,
                f"test-runtime-{allocation_id}",
                pool_role,
                mint_secret(),
                mint_secret(),
                template_id,
            ),
        )
    return allocation_id


def _service(settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.front_door_service_token}"}


def _participant(user: SignedInUser) -> dict[str, str]:
    return {"Authorization": f"Bearer {user.access_token}"}


async def _claim_code(
    client: httpx.AsyncClient, *, session: RoutedSession, participant: SignedInUser
) -> str:
    response = await client.post(
        f"/v1/sessions/{session.session_id}/preview-handoff",
        headers=_participant(participant),
    )
    assert response.status_code == 200, response.text
    url = response.json()["front_door_url"]
    expected = f"https://{session.route.public_preview_host}/__utrace/claim?code="
    assert url.startswith(expected), url
    return url.removeprefix(expected)


async def test_the_claim_opens_the_sessions_current_route(
    client: httpx.AsyncClient,
    database: Database,
    settings: Settings,
    auth_users: dict[str, SignedInUser],
    recipe: TemplateRecipe,
    template_snapshot_id: str,
    template_image: str,
) -> None:
    """The session tab hands the participant a one-time claim for its own origin."""
    session = await _routed_session(
        database,
        name="front-door-claim",
        owner=auth_users["owner_a"],
        participant=auth_users["participant"],
        recipe=recipe,
        template_snapshot_id=template_snapshot_id,
        template_image=template_image,
    )
    code = await _claim_code(
        client, session=session, participant=auth_users["participant"]
    )

    exchanged = await client.post(
        f"/v1/preview/handoffs/{code}/exchange",
        headers=_service(settings),
        json={
            "schema_version": "utrace_preview_handoff_exchange_request_v1",
            "public_preview_host": session.route.public_preview_host,
        },
    )
    assert exchanged.status_code == 200, exchanged.text
    body = exchanged.json()
    assert body["schema_version"] == "utrace_preview_handoff_exchange_v1"
    assert body["interview_session_id"] == str(session.session_id)
    assert body["public_preview_host"] == session.route.public_preview_host
    credential = body["session_credential"]["value"]

    replayed = await client.post(
        f"/v1/preview/handoffs/{code}/exchange",
        headers=_service(settings),
        json={
            "schema_version": "utrace_preview_handoff_exchange_request_v1",
            "public_preview_host": session.route.public_preview_host,
        },
    )
    assert replayed.status_code == 401
    assert replayed.json() == {
        "schema_version": "utrace_preview_denial_v1",
        "reason": "unknown_handoff_code",
    }

    resolved = await client.get(
        f"/v1/preview/routes/{session.route.public_preview_host}",
        headers={
            **_service(settings),
            "x-utrace-session-credential": credential,
        },
    )
    assert resolved.status_code == 200, resolved.text
    record = resolved.json()
    assert record["schema_version"] == "utrace_preview_route_record_v1"
    assert record["route_revision"] == 1
    assert record["readiness_state"] == "ready"
    assert record["upstream"] == {
        "port_url": UPSTREAM,
        "application_port": recipe.application_port,
    }
    assert record["preview_version_id"] == str(BASE_PREVIEW_VERSION_ID)
    assert record["route_context_signing_secret"]
    # No telemetry-ingestion adapter has activated this runtime, so the door has
    # no entry token to attach yet.
    assert record["preview_entry_token"] is None

    sign_in = await client.post(
        f"/v1/preview/routes/{session.route.public_preview_host}/product-sign-in",
        headers={**_service(settings), "x-utrace-session-credential": credential},
    )
    assert sign_in.status_code == 200, sign_in.text
    minted = sign_in.json()
    assert minted["schema_version"] == "utrace_preview_product_sign_in_v1"
    assert minted["path"] == recipe.handoff_path
    # The token is signed with the routed runtime's own secret, for this origin,
    # as the identity the allocation was prepared with.
    version, payload, signature = minted["token"].split(".")
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select handoff_secret, authenticated_identity
            from public.preview_runtime_allocations where id = %s
            """,
            (session.allocation_id,),
        )
        allocation = await cur.fetchone()
    assert allocation is not None
    expected = hmac.new(
        str(allocation["handoff_secret"]).encode(), payload.encode(), hashlib.sha256
    ).digest()
    assert version == "v1"
    assert base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)) == expected
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims["audience"] == session.route.public_preview_host
    assert claims["subject_email"] == allocation["authenticated_identity"]

    unauthorized = await client.post(
        f"/v1/preview/routes/{session.route.public_preview_host}/product-sign-in",
        headers={**_service(settings), "x-utrace-session-credential": code},
    )
    assert unauthorized.status_code == 401


async def test_a_claim_code_is_not_a_session_credential(
    client: httpx.AsyncClient,
    database: Database,
    settings: Settings,
    auth_users: dict[str, SignedInUser],
    recipe: TemplateRecipe,
    template_snapshot_id: str,
    template_image: str,
) -> None:
    """Route resolution refuses the one-time code and a credential for another origin."""
    session = await _routed_session(
        database,
        name="front-door-credential",
        owner=auth_users["owner_a"],
        participant=auth_users["participant"],
        recipe=recipe,
        template_snapshot_id=template_snapshot_id,
        template_image=template_image,
    )
    code = await _claim_code(
        client, session=session, participant=auth_users["participant"]
    )
    as_credential = await client.get(
        f"/v1/preview/routes/{session.route.public_preview_host}",
        headers={**_service(settings), "x-utrace-session-credential": code},
    )
    assert as_credential.status_code == 401
    assert as_credential.json()["reason"] == "unauthorized_session"

    bare = await client.get(
        f"/v1/preview/routes/{session.route.public_preview_host}",
        headers=_service(settings),
    )
    assert bare.status_code == 401


async def test_an_advanced_route_supersedes_the_previous_revision(
    client: httpx.AsyncClient,
    database: Database,
    settings: Settings,
    auth_users: dict[str, SignedInUser],
    recipe: TemplateRecipe,
    template_snapshot_id: str,
    template_image: str,
) -> None:
    """One origin resolves exactly one revision, and the superseded one is gone."""
    session = await _routed_session(
        database,
        name="front-door-advance",
        owner=auth_users["owner_a"],
        participant=auth_users["participant"],
        recipe=recipe,
        template_snapshot_id=template_snapshot_id,
        template_image=template_image,
    )
    code = await _claim_code(
        client, session=session, participant=auth_users["participant"]
    )
    exchanged = await client.post(
        f"/v1/preview/handoffs/{code}/exchange",
        headers=_service(settings),
        json={
            "schema_version": "utrace_preview_handoff_exchange_request_v1",
            "public_preview_host": session.route.public_preview_host,
        },
    )
    credential = exchanged.json()["session_credential"]["value"]

    second_secret = mint_secret()
    await commit_route(
        database,
        client_id=session.case.client_id,
        product_signal_id=session.case.product_signal_id,
        interview_session_id=session.session_id,
        preview_runtime_allocation_id=session.allocation_id,
        preview_version_id=None,
        public_preview_host=session.route.public_preview_host,
        upstream_port_url="https://sb-contracttest-second.vercel.run",
        application_port=recipe.application_port,
        route_context_signing_secret=second_secret,
        ttl_seconds=3600,
    )
    resolved = await client.get(
        f"/v1/preview/routes/{session.route.public_preview_host}",
        headers={**_service(settings), "x-utrace-session-credential": credential},
    )
    assert resolved.status_code == 200
    record = resolved.json()
    assert record["route_revision"] == 2
    assert record["route_context_signing_secret"] == second_secret

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select route_revision, state from public.preview_routes
            where public_preview_host = %s order by route_revision
            """,
            (session.route.public_preview_host,),
        )
        rows = await cur.fetchall()
    assert [(row["route_revision"], row["state"]) for row in rows] == [
        (1, "superseded"),
        (2, "active"),
    ]


async def test_an_advance_to_a_candidate_moves_the_origin_and_its_binding_there(
    database: Database,
    auth_users: dict[str, SignedInUser],
    recipe: TemplateRecipe,
    template_snapshot_id: str,
    template_image: str,
) -> None:
    """A document that activates after the advance, and its rebind, are the candidate's."""
    session = await _routed_session(
        database,
        name="front-door-candidate",
        owner=auth_users["owner_a"],
        participant=auth_users["participant"],
        recipe=recipe,
        template_snapshot_id=template_snapshot_id,
        template_image=template_image,
    )
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select preview_runtime_template_id from public.preview_runtime_allocations "
            "where id = %s",
            (session.allocation_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    candidate_id = await _allocation(
        database,
        case=session.case,
        session_id=session.session_id,
        template_id=row["preview_runtime_template_id"],
        kind="candidate",
        pool_role="candidate",
    )

    await commit_route(
        database,
        client_id=session.case.client_id,
        product_signal_id=session.case.product_signal_id,
        interview_session_id=session.session_id,
        preview_runtime_allocation_id=candidate_id,
        preview_version_id=None,
        public_preview_host=session.route.public_preview_host,
        upstream_port_url="https://sb-contracttest-candidate.vercel.run",
        application_port=recipe.application_port,
        route_context_signing_secret=mint_secret(),
        ttl_seconds=3600,
    )

    async with database.connection() as conn:
        served = await served_allocation(conn, interview_session_id=session.session_id)
        async with conn.cursor() as cur:
            await cur.execute(
                "select id, origin_host from public.preview_runtime_allocations "
                "where id = any(%s)",
                ([session.allocation_id, candidate_id],),
            )
            hosts = {row["id"]: row["origin_host"] for row in await cur.fetchall()}
    assert served == candidate_id
    # Browser activation resolves an origin to the allocation that holds it.
    assert hosts == {session.allocation_id: None, candidate_id: session.route.public_preview_host}


async def test_withdrawn_readiness_and_a_closed_session_are_distinguishable(
    client: httpx.AsyncClient,
    database: Database,
    settings: Settings,
    auth_users: dict[str, SignedInUser],
    recipe: TemplateRecipe,
    template_snapshot_id: str,
    template_image: str,
) -> None:
    """A restart shows the reconnecting surface; an ended session shows the ended one."""
    session = await _routed_session(
        database,
        name="front-door-readiness",
        owner=auth_users["owner_a"],
        participant=auth_users["participant"],
        recipe=recipe,
        template_snapshot_id=template_snapshot_id,
        template_image=template_image,
    )
    code = await _claim_code(
        client, session=session, participant=auth_users["participant"]
    )
    exchanged = await client.post(
        f"/v1/preview/handoffs/{code}/exchange",
        headers=_service(settings),
        json={
            "schema_version": "utrace_preview_handoff_exchange_request_v1",
            "public_preview_host": session.route.public_preview_host,
        },
    )
    credential = exchanged.json()["session_credential"]["value"]

    await set_route_readiness(
        database,
        preview_route_id=session.route.preview_route_id,
        readiness_state=RouteReadinessState.RECONNECTING,
        reason="runtime_restart",
    )
    reconnecting = await client.get(
        f"/v1/preview/routes/{session.route.public_preview_host}",
        headers={**_service(settings), "x-utrace-session-credential": credential},
    )
    assert reconnecting.status_code == 200
    assert reconnecting.json()["readiness_state"] == "reconnecting"

    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.interview_sessions
            set state = %s, closed_at = now(), revision = revision + 1
            where id = %s
            """,
            (InterviewSessionState.CLOSED.value, session.session_id),
        )
    ended = await client.get(
        f"/v1/preview/routes/{session.route.public_preview_host}",
        headers={**_service(settings), "x-utrace-session-credential": credential},
    )
    assert ended.status_code == 410
    assert ended.json()["reason"] == "session_ended"


async def test_a_session_without_a_route_cannot_be_claimed(
    client: httpx.AsyncClient,
    database: Database,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A preview that is not routed yet refuses rather than handing out a dead URL."""
    case = await create_case(
        database,
        name="front-door-unrouted",
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=auth_users["participant"].auth_user_id,
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    response = await client.post(
        f"/v1/sessions/{session_id}/preview-handoff",
        headers=_participant(auth_users["participant"]),
    )
    assert response.status_code == 409
    assert "committed preview route" in response.json()["detail"]
