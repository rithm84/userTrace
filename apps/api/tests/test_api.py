"""API authorization and route behaviour.

Requests go through the assembled application with real Supabase access tokens.
Authentication alone grants nothing, staff and participant authority are
separate, and a route whose behaviour belongs to a later workstream returns the
typed "not yet wired" refusal rather than a plausible stub.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest_asyncio
from fastapi import FastAPI
from utrace_contracts.domain.lifecycle import (
    ClientMemberRole,
    InterviewSessionState,
    JobState,
    MembershipStatus,
    OutreachAttemptState,
    ProductSignalState,
)

from app.db.pool import Database
from app.extension.bindings import offer_digest
from app.ids import uuid7
from app.intake.jobs import SEND_STOP_NOTICE
from app.interview.settings import get_interview_settings
from app.lifecycle.case_stop import CLOSE_CASE_AFTER_STOP
from app.main import create_app
from app.offers.job_types import EVALUATE_ENTITLEMENT
from app.ports import PortRegistry
from app.settings import Settings
from tests.extension.keys import DeviceKey
from tests.factories import (
    CaseFixture,
    create_case,
    create_interview_session,
    create_invitation,
    create_outreach_attempt,
)
from tests.helpers import count_rows, read_state
from tests.interview.fixtures import (
    create_binding,
    create_preview_allocation,
    link_installation,
)
from tests.supabase_auth import SignedInUser

BASE_URL = "http://control-plane.test"


async def _stop_notices(database: Database, *, product_signal_id: UUID) -> int:
    """Count the ready client-stop notice Jobs no cancellation scope can cancel."""
    return await count_rows(
        database,
        table="jobs",
        where=(
            "job_type = %s and state = %s and cancellation_scope_id is null "
            "and input->>'product_signal_id' = %s"
        ),
        params=[SEND_STOP_NOTICE, JobState.READY.value, str(product_signal_id)],
    )


@pytest_asyncio.fixture
async def client(settings: Settings, database: Database) -> AsyncIterator[httpx.AsyncClient]:
    """The assembled application with no provider adapters installed."""
    app: FastAPI = create_app(settings, PortRegistry(voice=None, receipts=None, observation=None, evidence=None))
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
    database: Database,
    owner: SignedInUser,
    name: str,
    state: ProductSignalState,
    participant: SignedInUser | None,
) -> CaseFixture:
    return await create_case(
        database,
        name=name,
        state=state,
        authorizing_auth_user_id=owner.auth_user_id,
        participant_auth_user_id=(
            participant.auth_user_id if participant is not None else None
        ),
    )


async def test_health_needs_no_authority(client: httpx.AsyncClient) -> None:
    """Liveness is unauthenticated and says nothing about readiness."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_a_missing_or_malformed_token_is_refused(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """No header, a non-Bearer scheme, and a forged token all fail closed."""
    case = await _case(
        database, auth_users["owner_a"], "api-noauth", ProductSignalState.INTERVIEWING, None
    )
    path = f"/v1/clients/{case.client_id}"

    missing = await client.get(path)
    assert missing.status_code == 403
    assert missing.json()["error"] == "AuthorizationError"
    assert "Authorization header is missing" in missing.json()["detail"]

    wrong_scheme = await client.get(path, headers={"Authorization": "Basic abc"})
    assert wrong_scheme.status_code == 403

    forged = await client.get(path, headers={"Authorization": "Bearer not-a-jwt"})
    assert forged.status_code == 403
    assert forged.json()["error"] == "AuthorizationError"


async def test_authentication_without_membership_is_refused(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A verified identity with no active membership row reads nothing."""
    case = await _case(
        database, auth_users["owner_a"], "api-nomember", ProductSignalState.INTERVIEWING, None
    )
    response = await client.get(
        f"/v1/clients/{case.client_id}", headers=_staff(auth_users["owner_a"])
    )
    assert response.status_code == 403
    assert "no active client_members row" in response.json()["detail"]


async def test_staff_membership_grants_only_its_own_tenant(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Owner A reads client A and is refused client B, with the same token."""
    first = await _case(
        database, auth_users["owner_a"], "api-tenant-a", ProductSignalState.INTERVIEWING, None
    )
    second = await _case(
        database, auth_users["owner_b"], "api-tenant-b", ProductSignalState.INTERVIEWING, None
    )
    await _grant(
        database, client_id=first.client_id, user=auth_users["owner_a"], role=ClientMemberRole.OWNER
    )
    await _grant(
        database, client_id=second.client_id, user=auth_users["owner_b"], role=ClientMemberRole.OWNER
    )

    mine = await client.get(
        f"/v1/clients/{first.client_id}", headers=_staff(auth_users["owner_a"])
    )
    assert mine.status_code == 200
    body = mine.json()
    assert body["client"]["id"] == str(first.client_id)
    assert body["membership"]["role"] == ClientMemberRole.OWNER.value

    theirs = await client.get(
        f"/v1/clients/{second.client_id}", headers=_staff(auth_users["owner_a"])
    )
    assert theirs.status_code == 403


async def test_staff_read_routes_return_tenant_scoped_projections(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Cases, Case Files, members, and offers are scoped and typed."""
    case = await _case(
        database, auth_users["owner_a"], "api-reads", ProductSignalState.INTERVIEWING, None
    )
    other = await _case(
        database, auth_users["owner_b"], "api-reads-other", ProductSignalState.INTERVIEWING, None
    )
    await _grant(
        database, client_id=case.client_id, user=auth_users["owner_a"], role=ClientMemberRole.OWNER
    )
    headers = _staff(auth_users["owner_a"])

    cases = await client.get(f"/v1/clients/{case.client_id}/cases", headers=headers)
    assert cases.status_code == 200
    ids = {row["id"] for row in cases.json()["cases"]}
    assert ids == {str(case.product_signal_id)}
    assert str(other.product_signal_id) not in ids

    detail = await client.get(
        f"/v1/clients/{case.client_id}/cases/{case.product_signal_id}", headers=headers
    )
    assert detail.status_code == 200
    assert detail.json()["case"]["state"] == ProductSignalState.INTERVIEWING.value
    # The case is still open, so there is no Case File on the detail view yet.
    assert "case_file" not in detail.json()

    cross_tenant = await client.get(
        f"/v1/clients/{case.client_id}/cases/{other.product_signal_id}", headers=headers
    )
    assert cross_tenant.status_code == 404

    for path in ("case-files", "members", "offers"):
        response = await client.get(
            f"/v1/clients/{case.client_id}/{path}", headers=headers
        )
        assert response.status_code == 200, path

    offers = await client.get(f"/v1/clients/{case.client_id}/offers", headers=headers)
    assert offers.json()["remaining_minor_units"] == 0
    # No offer has been issued yet, so there is no currency to report.
    assert "currency" not in offers.json()


async def test_client_stop_before_participation_cancels_without_a_case_file(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The deny-only stop cancels the case, revokes the pending invitation,
    schedules the entitlement evaluation that releases the unearned reservation,
    and schedules the notice that tells the originating user."""
    case = await _case(
        database, auth_users["owner_a"], "api-stop-early", ProductSignalState.AWAITING_INTERVIEW, None
    )
    await _grant(
        database, client_id=case.client_id, user=auth_users["owner_a"], role=ClientMemberRole.OWNER
    )
    attempt_id = await create_outreach_attempt(
        database, case=case, state=OutreachAttemptState.AWAITING_RESPONSE, expires_in_seconds=3600
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.PREPARING, conversational_ready=False
    )

    response = await client.post(
        f"/v1/clients/{case.client_id}/cases/{case.product_signal_id}/stop",
        headers=_staff(auth_users["owner_a"]),
        json={"reason": "the customer asked us to stand down"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["participation_occurred"] is False
    session_state, _ = await read_state(
        database, table="interview_sessions", row_id=session_id, column="state"
    )
    assert session_state == InterviewSessionState.CLOSED.value
    assert body["terminal_state"] == ProductSignalState.CANCELED.value
    assert body["revoked_outreach_attempts"] == 1

    state, _ = await read_state(
        database, table="product_signals", row_id=case.product_signal_id, column="state"
    )
    assert state == ProductSignalState.CANCELED.value
    attempt_state, _ = await read_state(
        database, table="outreach_attempts", row_id=attempt_id, column="state"
    )
    assert attempt_state == OutreachAttemptState.REVOKED.value
    assert (
        await count_rows(
            database,
            table="case_files",
            where="product_signal_id = %s",
            params=[case.product_signal_id],
        )
        == 0
    )
    assert (
        await count_rows(
            database,
            table="jobs",
            where="job_type = %s and input->>'product_signal_id' = %s",
            params=[EVALUATE_ENTITLEMENT, str(case.product_signal_id)],
        )
        == 1
    )
    assert await _stop_notices(database, product_signal_id=case.product_signal_id) == 1


async def test_client_stop_after_participation_withdraws_authority_and_schedules_closure(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """After participation the stop withdraws authority, enqueues the Case File, and
    schedules the notice that tells the originating user."""
    case = await _case(
        database, auth_users["owner_a"], "api-stop-late", ProductSignalState.IMPLEMENTING, None
    )
    await _grant(
        database, client_id=case.client_id, user=auth_users["owner_a"], role=ClientMemberRole.OWNER
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    allocation_id = await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )
    installation_id = await link_installation(
        database,
        utrace_user_id=case.utrace_user_id,
        device_public_key_spki_b64u=DeviceKey.generate().public_key_spki_b64u,
        protocol_version="1.0",
    )
    await create_binding(
        database,
        case=case,
        interview_session_id=session_id,
        extension_installation_id=installation_id,
        preview_runtime_allocation_id=allocation_id,
    )

    response = await client.post(
        f"/v1/clients/{case.client_id}/cases/{case.product_signal_id}/stop",
        headers=_staff(auth_users["owner_a"]),
        json={"reason": "legal hold"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["participation_occurred"] is True
    # The case had not reached a terminal state, so the field is absent.
    assert "terminal_state" not in body
    # The closed session's extension binding was revoked with the close, so the
    # extension's socket pushes the revocation and command authority ends.
    assert (
        await count_rows(
            database,
            table="extension_bindings",
            where="interview_session_id = %s and status = 'revoked' and reason = %s",
            params=[session_id, "client_stopped"],
        )
        == 1
    )

    # The case stays nonterminal until its blocker Case File is published.
    state, _ = await read_state(
        database, table="product_signals", row_id=case.product_signal_id, column="state"
    )
    assert state == ProductSignalState.IMPLEMENTING.value
    assert (
        await count_rows(
            database,
            table="jobs",
            where="job_type = %s and state = %s",
            params=[CLOSE_CASE_AFTER_STOP, JobState.READY.value],
        )
        == 1
    )
    assert await _stop_notices(database, product_signal_id=case.product_signal_id) == 1


async def test_stopping_a_terminal_case_is_refused(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The stop control cannot reopen or re-stop a closed case."""
    case = await _case(
        database, auth_users["owner_a"], "api-stop-closed", ProductSignalState.CLOSED_VALIDATED, None
    )
    await _grant(
        database, client_id=case.client_id, user=auth_users["owner_a"], role=ClientMemberRole.OWNER
    )
    response = await client.post(
        f"/v1/clients/{case.client_id}/cases/{case.product_signal_id}/stop",
        headers=_staff(auth_users["owner_a"]),
        json={"reason": "too late"},
    )
    assert response.status_code == 409
    assert "already terminal" in response.json()["detail"]


async def test_invitation_claim_proves_the_invited_identity(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Only the invited identity claims the invitation, and claiming is idempotent.

    The uTrace User exists before the person ever signs in, as intake and the
    demo seed create it, so the first claim is what binds the account to it.
    """
    case = await _case(
        database,
        auth_users["owner_a"],
        "api-claim",
        ProductSignalState.AWAITING_INTERVIEW,
        None,
    )
    attempt_id = await create_outreach_attempt(
        database, case=case, state=OutreachAttemptState.AWAITING_RESPONSE, expires_in_seconds=3600
    )
    token = uuid7().hex + uuid7().hex
    await create_invitation(
        database,
        case=case,
        outreach_attempt_id=attempt_id,
        token=token,
        bound_auth_user_id=auth_users["participant"].auth_user_id,
        expires_in_seconds=3600,
    )

    stranger = await client.post(
        f"/v1/invitations/{token}/claim", headers=_staff(auth_users["owner_b"])
    )
    assert stranger.status_code == 403
    assert "does not control the invited" in stranger.json()["detail"]

    claimed = await client.post(
        f"/v1/invitations/{token}/claim", headers=_staff(auth_users["participant"])
    )
    assert claimed.status_code == 200
    session_id = claimed.json()["session_id"]

    again = await client.post(
        f"/v1/invitations/{token}/claim", headers=_staff(auth_users["participant"])
    )
    assert again.json()["session_id"] == session_id

    session = await client.get(
        f"/v1/sessions/{session_id}", headers=_staff(auth_users["participant"])
    )
    assert session.status_code == 200
    projection = await client.get(
        f"/v1/sessions/{session_id}/projection", headers=_staff(auth_users["participant"])
    )
    assert projection.status_code == 200

    attempt_state, _ = await read_state(
        database, table="outreach_attempts", row_id=attempt_id, column="state"
    )
    assert attempt_state == OutreachAttemptState.CLAIMED.value

    unknown = await client.post(
        f"/v1/invitations/{uuid7().hex * 2}/claim",
        headers=_staff(auth_users["participant"]),
    )
    assert unknown.status_code == 403


async def test_session_projection_and_controls_require_the_same_participant(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The session projection is durable, and only its own participant may act."""
    case = await _case(
        database,
        auth_users["owner_a"],
        "api-session",
        ProductSignalState.INTERVIEWING,
        auth_users["participant"],
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )

    stranger = await client.get(
        f"/v1/sessions/{session_id}", headers=_staff(auth_users["owner_a"])
    )
    assert stranger.status_code == 403
    assert "own originating user" in stranger.json()["detail"]

    mine = await client.get(
        f"/v1/sessions/{session_id}", headers=_staff(auth_users["participant"])
    )
    assert mine.status_code == 200
    session = mine.json()["session"]
    assert session["state"] == InterviewSessionState.LIVE.value
    assert session["readiness"] == {"conversational": True, "observation": False}
    assert session["controls"] == {"muted": False, "paused": False}

    # The acknowledgement returns the projection revision the session tab's
    # Realtime subscription will also see, with the controls it may now use.
    paused = await client.post(
        f"/v1/sessions/{session_id}/controls",
        headers=_staff(auth_users["participant"]),
        json={"action": "pause"},
    )
    assert paused.status_code == 200
    assert paused.json()["state"] == "paused"
    assert paused.json()["controls"]["available"] == [
        "resume",
        "finish_later",
        "end_confirm",
    ]

    resumed = await client.post(
        f"/v1/sessions/{session_id}/controls",
        headers=_staff(auth_users["participant"]),
        json={"action": "resume"},
    )
    assert resumed.json()["state"] == "interviewing"
    assert resumed.json()["revision"] > paused.json()["revision"]

    # The projection route answers the whole document Realtime distributes, at
    # the revision the acknowledgement above committed. The acknowledgement
    # itself stays the narrow control shape.
    document = await client.get(
        f"/v1/sessions/{session_id}/projection",
        headers=_staff(auth_users["participant"]),
    )
    assert document.status_code == 200
    body = document.json()
    assert set(body) == {"revision", "projection"}
    assert set(resumed.json()) == {"revision", "state", "controls"}
    assert body["revision"] == resumed.json()["revision"]
    assert body["projection"]["revision"] == body["revision"]
    assert body["projection"]["session_id"] == str(session_id)
    assert body["projection"]["state"] == "interviewing"
    assert body["projection"]["controls"] == resumed.json()["controls"]
    assert body["projection"]["character"]["state"] == "listening"
    assert body["projection"]["status_text"] == "Preparing your preview"

    finished = await client.post(
        f"/v1/sessions/{session_id}/controls",
        headers=_staff(auth_users["participant"]),
        json={"action": "finish_later"},
    )
    assert finished.status_code == 200
    assert finished.json()["state"] == "ended"
    assert finished.json()["controls"]["available"] == []

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select return_deadline_at from public.product_signals where id = %s",
            (case.product_signal_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["return_deadline_at"] is not None


async def test_the_participant_account_lists_its_sessions_and_guards_preferences(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The account shows each session as its tab last did; a stale edit is refused."""
    participant = _staff(auth_users["participant"])
    case = await _case(
        database,
        auth_users["owner_a"],
        "api-account",
        ProductSignalState.INTERVIEWING,
        auth_users["participant"],
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.source_reports (
                id, client_id, product_signal_id, intake_item_id, reporting_client_user_id,
                reporting_customer_account_id, source_excerpt, occurred_at
            )
            values (%s, %s, %s, %s, %s, %s, 'Saved conversion views', now())
            """,
            (
                uuid7(),
                case.client_id,
                case.product_signal_id,
                case.intake_item_id,
                case.client_user_id,
                case.customer_account_id,
            ),
        )
    finished = await client.post(
        f"/v1/sessions/{session_id}/controls",
        headers=participant,
        json={"action": "finish_later"},
    )
    assert finished.status_code == 200

    account = await client.get("/v1/participants/me", headers=participant)
    assert account.status_code == 200, account.text
    body = account.json()
    mine = [item for item in body["interviews"] if item["session_id"] == str(session_id)]
    assert len(mine) == 1
    assert mine[0]["topic"] == "Saved conversion views"
    assert mine[0]["purpose"] == "interview"
    assert mine[0]["state"] == "ended"
    assert mine[0]["end_reason"] == "finish_later"

    preferences = body["preferences"]
    unchanged = {
        "visual_guidance_enabled": preferences["visual_guidance_enabled"],
        "agent_cursor_enabled": preferences["agent_cursor_enabled"],
    }
    saved = await client.patch(
        "/v1/participants/me/preferences",
        headers=participant,
        json={"expected_revision": preferences["revision"], **unchanged},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"revision": preferences["revision"] + 1, **unchanged}
    stale = await client.patch(
        "/v1/participants/me/preferences",
        headers=participant,
        json={"expected_revision": preferences["revision"], **unchanged},
    )
    assert stale.status_code == 409

    staff_only = await client.get("/v1/participants/me", headers=_staff(auth_users["owner_a"]))
    assert staff_only.status_code == 403


async def test_preflight_refuses_an_unsupported_participant_environment(
    client: httpx.AsyncClient,
    database: Database,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Participant setup must pass before the session enters preparation."""
    case = await _case(
        database,
        auth_users["owner_a"],
        "api-preflight",
        ProductSignalState.AWAITING_INTERVIEW,
        auth_users["participant"],
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.CLAIMED, conversational_ready=False
    )
    headers = _staff(auth_users["participant"])

    # A failing environment is recorded with its guidance rather than refused
    # outright: the participant may still fix the setup.
    refused = await client.post(
        f"/v1/sessions/{session_id}/preflight",
        headers=headers,
        json={
            "browser": {"name": "Chrome", "major_version": 153, "supported": True},
            "extension": {"installed": False, "account_linked": False},
            "permissions": {"host": True, "microphone": True},
        },
    )
    assert refused.status_code == 200
    assert refused.json()["passed"] is False
    failed = {
        check["id"] for check in refused.json()["checks"] if check["status"] == "failed"
    }
    assert failed == {"extension_installed", "extension_account_linked"}
    state, _ = await read_state(
        database, table="interview_sessions", row_id=session_id, column="state"
    )
    assert state == InterviewSessionState.CLAIMED.value

    device_key = DeviceKey.generate()
    installation_id = await link_installation(
        database,
        utrace_user_id=case.utrace_user_id,
        device_public_key_spki_b64u=device_key.public_key_spki_b64u,
        protocol_version="1.0",
    )
    passed = await client.post(
        f"/v1/sessions/{session_id}/preflight",
        headers=headers,
        json={
            "browser": {"name": "Google Chrome", "major_version": 153, "supported": True},
            "extension": {
                "installed": True,
                "account_linked": True,
                "installation_proof": device_key.forwarded_preflight_proof(
                    audience=get_interview_settings().extension_api_audience,
                    installation_id=installation_id,
                ),
                "protocol_version": "1.0",
            },
            "permissions": {"host": True, "microphone": True},
        },
    )
    assert passed.status_code == 200
    body = passed.json()
    assert body["passed"] is True
    state, _ = await read_state(
        database, table="interview_sessions", row_id=session_id, column="state"
    )
    assert state == InterviewSessionState.PREPARING.value


async def test_extension_offer_is_audience_bound(
    client: httpx.AsyncClient,
    database: Database,
    settings: Settings,
    auth_users: dict[str, SignedInUser],
) -> None:
    """The offer is single use and scoped to one installation and allocation."""
    case = await _case(
        database,
        auth_users["owner_a"],
        "api-extension-offer",
        ProductSignalState.INTERVIEWING,
        auth_users["participant"],
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    headers = _staff(auth_users["participant"])

    # Without a ready Preview Runtime Allocation there is nothing to bind to.
    unready = await client.post(
        f"/v1/sessions/{session_id}/extension-offer", headers=headers
    )
    assert unready.status_code == 409
    assert "Preview Runtime Allocation" in unready.json()["detail"]

    allocation_id = await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )
    installation_id = await link_installation(
        database,
        utrace_user_id=case.utrace_user_id,
        device_public_key_spki_b64u=f"device-key-{session_id.hex}",
        protocol_version="1.0",
    )
    response = await client.post(
        f"/v1/sessions/{session_id}/extension-offer", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == str(session_id)
    assert body["client_id"] == str(case.client_id)
    assert body["extension_installation_id"] == str(installation_id)
    assert body["preview_runtime_allocation_id"] == str(allocation_id)
    assert body["protocol_version"] == "1.0"

    # The token is opaque to the tab; only its digest is stored.
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select offer_digest, consumed_at from public.extension_binding_offers "
            "where interview_session_id = %s",
            (session_id,),
        )
        stored = await cur.fetchone()
    assert stored is not None
    assert stored["offer_digest"] == offer_digest(body["offer_token"])
    assert stored["consumed_at"] is None


async def test_preview_handoff_requires_a_committed_route(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A session with no committed preview route cannot hand off to the front door."""
    case = await _case(
        database,
        auth_users["owner_a"],
        "api-handoff",
        ProductSignalState.INTERVIEWING,
        auth_users["participant"],
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    response = await client.post(
        f"/v1/sessions/{session_id}/preview-handoff",
        headers=_staff(auth_users["participant"]),
    )
    assert response.status_code == 409
    assert "committed preview route" in response.json()["detail"]


async def test_voice_connection_reports_the_unwired_provider_adapter(
    client: httpx.AsyncClient, database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """With no voice adapter installed the route refuses, naming its owner."""
    case = await _case(
        database,
        auth_users["owner_a"],
        "api-voice",
        ProductSignalState.INTERVIEWING,
        auth_users["participant"],
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    response = await client.post(
        f"/v1/sessions/{session_id}/voice/connections",
        headers=_staff(auth_users["participant"]),
        json={"sdp_offer": "v=0\r\n"},
    )
    assert response.status_code == 501
    body = response.json()
    assert body["error"] == "NotYetWiredError"
    assert body["subject"] == "provider-adapters"
    assert (
        await count_rows(
            database,
            table="voice_connections",
            where="interview_session_id = %s",
            params=[session_id],
        )
        == 0
    )


async def test_front_door_routes_require_the_service_token(
    client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    """The front door presents its own token; a participant token is refused."""
    unauthorized = await client.get(
        "/v1/preview/routes/example.preview.utrace.dev",
        headers={"Authorization": "Bearer not-the-service-token"},
    )
    assert unauthorized.status_code == 403

    service = {"Authorization": f"Bearer {settings.front_door_service_token}"}
    # The door always presents the participant's origin-bound session credential;
    # without one the answer is the typed refusal it maps to a neutral surface.
    missing = await client.get(
        "/v1/preview/routes/example.preview.utrace.dev", headers=service
    )
    assert missing.status_code == 401
    assert missing.json() == {
        "schema_version": "utrace_preview_denial_v1",
        "reason": "unauthorized_session",
    }

    # Document activation is wired by the telemetry ingestion workstream. It
    # still refuses a caller without the door's own token, and it refuses a
    # request that carries no forwarded session context.
    activations = await client.post(
        "/v1/preview/activations",
        headers={"Authorization": "Bearer not-the-service-token"},
        json={
            "schema_version": "utrace_preview_activation_relay_request_v1",
            "public_preview_host": "example.preview.utrace.dev",
            "activation_request": {
                "schema_version": "1",
                "sdk_installation_id": "example-sdk",
                "environment_name": "preview",
                "client_release_id": "example-release",
                "protocol_version": "1",
                "document_nonce": "0" * 48,
                "document_origin": "https://example.preview.utrace.dev",
            },
        },
    )
    assert activations.status_code == 403

    without_session = await client.post(
        "/v1/preview/activations",
        headers=service,
        json={
            "schema_version": "utrace_preview_activation_relay_request_v1",
            "public_preview_host": "example.preview.utrace.dev",
            "activation_request": {
                "schema_version": "1",
                "sdk_installation_id": "example-sdk",
                "environment_name": "preview",
                "client_release_id": "example-release",
                "protocol_version": "1",
                "document_nonce": "0" * 48,
                "document_origin": "https://example.preview.utrace.dev",
            },
        },
    )
    assert without_session.status_code == 422


async def test_ingestion_and_callback_skeletons_refuse_explicitly(
    client: httpx.AsyncClient,
) -> None:
    """Unwired routes name their owning workstream instead of returning a stub."""
    # The OTLP receiver is wired by the telemetry ingestion workstream; an
    # unauthenticated export is refused as a credential rejection, not a stub.
    otlp = await client.post("/v1/ingest/otlp/v1/traces", content=b"")
    assert otlp.status_code == 401

    stripe = await client.post(
        "/v1/callbacks/stripe",
        headers={"x-utrace-client-id": str(uuid7())},
        content=b"{}",
    )
    assert stripe.status_code == 501
    assert stripe.json()["subject"] == "provider-adapters"
