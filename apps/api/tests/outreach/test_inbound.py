"""Inbound direct messages.

A direct message is not an intake surface. It routes only to the invitation it
answers, an active session, a re-contact, or an explicit preference action, and
never becomes a customer report.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.connectors.ports import InboundDirectMessageReceipt
from app.db.pool import Database
from app.db.rows import as_bool, as_int, as_str
from app.ids import uuid7
from app.intake.worker import route_inbound_direct_message
from app.outreach.inbound import RouteKind, route_direct_message
from app.outreach.reasons import DECLINED, DO_NOT_CONTACT
from tests.cases import seed_awaiting_invitation, seed_product_signal
from tests.supabase_auth import SignedInUser
from tests.workstream import (
    WorkstreamFixture,
    create_workstream_client,
)

CONCERN = "Saving a conversion view would stop the Monday rebuild."


async def _invited(
    database: Database, auth_users: dict[str, SignedInUser]
) -> WorkstreamFixture:
    """Configure a client with one case awaiting Maya's answer."""
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
    await seed_awaiting_invitation(
        database,
        fixture,
        product_signal_id=signal_id,
        participant=fixture.maya,
        bound_provider=participant.identity_provider,
        bound_identity=participant.identity_provider_id,
    )
    return fixture


async def _signal_state(database: Database, *, client_id: object) -> tuple[str, str]:
    """Return the client's single Product Signal state and closure reason."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state, coalesce(closure_reason, '') as closure_reason "
            "from public.product_signals where client_id = %s",
            (client_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    return as_str(row, "state"), as_str(row, "closure_reason")


async def test_a_decline_reply_cancels_the_case(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    fixture = await _invited(database, auth_users)
    route = await route_direct_message(
        database,
        provider_message_id=uuid7().hex,
        sender_provider_identity=fixture.maya.discord_user_id,
        content="No thanks",
        occurred_at=datetime.now(tz=UTC),
    )
    assert route.route_kind == RouteKind.INVITATION_RESPONSE
    assert not route.duplicate

    state, reason = await _signal_state(database, client_id=fixture.client_id)
    assert state == "canceled"
    assert reason == DECLINED

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state from public.outreach_attempts where client_id = %s",
            (fixture.client_id,),
        )
        attempt = await cur.fetchone()
        await cur.execute(
            "select count(*) as total from public.case_files where client_id = %s",
            (fixture.client_id,),
        )
        case_files = await cur.fetchone()
    assert attempt is not None
    assert as_str(attempt, "state") == "declined"
    assert case_files is not None
    assert as_int(case_files, "total") == 0


async def test_a_stop_reply_records_do_not_contact_and_cancels(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    fixture = await _invited(database, auth_users)
    # Delivered as the gateway queues it, through the consumer's own routing.
    route = await route_inbound_direct_message(
        database,
        receipt=InboundDirectMessageReceipt(
            provider="discord",
            message_id=uuid7().hex,
            author_id=fixture.maya.discord_user_id,
            content="stop",
            sent_at=datetime.now(tz=UTC),
        ),
    )
    assert route.route_kind == RouteKind.PREFERENCE_ACTION

    state, reason = await _signal_state(database, client_id=fixture.client_id)
    assert state == "canceled"
    assert reason == DO_NOT_CONTACT

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select do_not_contact from public.users where id = %s",
            (fixture.maya.utrace_user_id,),
        )
        user = await cur.fetchone()
    assert user is not None
    assert as_bool(user, "do_not_contact")


async def test_a_duplicate_delivery_changes_nothing(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    fixture = await _invited(database, auth_users)
    message_id = uuid7().hex
    first = await route_direct_message(
        database,
        provider_message_id=message_id,
        sender_provider_identity=fixture.maya.discord_user_id,
        content="no thanks",
        occurred_at=datetime.now(tz=UTC),
    )
    second = await route_direct_message(
        database,
        provider_message_id=message_id,
        sender_provider_identity=fixture.maya.discord_user_id,
        content="no thanks",
        occurred_at=datetime.now(tz=UTC),
    )
    assert not first.duplicate
    assert second.duplicate

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as total from public.inbound_direct_messages"
        )
        recorded = await cur.fetchone()
    assert recorded is not None
    assert as_int(recorded, "total") == 1


async def test_a_direct_message_with_no_outstanding_outreach_is_unroutable(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    await _invited(database, auth_users)
    route = await route_direct_message(
        database,
        provider_message_id=uuid7().hex,
        sender_provider_identity="900000000000009999",
        content="hello, is anyone there?",
        occurred_at=datetime.now(tz=UTC),
    )
    assert route.route_kind == RouteKind.UNROUTABLE
    assert route.client_id is None

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as total from public.intake_items"
        )
        items = await cur.fetchone()
    assert items is not None
    assert as_int(items, "total") == 1
