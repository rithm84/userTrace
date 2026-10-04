"""Private outreach against the real Discord surface.

The reservation, the invitation, the delivery Result, and the cancellation paths
all run against the real database. The one test that delivers a direct message
is opt-in and targets only the configured test account.
"""

from __future__ import annotations

from uuid import UUID

from app.connectors.discord_connector import DiscordConnector
from app.db.pool import Database
from app.db.rows import as_int, as_str
from app.ids import uuid7
from app.intake.settings import get_intake_settings
from app.outreach.delivery import UncertainDeliveryError
from app.outreach.dispatch import send_initial_invitation
from app.outreach.offers import remaining_allowance
from app.outreach.reasons import CONTACT_BUDGET_EXHAUSTED, UNREACHABLE
from tests.cases import seed_product_signal
from tests.conftest import require_env
from tests.supabase_auth import SignedInUser
from tests.workstream import (
    OFFER_AMOUNT_MINOR_UNITS,
    Participant,
    WorkstreamFixture,
    create_workstream_client,
    discord_connector,
    require_dm_opt_in,
)

CONCERN = (
    "I keep rebuilding the same conversion filters every Monday. Can Shopalytics let "
    "me save a view and reopen it later?"
)


async def _fixture(
    database: Database, auth_users: dict[str, SignedInUser]
) -> WorkstreamFixture:
    """One configured client with Outreach Offers enabled."""
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


def _connector(
    fixture: WorkstreamFixture, *, allow_recipient: bool
) -> DiscordConnector:
    """Build the surface connector, optionally with an empty DM allowlist.

    An empty allowlist makes the connector refuse the recipient before the send
    reaches Discord, which is how the unreachable path is proved without
    delivering anything.
    """
    if allow_recipient:
        return discord_connector(
            guild_id=fixture.guild_id, channel_id=fixture.channel_id
        )
    return DiscordConnector(
        bot_token=require_env("UTRACE_DISCORD_BOT_TOKEN"),
        guild_id=fixture.guild_id,
        watched_channel_ids=[fixture.channel_id],
        direct_message_allowlist=[],
    )


async def _counts(database: Database, *, product_signal_id: UUID) -> dict[str, int]:
    """Return the row counts one dispatch is allowed to produce exactly once."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select
              (select count(*) from public.outreach_offers
                where product_signal_id = %s) as offers,
              (select count(*) from public.offer_reservations as reservations
                 join public.outreach_offers as offers
                   on offers.id = reservations.outreach_offer_id
                where offers.product_signal_id = %s) as reservations,
              (select count(*) from public.outreach_attempts
                where product_signal_id = %s) as attempts,
              (select count(*) from public.invitations
                where product_signal_id = %s) as invitations,
              (select count(*) from public.outreach_deliveries as deliveries
                 join public.outreach_attempts as attempts
                   on attempts.id = deliveries.outreach_attempt_id
                where attempts.product_signal_id = %s) as deliveries
            """,
            (product_signal_id,) * 5,
        )
        row = await cur.fetchone()
    assert row is not None
    return {key: as_int(row, key) for key in row}


async def test_one_reservation_and_one_direct_message_per_invitation(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    require_dm_opt_in()
    fixture = await _fixture(database, auth_users)
    signal_id = await seed_product_signal(
        database, fixture, reporter=fixture.maya, excerpt=CONCERN
    )
    settings = get_intake_settings()
    connector = _connector(fixture, allow_recipient=True)
    await connector.open()
    try:
        outcome = await send_initial_invitation(
            database,
            connector,
            settings,
            client_id=fixture.client_id,
            product_signal_id=signal_id,
        )
    finally:
        await connector.close()

    assert outcome.delivered
    assert outcome.state == "awaiting_response"
    counts = await _counts(database, product_signal_id=signal_id)
    assert counts == {
        "offers": 1,
        "reservations": 1,
        "attempts": 1,
        "invitations": 1,
        "deliveries": 1,
    }

    allowance = await remaining_allowance(
        database, client_id=fixture.client_id, currency="USD"
    )
    assert allowance.reserved_minor_units == OFFER_AMOUNT_MINOR_UNITS

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select effect_state, disposition, provider_message_ref "
            "from public.outreach_deliveries",
        )
        delivery = await cur.fetchone()
    assert delivery is not None
    assert as_str(delivery, "effect_state") == "completed"
    assert as_str(delivery, "disposition") == "succeeded"
    assert as_str(delivery, "provider_message_ref")

    # A replay finds the case already dispatched and sends nothing further.
    connector = _connector(fixture, allow_recipient=True)
    await connector.open()
    try:
        replay = await send_initial_invitation(
            database,
            connector,
            settings,
            client_id=fixture.client_id,
            product_signal_id=signal_id,
        )
    finally:
        await connector.close()
    assert replay.delivered
    assert "already dispatched" in replay.reason
    assert await _counts(database, product_signal_id=signal_id) == counts


async def test_an_unreachable_recipient_cancels_and_releases_the_reservation(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    fixture = await _fixture(database, auth_users)
    signal_id = await seed_product_signal(
        database, fixture, reporter=fixture.maya, excerpt=CONCERN
    )
    settings = get_intake_settings()
    connector = _connector(fixture, allow_recipient=False)
    await connector.open()
    try:
        outcome = await send_initial_invitation(
            database,
            connector,
            settings,
            client_id=fixture.client_id,
            product_signal_id=signal_id,
        )
    finally:
        await connector.close()

    assert not outcome.delivered
    assert outcome.state == "unreached"
    assert outcome.reason == UNREACHABLE

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state, closure_reason from public.product_signals where id = %s",
            (signal_id,),
        )
        signal = await cur.fetchone()
        await cur.execute(
            "select count(*) as total from public.case_files where client_id = %s",
            (fixture.client_id,),
        )
        case_files = await cur.fetchone()
        await cur.execute(
            "select revoked_at from public.invitations where product_signal_id = %s",
            (signal_id,),
        )
        invitation = await cur.fetchone()
    assert signal is not None
    assert as_str(signal, "state") == "canceled"
    assert as_str(signal, "closure_reason") == UNREACHABLE
    assert case_files is not None
    assert as_int(case_files, "total") == 0
    assert invitation is not None
    assert invitation["revoked_at"] is not None

    allowance = await remaining_allowance(
        database, client_id=fixture.client_id, currency="USD"
    )
    assert allowance.reserved_minor_units == 0
    assert allowance.remaining_minor_units == allowance.authorized_minor_units


async def test_contact_budget_exhaustion_cancels_without_reserving(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    fixture = await _fixture(database, auth_users)
    signal_id = await seed_product_signal(
        database, fixture, reporter=fixture.maya, excerpt=CONCERN
    )
    await _consume_person_budget(database, fixture, participant=fixture.maya)

    settings = get_intake_settings()
    connector = _connector(fixture, allow_recipient=False)
    await connector.open()
    try:
        outcome = await send_initial_invitation(
            database,
            connector,
            settings,
            client_id=fixture.client_id,
            product_signal_id=signal_id,
        )
    finally:
        await connector.close()

    assert outcome.reason == CONTACT_BUDGET_EXHAUSTED
    assert outcome.state == "canceled"
    counts = await _counts(database, product_signal_id=signal_id)
    assert counts["offers"] == 0
    assert counts["reservations"] == 0
    assert counts["invitations"] == 0

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state, closure_reason from public.product_signals where id = %s",
            (signal_id,),
        )
        signal = await cur.fetchone()
    assert signal is not None
    assert as_str(signal, "state") == "canceled"
    assert as_str(signal, "closure_reason") == CONTACT_BUDGET_EXHAUSTED


async def _consume_person_budget(
    database: Database, fixture: WorkstreamFixture, *, participant: Participant
) -> None:
    """Record enough delivered contacts to exhaust the pinned per-person budget."""
    budget = fixture.release.policy.contact_budget.per_person
    other_signal = await seed_product_signal(
        database, fixture, reporter=participant, excerpt="an earlier concern"
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        for index in range(budget):
            await cur.execute(
                """
                insert into public.outreach_attempts (
                    id, client_id, product_signal_id, client_user_id, purpose, state,
                    dispatched_at, expires_at
                )
                values (%s, %s, %s, %s, %s, 'expired', now(),
                        now() + interval '1 hour')
                """,
                (
                    uuid7(),
                    fixture.client_id,
                    other_signal,
                    participant.client_user_id,
                    "return_reminder" if index else "initial_invitation",
                ),
            )


def test_an_uncertain_delivery_refuses_to_resend() -> None:
    """The typed refusal names the attempt and its command, and advises no retry."""
    error = UncertainDeliveryError(
        outreach_attempt_id=UUID(int=1), command_id=UUID(int=2)
    )
    assert "reconcile the direct-message channel" in error.reason
    assert str(UUID(int=1)) in error.reason
