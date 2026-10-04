"""Terminal expiry of an unanswered initial invitation.

Expiry is a cancellation: no Case File, revoked invitations, and the unearned
reservation returns its allowance. A late Job against an answered attempt
changes nothing.
"""

from __future__ import annotations

from uuid import UUID

from app.db.pool import Database
from app.db.rows import as_int, as_str
from app.outreach.expiry import expire_initial_invitation
from app.outreach.offers import remaining_allowance, reserve_offer
from app.outreach.reasons import INVITATION_EXPIRED
from tests.cases import seed_awaiting_invitation, seed_product_signal
from tests.supabase_auth import SignedInUser
from tests.workstream import (
    OFFER_AMOUNT_MINOR_UNITS,
    STRIPE_CUSTOMER_REF,
    WorkstreamFixture,
    create_workstream_client,
)

CONCERN = "Saving a conversion view would stop the Monday rebuild."


async def _case_awaiting_response(
    database: Database, auth_users: dict[str, SignedInUser]
) -> tuple[WorkstreamFixture, UUID, UUID]:
    """A reserved offer and a delivered invitation waiting for Maya's answer."""
    participant = auth_users["participant"]
    fixture = await create_workstream_client(
        database,
        owner_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_sign_in=(
            participant.identity_provider,
            participant.identity_provider_id,
        ),
        offers_enabled=True,
    )
    signal_id = await seed_product_signal(
        database, fixture, reporter=fixture.maya, excerpt=CONCERN
    )
    attempt_id, _ = await seed_awaiting_invitation(
        database,
        fixture,
        product_signal_id=signal_id,
        participant=fixture.maya,
        bound_provider=participant.identity_provider,
        bound_identity=participant.identity_provider_id,
    )
    async with database.transaction() as conn:
        await reserve_offer(
            conn,
            client_id=fixture.client_id,
            product_signal_id=signal_id,
            customer_account_id=fixture.dudulemon_account_id,
            policy=fixture.release.policy.outreach_offers,
            billing_customer_ref=STRIPE_CUSTOMER_REF,
            billing_mapping_revision=1,
        )
    return fixture, signal_id, attempt_id


async def _backdate(database: Database, *, attempt_id: UUID) -> None:
    """Move the pinned response deadline into the past."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.outreach_attempts set expires_at = now() - interval "
            "'1 minute' where id = %s",
            (attempt_id,),
        )


async def test_an_unanswered_invitation_expires_and_releases_its_reservation(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    fixture, signal_id, attempt_id = await _case_awaiting_response(database, auth_users)
    before = await remaining_allowance(
        database, client_id=fixture.client_id, currency="USD"
    )
    assert before.reserved_minor_units == OFFER_AMOUNT_MINOR_UNITS

    await _backdate(database, attempt_id=attempt_id)
    outcome = await expire_initial_invitation(
        database, outreach_attempt_id=attempt_id
    )
    assert outcome.expired
    assert outcome.reason == INVITATION_EXPIRED
    assert outcome.released_minor_units == OFFER_AMOUNT_MINOR_UNITS

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state, closure_reason from public.product_signals where id = %s",
            (signal_id,),
        )
        signal = await cur.fetchone()
        await cur.execute(
            "select state from public.outreach_attempts where id = %s", (attempt_id,)
        )
        attempt = await cur.fetchone()
        await cur.execute(
            "select count(*) as total from public.invitations "
            "where product_signal_id = %s and revoked_at is null",
            (signal_id,),
        )
        live = await cur.fetchone()
        await cur.execute(
            "select count(*) as total from public.case_files where client_id = %s",
            (fixture.client_id,),
        )
        case_files = await cur.fetchone()
    assert signal is not None
    assert as_str(signal, "state") == "canceled"
    assert as_str(signal, "closure_reason") == INVITATION_EXPIRED
    assert attempt is not None
    assert as_str(attempt, "state") == "expired"
    assert live is not None
    assert as_int(live, "total") == 0
    assert case_files is not None
    assert as_int(case_files, "total") == 0

    after = await remaining_allowance(
        database, client_id=fixture.client_id, currency="USD"
    )
    assert after.reserved_minor_units == 0
    assert after.remaining_minor_units == after.authorized_minor_units


async def test_expiry_before_the_deadline_changes_nothing(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    fixture, signal_id, attempt_id = await _case_awaiting_response(database, auth_users)
    outcome = await expire_initial_invitation(
        database, outreach_attempt_id=attempt_id
    )
    assert not outcome.expired
    assert "deadline has not passed" in outcome.reason

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state from public.product_signals where id = %s", (signal_id,)
        )
        signal = await cur.fetchone()
    assert signal is not None
    assert as_str(signal, "state") == "awaiting_interview"

    allowance = await remaining_allowance(
        database, client_id=fixture.client_id, currency="USD"
    )
    assert allowance.reserved_minor_units == OFFER_AMOUNT_MINOR_UNITS
