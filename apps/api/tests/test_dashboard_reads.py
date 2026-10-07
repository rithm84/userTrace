"""The read models the authenticated dashboard adds to the kernel's records.

Requests go through the assembled application with real Supabase access tokens
against the real database. These cover what a durable record does not itself
carry: the established concern of a case, the
outreach it sent, the sessions and candidates it produced, the preview runtimes
it claimed and gave back, the audited client stop, the sign-in email of a staff
member, the active configuration release, and the resolution of the opaque
client-scoped references a published Case File names people by.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest_asyncio
from fastapi import FastAPI
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    ClientMemberRole,
    InterviewSessionState,
    MembershipStatus,
    OutreachAttemptState,
    ProductSignalState,
)

from app.clients.configuration import release_digest
from app.db.kernel import append_fact
from app.db.pool import Database
from app.envelope import Actor, AuditIntent, EventAppend, StreamRef
from app.ids import uuid7
from app.main import create_app
from app.outreach.invitations import link_supersession, revoke_live_invitation
from app.ports import PortRegistry
from app.settings import Settings
from tests.factories import (
    CaseFixture,
    create_case,
    create_interview_session,
    create_invitation,
    create_outreach_attempt,
)
from tests.interview.fixtures import create_preview_allocation
from tests.supabase_auth import SignedInUser
from tests.workstream import fixture_policy

BASE_URL = "http://control-plane.test"
PAIN_POINT = "Rebuilding the same conversion filters every Monday"
VALIDATION_CRITERION = "A saved view restores every filter after a refresh"


@pytest_asyncio.fixture
async def client(
    settings: Settings, database: Database
) -> AsyncIterator[httpx.AsyncClient]:
    """The assembled application with no provider adapters installed."""
    app: FastAPI = create_app(
        settings,
        PortRegistry(voice=None, observation=None, evidence=None),
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http:
            yield http


def _staff(user: SignedInUser) -> dict[str, str]:
    return {"Authorization": f"Bearer {user.access_token}"}


async def _grant(
    database: Database, *, client_id: UUID, user: SignedInUser, role: ClientMemberRole
) -> UUID:
    member_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.client_members (id, client_id, auth_user_id, role, status)
            values (%s, %s, %s, %s, %s)
            """,
            (
                member_id,
                client_id,
                user.auth_user_id,
                role.value,
                MembershipStatus.ACTIVE.value,
            ),
        )
    return member_id


async def _case(
    database: Database, owner: SignedInUser, name: str, state: ProductSignalState
) -> CaseFixture:
    return await create_case(
        database,
        name=name,
        state=state,
        authorizing_auth_user_id=owner.auth_user_id,
        participant_auth_user_id=None,
    )


async def _record_concern(database: Database, case: CaseFixture) -> None:
    """Record the interview-established concern revision of one case."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.product_signal_concerns (
                id, client_id, product_signal_id, concern_revision, origin,
                pain_point, validation_criterion, user_wording, unknowns
            )
            values (%s, %s, %s, 0, 'routing_inference', %s, %s, %s, %s)
            """,
            (
                uuid7(),
                case.client_id,
                case.product_signal_id,
                "an inferred first reading",
                "an inferred criterion",
                Jsonb([]),
                Jsonb([]),
            ),
        )
        await cur.execute(
            """
            insert into public.product_signal_concerns (
                id, client_id, product_signal_id, concern_revision, origin,
                pain_point, validation_criterion, user_wording, unknowns
            )
            values (%s, %s, %s, 1, 'interview_established', %s, %s, %s, %s)
            """,
            (
                uuid7(),
                case.client_id,
                case.product_signal_id,
                PAIN_POINT,
                VALIDATION_CRITERION,
                Jsonb(["I keep rebuilding the same filters"]),
                Jsonb([]),
            ),
        )


async def test_a_case_overview_carries_its_concern(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The list and the detail both read the current concern revision."""
    case = await _case(
        database, auth_users["owner_a"], "dash-overview", ProductSignalState.INTERVIEWING
    )
    await _grant(
        database,
        client_id=case.client_id,
        user=auth_users["owner_a"],
        role=ClientMemberRole.OWNER,
    )
    await _record_concern(database, case)
    headers = _staff(auth_users["owner_a"])

    listed = await client.get(f"/v1/clients/{case.client_id}/cases", headers=headers)
    assert listed.status_code == 200
    overview = listed.json()["overviews"][0]
    assert overview["product_signal_id"] == str(case.product_signal_id)
    assert overview["concern"]["pain_point"] == PAIN_POINT
    assert overview["concern"]["concern_revision"] == 1
    assert overview["concern"]["origin"] == "interview_established"
    assert overview["originating_user_ref"] == f"client_user:{case.client_user_id}"
    assert (
        overview["customer_account_ref"]
        == f"customer_account:{case.customer_account_id}"
    )

    detail = await client.get(
        f"/v1/clients/{case.client_id}/cases/{case.product_signal_id}", headers=headers
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["overview"]["concern"]["validation_criterion"] == VALIDATION_CRITERION
    assert body["stoppable"] is True
    assert "stop" not in body
    assert body["interview_sessions"] == []
    assert body["preview_versions"] == []


async def test_a_case_detail_lists_the_sessions_it_ran(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """An Interview Session appears with its purpose, state, and phase."""
    case = await _case(
        database, auth_users["owner_a"], "dash-sessions", ProductSignalState.INTERVIEWING
    )
    await _grant(
        database,
        client_id=case.client_id,
        user=auth_users["owner_a"],
        role=ClientMemberRole.OWNER,
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )

    detail = await client.get(
        f"/v1/clients/{case.client_id}/cases/{case.product_signal_id}",
        headers=_staff(auth_users["owner_a"]),
    )
    assert detail.status_code == 200
    sessions = detail.json()["interview_sessions"]
    assert [row["id"] for row in sessions] == [str(session_id)]
    assert sessions[0]["purpose"] == "interview"
    assert sessions[0]["state"] == InterviewSessionState.LIVE.value


async def test_a_terminal_case_is_not_stoppable(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The detail view says plainly that there is no authority left to withdraw."""
    case = await _case(
        database, auth_users["owner_a"], "dash-terminal", ProductSignalState.CLOSED_VALIDATED
    )
    await _grant(
        database,
        client_id=case.client_id,
        user=auth_users["owner_a"],
        role=ClientMemberRole.OWNER,
    )
    detail = await client.get(
        f"/v1/clients/{case.client_id}/cases/{case.product_signal_id}",
        headers=_staff(auth_users["owner_a"]),
    )
    assert detail.status_code == 200
    assert detail.json()["stoppable"] is False


async def test_a_stopped_case_reports_who_stopped_it_and_why(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The audited stop is read back from the audit record, not re-derived."""
    case = await _case(
        database, auth_users["owner_a"], "dash-stop", ProductSignalState.AWAITING_INTERVIEW
    )
    member_id = await _grant(
        database,
        client_id=case.client_id,
        user=auth_users["owner_a"],
        role=ClientMemberRole.OWNER,
    )
    headers = _staff(auth_users["owner_a"])
    stopped = await client.post(
        f"/v1/clients/{case.client_id}/cases/{case.product_signal_id}/stop",
        headers=headers,
        json={"reason": "the requester left the Dudulemon account"},
    )
    assert stopped.status_code == 200

    detail = await client.get(
        f"/v1/clients/{case.client_id}/cases/{case.product_signal_id}", headers=headers
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["stoppable"] is False
    assert body["stop"]["reason"] == "the requester left the Dudulemon account"
    assert body["stop"]["client_member_id"] == str(member_id)
    assert body["stop"]["staff_role"] == ClientMemberRole.OWNER.value


async def test_members_carry_the_sign_in_email_the_dashboard_shows(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """uTrace records no staff display name, so the membership list shows the email."""
    case = await _case(
        database, auth_users["owner_a"], "dash-members", ProductSignalState.INTERVIEWING
    )
    await _grant(
        database,
        client_id=case.client_id,
        user=auth_users["owner_a"],
        role=ClientMemberRole.OWNER,
    )
    response = await client.get(
        f"/v1/clients/{case.client_id}/members", headers=_staff(auth_users["owner_a"])
    )
    assert response.status_code == 200
    members = response.json()["members"]
    assert [row["email"] for row in members] == [auth_users["owner_a"].email]


async def test_references_resolve_only_inside_the_calling_tenant(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Another client's reference resolves to no label rather than to its rows."""
    case = await _case(
        database, auth_users["owner_a"], "dash-refs", ProductSignalState.INTERVIEWING
    )
    other = await _case(
        database, auth_users["owner_b"], "dash-refs-other", ProductSignalState.INTERVIEWING
    )
    await _grant(
        database,
        client_id=case.client_id,
        user=auth_users["owner_a"],
        role=ClientMemberRole.OWNER,
    )
    mine_account = f"customer_account:{case.customer_account_id}"
    mine_user = f"client_user:{case.client_user_id}"
    theirs = f"customer_account:{other.customer_account_id}"

    response = await client.get(
        f"/v1/clients/{case.client_id}/references",
        headers=_staff(auth_users["owner_a"]),
        params=[("ref", mine_account), ("ref", mine_user), ("ref", theirs)],
    )
    assert response.status_code == 200
    labels = {row["ref"]: row.get("label") for row in response.json()["references"]}
    assert labels[mine_account] == "dash-refs customer"
    assert labels[mine_user] == "integration participant"
    assert labels[theirs] is None


async def test_a_reference_that_is_not_a_reference_is_refused(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A caller cannot turn the resolver into a general lookup."""
    case = await _case(
        database, auth_users["owner_a"], "dash-bad-ref", ProductSignalState.INTERVIEWING
    )
    await _grant(
        database,
        client_id=case.client_id,
        user=auth_users["owner_a"],
        role=ClientMemberRole.OWNER,
    )
    response = await client.get(
        f"/v1/clients/{case.client_id}/references",
        headers=_staff(auth_users["owner_a"]),
        params=[("ref", "client_members:everyone")],
    )
    assert response.status_code == 400
    assert response.json()["error"] == "ReferenceFormatError"


async def test_the_configuration_route_reports_integration_status(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The active release's Customer Surface, repository, and offer policy."""
    case = await _case(
        database, auth_users["owner_a"], "dash-config", ProductSignalState.INTERVIEWING
    )
    await _grant(
        database,
        client_id=case.client_id,
        user=auth_users["owner_a"],
        role=ClientMemberRole.OWNER,
    )
    await _activate_release(database, case, owner=auth_users["owner_a"])

    response = await client.get(
        f"/v1/clients/{case.client_id}/configuration",
        headers=_staff(auth_users["owner_a"]),
    )
    assert response.status_code == 200
    release = response.json()["release"]
    assert release["release_number"] == 2
    policy = release["policy"]
    assert policy["customer_surface"]["installation_ref"] == "guild-dash-config"
    assert policy["implementation"]["repository_ref"] == "ShopalyticsStore/shopalytics"
    assert policy["outreach_offers"]["fixed_amount_minor_units"] == 25_000
    assert policy["outreach_offers"]["currency"] == "USD"


async def _activate_release(
    database: Database, case: CaseFixture, *, owner: SignedInUser
) -> None:
    """Insert a complete configuration release for the fixture client."""
    policy = fixture_policy(
        guild_id="guild-dash-config", channel_id="channel-1", offers_enabled=True
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.configuration_releases (
                id, client_id, release_number, policy, digest,
                authorized_by_auth_user_id
            )
            values (%s, %s, 2, %s, %s, %s)
            """,
            (
                uuid7(),
                case.client_id,
                Jsonb(policy.model_dump(mode="json")),
                release_digest(policy),
                owner.auth_user_id,
            ),
        )


async def _pause_fact(
    database: Database, *, case: CaseFixture, session_id: UUID, paused: bool
) -> None:
    """Append the same Pause or Resume fact the participant control appends."""
    event_type = (
        "session_paused_live_implementation_held"
        if paused
        else "session_resumed_live_implementation_released"
    )
    stream = StreamRef(kind=AggregateKind.INTERVIEW_SESSION, id=session_id)
    actor = Actor(kind=ActorKind.CUSTOMER_PARTICIPANT, ref=str(case.client_user_id))
    async with database.transaction() as conn:
        await append_fact(
            conn,
            client_id=case.client_id,
            event=EventAppend(
                message_id=uuid7(),
                stream=stream,
                event_type=event_type,
                payload={"blocks_new_live_implementation_steps": paused},
                actor=actor,
                causation_id=None,
                correlation_id=None,
                occurred_at=datetime.now(tz=UTC),
            ),
            audit=AuditIntent(
                audit_id=uuid7(),
                subject=stream,
                action=event_type,
                outcome="applied",
                reason_code=None,
                actor=actor,
                pinned_configuration_release_id=None,
                detail={},
            ),
            jobs=(),
        )


async def test_a_case_reports_its_outreach_attempts_and_their_invitations(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Each invitation is reported by what became of it, never by its token."""
    case = await _case(
        database, auth_users["owner_a"], "dash-outreach", ProductSignalState.INTERVIEWING
    )
    await _grant(
        database,
        client_id=case.client_id,
        user=auth_users["owner_a"],
        role=ClientMemberRole.OWNER,
    )
    attempt_id = await create_outreach_attempt(
        database,
        case=case,
        state=OutreachAttemptState.CLAIMED,
        expires_in_seconds=3600,
    )
    # A case holds one live access link at a time: the first is revoked, the
    # replacement takes its place, and the supersession is recorded. The revoked
    # one has also passed its expiry, which is what proves the order the
    # dispositions are derived in.
    revoked_id = await create_invitation(
        database,
        case=case,
        outreach_attempt_id=attempt_id,
        token=f"revoked-{uuid7().hex}",
        bound_auth_user_id=auth_users["participant"].auth_user_id,
        expires_in_seconds=-60,
    )
    async with database.transaction() as conn:
        assert await revoke_live_invitation(
            conn, product_signal_id=case.product_signal_id
        ) == revoked_id
    claimed_id = await create_invitation(
        database,
        case=case,
        outreach_attempt_id=attempt_id,
        token=f"claimed-{uuid7().hex}",
        bound_auth_user_id=auth_users["participant"].auth_user_id,
        expires_in_seconds=3600,
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await link_supersession(
            conn, previous_id=revoked_id, replacement_id=claimed_id
        )
        await cur.execute(
            "update public.invitations set claimed_at = now(), interview_session_id = %s "
            "where id = %s",
            (session_id, claimed_id),
        )

    response = await client.get(
        f"/v1/clients/{case.client_id}/cases/{case.product_signal_id}/outreach-attempts",
        headers=_staff(auth_users["owner_a"]),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["product_signal_id"] == str(case.product_signal_id)
    assert [attempt["id"] for attempt in body["attempts"]] == [str(attempt_id)]
    attempt = body["attempts"][0]
    assert attempt["purpose"] == "initial_invitation"
    assert attempt["state"] == OutreachAttemptState.CLAIMED.value
    dispositions = {
        invitation["id"]: invitation["disposition"] for invitation in attempt["invitations"]
    }
    assert dispositions == {str(claimed_id): "claimed", str(revoked_id): "revoked"}
    for invitation in attempt["invitations"]:
        assert "token_digest" not in invitation
        assert "bound_provider_identity" not in invitation
    claimed = next(
        item for item in attempt["invitations"] if item["id"] == str(claimed_id)
    )
    assert claimed["interview_session_id"] == str(session_id)


async def test_a_case_session_carries_its_character_readiness_and_pauses(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The staff view reads the durable session facts, including every Pause."""
    case = await _case(
        database, auth_users["owner_a"], "dash-session-state", ProductSignalState.INTERVIEWING
    )
    await _grant(
        database,
        client_id=case.client_id,
        user=auth_users["owner_a"],
        role=ClientMemberRole.OWNER,
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    await _pause_fact(database, case=case, session_id=session_id, paused=True)
    await _pause_fact(database, case=case, session_id=session_id, paused=False)

    detail = await client.get(
        f"/v1/clients/{case.client_id}/cases/{case.product_signal_id}",
        headers=_staff(auth_users["owner_a"]),
    )
    assert detail.status_code == 200
    session = detail.json()["interview_sessions"][0]
    assert session["id"] == str(session_id)
    assert session["character_state"] == "listening"
    assert session["readiness"] == {"conversational": True, "observation": False}
    assert session["implementation_progress"] == "not_started"
    assert session["muted"] is False and session["paused"] is False
    assert [record["paused"] for record in session["pauses"]] == [True, False]


async def test_a_case_reports_its_preview_runtimes_and_what_cleanup_left(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Cleanup is complete only once nothing is held and the workspace said so."""
    case = await _case(
        database, auth_users["owner_a"], "dash-runtimes", ProductSignalState.CLOSED_VALIDATED
    )
    await _grant(
        database,
        client_id=case.client_id,
        user=auth_users["owner_a"],
        role=ClientMemberRole.OWNER,
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.CLOSED, conversational_ready=False
    )
    allocation_id = await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )

    runtimes = await client.get(
        f"/v1/clients/{case.client_id}/cases/{case.product_signal_id}/preview-runtime-allocations",
        headers=_staff(auth_users["owner_a"]),
    )
    assert runtimes.status_code == 200
    allocations = runtimes.json()["allocations"]
    assert [row["id"] for row in allocations] == [str(allocation_id)]
    assert allocations[0]["allocation_kind"] == "base"
    assert allocations[0]["state"] == "ready"
    assert allocations[0]["origin_host"].endswith(".preview.utrace.dev")

    held = await client.get(
        f"/v1/clients/{case.client_id}/cases/{case.product_signal_id}",
        headers=_staff(auth_users["owner_a"]),
    )
    assert held.json()["cleanup"] == {
        "preview_runtime_allocations_retained": 1,
        "preview_runtime_allocations_released": 0,
        "preview_runtime_allocations_terminated": 0,
        "environment_leases_open": 0,
        "invitations_outstanding": 0,
        "complete": False,
    }

    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.preview_runtime_allocations "
            "set state = 'terminated', terminated_at = now() where id = %s",
            (allocation_id,),
        )
        await cur.execute(
            """
            insert into public.evidence_records (
                id, client_id, product_signal_id, evidence_class, claim_state,
                source_ref, context_revision, body
            )
            values (%s, %s, %s, 'implementation_artifact', 'confirmed', %s, 1, %s)
            """,
            (
                uuid7(),
                case.client_id,
                case.product_signal_id,
                "daytona:workspace",
                Jsonb({"files_touched": [], "cleanup_state": "workspace destroyed"}),
            ),
        )

    cleaned = await client.get(
        f"/v1/clients/{case.client_id}/cases/{case.product_signal_id}",
        headers=_staff(auth_users["owner_a"]),
    )
    assert cleaned.json()["cleanup"] == {
        "preview_runtime_allocations_retained": 0,
        "preview_runtime_allocations_released": 0,
        "preview_runtime_allocations_terminated": 1,
        "environment_leases_open": 0,
        "invitations_outstanding": 0,
        "workspace_cleanup_state": "workspace destroyed",
        "complete": True,
    }
