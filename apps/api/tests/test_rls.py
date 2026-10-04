"""Row-level security isolation between two clients.

These go through the Data API with the publishable key and two real signed-in
users, because that is the path a browser takes. Authentication alone grants
nothing: only an active ``client_members`` row for the matching ``client_id``
makes a row visible, and a participant sees only their own session projection.
"""

from __future__ import annotations

from uuid import UUID

import httpx
from utrace_contracts.domain.lifecycle import (
    ClientMemberRole,
    InterviewSessionState,
    MembershipStatus,
    ProductSignalState,
)

from app.db.pool import Database
from app.ids import uuid7
from app.settings import Settings
from tests.factories import CaseFixture, create_case, create_interview_session
from tests.supabase_auth import SignedInUser

_TIMEOUT = 30.0


async def _grant_membership(
    database: Database,
    *,
    client_id: UUID,
    auth_user_id: UUID,
    role: ClientMemberRole,
    status: MembershipStatus,
) -> UUID:
    member_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.client_members (
                id, client_id, auth_user_id, role, status
            )
            values (%s, %s, %s, %s, %s)
            """,
            (member_id, client_id, auth_user_id, role.value, status.value),
        )
    return member_id


async def _rest(
    settings: Settings, *, token: str | None, path: str, params: dict[str, str]
) -> list[dict[str, object]]:
    """Read one table through the Data API exactly as a browser would."""
    headers = {"apikey": settings.supabase_publishable_key}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"{settings.supabase_url.rstrip('/')}/rest/v1/{path}",
            params=params,
            headers=headers,
        )
    if response.status_code >= 300:
        raise AssertionError(
            f"Data API read of {path} failed with {response.status_code}: {response.text}"
        )
    body = response.json()
    assert isinstance(body, list)
    return body


async def _two_clients(
    database: Database, auth_users: dict[str, SignedInUser]
) -> tuple[CaseFixture, CaseFixture]:
    first = await create_case(
        database,
        name="rls-client-a",
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=auth_users["participant"].auth_user_id,
    )
    second = await create_case(
        database,
        name="rls-client-b",
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_b"].auth_user_id,
        participant_auth_user_id=None,
    )
    await _grant_membership(
        database,
        client_id=first.client_id,
        auth_user_id=auth_users["owner_a"].auth_user_id,
        role=ClientMemberRole.OWNER,
        status=MembershipStatus.ACTIVE,
    )
    await _grant_membership(
        database,
        client_id=second.client_id,
        auth_user_id=auth_users["owner_b"].auth_user_id,
        role=ClientMemberRole.OWNER,
        status=MembershipStatus.ACTIVE,
    )
    return first, second


async def test_each_client_sees_only_its_own_cases(
    database: Database, settings: Settings, auth_users: dict[str, SignedInUser]
) -> None:
    """A signed-in owner reads their tenant's Product Signals and no others."""
    first, second = await _two_clients(database, auth_users)

    owner_a_rows = await _rest(
        settings,
        token=auth_users["owner_a"].access_token,
        path="product_signals",
        params={"select": "id,client_id"},
    )
    assert {row["id"] for row in owner_a_rows} == {str(first.product_signal_id)}

    owner_b_rows = await _rest(
        settings,
        token=auth_users["owner_b"].access_token,
        path="product_signals",
        params={"select": "id,client_id"},
    )
    assert {row["id"] for row in owner_b_rows} == {str(second.product_signal_id)}


async def test_an_explicit_cross_tenant_filter_still_returns_nothing(
    database: Database, settings: Settings, auth_users: dict[str, SignedInUser]
) -> None:
    """Asking for the other tenant's rows by identifier does not widen access."""
    _, second = await _two_clients(database, auth_users)
    rows = await _rest(
        settings,
        token=auth_users["owner_a"].access_token,
        path="product_signals",
        params={"select": "id", "client_id": f"eq.{second.client_id}"},
    )
    assert rows == []
    rows = await _rest(
        settings,
        token=auth_users["owner_a"].access_token,
        path="product_signals",
        params={"select": "id", "id": f"eq.{second.product_signal_id}"},
    )
    assert rows == []


async def test_a_revoked_membership_loses_access_immediately(
    database: Database, settings: Settings, auth_users: dict[str, SignedInUser]
) -> None:
    """Deactivating the membership row removes access without touching the token."""
    first, _ = await _two_clients(database, auth_users)
    assert (
        await _rest(
            settings,
            token=auth_users["owner_a"].access_token,
            path="product_signals",
            params={"select": "id"},
        )
        != []
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.client_members set status = %s where client_id = %s",
            (MembershipStatus.REVOKED.value, first.client_id),
        )
    assert (
        await _rest(
            settings,
            token=auth_users["owner_a"].access_token,
            path="product_signals",
            params={"select": "id"},
        )
        == []
    )


async def test_authenticated_without_membership_reads_nothing(
    database: Database, settings: Settings, auth_users: dict[str, SignedInUser]
) -> None:
    """`TO authenticated` alone is not authorization: the participant sees no cases."""
    await _two_clients(database, auth_users)
    assert (
        await _rest(
            settings,
            token=auth_users["participant"].access_token,
            path="product_signals",
            params={"select": "id"},
        )
        == []
    )
    assert (
        await _rest(
            settings,
            token=auth_users["participant"].access_token,
            path="case_files",
            params={"select": "id"},
        )
        == []
    )


async def test_the_participant_projection_is_scoped_to_their_own_identity(
    database: Database, settings: Settings, auth_users: dict[str, SignedInUser]
) -> None:
    """A participant reads their own session and no other client's sessions."""
    first, second = await _two_clients(database, auth_users)
    mine = await create_interview_session(
        database, case=first, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    theirs = await create_interview_session(
        database, case=second, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    rows = await _rest(
        settings,
        token=auth_users["participant"].access_token,
        path="interview_sessions",
        params={"select": "id,client_id"},
    )
    assert {row["id"] for row in rows} == {str(mine)}
    assert str(theirs) not in {row["id"] for row in rows}

    # Staff of the same client see the session too; staff of the other do not.
    owner_a_rows = await _rest(
        settings,
        token=auth_users["owner_a"].access_token,
        path="interview_sessions",
        params={"select": "id"},
    )
    assert {row["id"] for row in owner_a_rows} == {str(mine)}
    owner_b_rows = await _rest(
        settings,
        token=auth_users["owner_b"].access_token,
        path="interview_sessions",
        params={"select": "id"},
    )
    assert {row["id"] for row in owner_b_rows} == {str(theirs)}


async def test_internal_tables_are_unreadable_through_the_data_api(
    database: Database, settings: Settings, auth_users: dict[str, SignedInUser]
) -> None:
    """Execution and audit tables grant nothing to a browser role."""
    await _two_clients(database, auth_users)
    for table in ("events", "jobs", "commands", "outbox", "inbox", "audit_records"):
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(
                f"{settings.supabase_url.rstrip('/')}/rest/v1/{table}",
                params={"select": "*"},
                headers={
                    "apikey": settings.supabase_publishable_key,
                    "Authorization": f"Bearer {auth_users['owner_a'].access_token}",
                },
            )
        if response.status_code < 300:
            assert response.json() == [], f"{table} leaked rows to a client role"
        else:
            assert response.status_code in {401, 403, 404}, (
                f"{table} returned an unexpected status {response.status_code}"
            )


async def test_the_anonymous_key_alone_reads_nothing(
    database: Database, settings: Settings, auth_users: dict[str, SignedInUser]
) -> None:
    """An unauthenticated caller with the publishable key sees no tenant data."""
    await _two_clients(database, auth_users)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"{settings.supabase_url.rstrip('/')}/rest/v1/product_signals",
            params={"select": "id"},
            headers={"apikey": settings.supabase_publishable_key},
        )
    if response.status_code < 300:
        assert response.json() == []
    else:
        assert response.status_code in {401, 403}


async def test_only_published_case_files_reach_the_dashboard(
    database: Database, settings: Settings, auth_users: dict[str, SignedInUser]
) -> None:
    """A Case File is invisible to staff until it is sealed and published."""
    first, _ = await _two_clients(database, auth_users)
    case_file_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.case_files (
                id, client_id, product_signal_id, variant, stage
            )
            values (%s, %s, %s, 'blocked', 'draft')
            """,
            (case_file_id, first.client_id, first.product_signal_id),
        )
    assert (
        await _rest(
            settings,
            token=auth_users["owner_a"].access_token,
            path="case_files",
            params={"select": "id"},
        )
        == []
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.case_files set stage = 'published', core_digest = %s, "
            "core_canonical = %s, published_at = now() where id = %s",
            ("sha256:core", '{"summary":"published"}', case_file_id),
        )
    rows = await _rest(
        settings,
        token=auth_users["owner_a"].access_token,
        path="case_files",
        params={"select": "id"},
    )
    assert {row["id"] for row in rows} == {str(case_file_id)}
