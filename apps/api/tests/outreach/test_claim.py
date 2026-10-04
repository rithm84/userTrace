"""Invitation claim completion.

The binding is provider-generic: the invitation names the participant's
configured sign-in provider and subject, and the claim proves control of exactly
that identity through `auth.identities`. These tests bind the email provider
because Discord sign-in is not yet enabled on the Supabase projects; the code
path never names a provider.
"""

from __future__ import annotations

import pytest

from app.db.pool import Database
from app.db.rows import as_int, as_str
from app.errors import AuthorizationError
from app.intake.jobs import PREPARE_BASE_ALLOCATION
from app.outreach.claim import claim_and_prepare
from tests.cases import seed_awaiting_invitation, seed_product_signal
from tests.supabase_auth import SignedInUser
from tests.workstream import (
    WorkstreamFixture,
    create_workstream_client,
)

CONCERN = "Saving a conversion view would stop the Monday rebuild."


async def _invited(
    database: Database, auth_users: dict[str, SignedInUser]
) -> tuple[WorkstreamFixture, str]:
    """Configure a client, a case, and one live invitation bound to Maya."""
    participant = auth_users["participant"]
    fixture = await create_workstream_client(
        database,
        owner_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_sign_in=(
            participant.identity_provider,
            participant.identity_provider_id,
        ),
        offers_enabled=False,
    )
    signal_id = await seed_product_signal(
        database, fixture, reporter=fixture.maya, excerpt=CONCERN
    )
    _, token = await seed_awaiting_invitation(
        database,
        fixture,
        product_signal_id=signal_id,
        participant=fixture.maya,
        bound_provider=participant.identity_provider,
        bound_identity=participant.identity_provider_id,
    )
    return fixture, token


async def test_a_claim_by_another_identity_is_rejected(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    fixture, token = await _invited(database, auth_users)
    impostor = auth_users["owner_b"]

    with pytest.raises(AuthorizationError) as rejected:
        await claim_and_prepare(
            database,
            token=token,
            auth_user_id=impostor.auth_user_id,
        )
    assert "does not control the invited" in rejected.value.reason

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as total from public.interview_sessions "
            "where client_id = %s",
            (fixture.client_id,),
        )
        sessions = await cur.fetchone()
    assert sessions is not None
    assert as_int(sessions, "total") == 0


async def test_the_invited_identity_claims_and_hands_off_to_preview_preparation(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    fixture, token = await _invited(database, auth_users)
    participant = auth_users["participant"]

    result = await claim_and_prepare(
        database,
        token=token,
        auth_user_id=participant.auth_user_id,
    )
    assert not result.already_claimed
    assert result.preparation_job_id is not None

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state, client_user_id from public.interview_sessions where id = %s",
            (result.interview_session_id,),
        )
        session = await cur.fetchone()
        await cur.execute(
            "select state from public.outreach_attempts where product_signal_id = %s",
            (result.product_signal_id,),
        )
        attempt = await cur.fetchone()
        await cur.execute(
            "select count(*) as total from public.jobs "
            "where client_id = %s and job_type = %s",
            (fixture.client_id, PREPARE_BASE_ALLOCATION),
        )
        jobs = await cur.fetchone()
    assert session is not None
    assert as_str(session, "state") == "claimed"
    assert session["client_user_id"] == fixture.maya.client_user_id
    assert attempt is not None
    assert as_str(attempt, "state") == "claimed"
    assert jobs is not None
    assert as_int(jobs, "total") == 1

    # Reconnecting returns the same Interview Session rather than a second one.
    again = await claim_and_prepare(
        database,
        token=token,
        auth_user_id=participant.auth_user_id,
    )
    assert again.already_claimed
    assert again.preparation_job_id is None
    assert again.interview_session_id == result.interview_session_id
