"""Watched-channel ingestion against the real Discord surface.

Every receipt here is a message the bot actually posted in the configured
watched channel and read back through the connector, so deduplication,
debouncing, and the bot-author refusal are proved on genuine provider data.
"""

from __future__ import annotations

import pytest

from app.clients.configuration import load_active_release
from app.db.pool import Database
from app.db.rows import as_int, as_str
from app.intake.errors import UnattributedAuthorError, UnwatchedSurfaceError
from app.intake.messages import DebounceWindow, record_message, seal_batch
from tests.supabase_auth import SignedInUser
from tests.workstream import (
    WorkstreamFixture,
    attribute_author,
    create_workstream_client,
    post_fixture_message,
    receipt_for,
)

_WINDOW = DebounceWindow(quiet_seconds=30, maximum_seconds=120)


async def _fixture(
    database: Database, auth_users: dict[str, SignedInUser]
) -> WorkstreamFixture:
    """Configure one client with Maya attributed to the posting bot account."""
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
    return fixture


async def test_a_bot_authored_message_is_refused_without_an_attribution(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    fixture = await _fixture(database, auth_users)
    message_id = await post_fixture_message(
        channel_id=fixture.channel_id,
        content="uTrace ingestion fixture: unattributed bot post.",
    )
    receipt = await receipt_for(
        guild_id=fixture.guild_id,
        channel_id=fixture.channel_id,
        message_id=message_id,
    )
    assert receipt.author_is_bot

    with pytest.raises(UnattributedAuthorError) as refused:
        await record_message(
            database,
            client_id=fixture.client_id,
            release=fixture.release,
            receipt=receipt,
            window=_WINDOW,
        )
    assert refused.value.author_provider_identity == receipt.author_id


async def test_a_duplicate_provider_message_id_collapses(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    fixture = await _fixture(database, auth_users)
    await attribute_author(
        database,
        client_id=fixture.client_id,
        author_provider_identity=fixture.bot_user_id,
        participant=fixture.maya,
    )
    message_id = await post_fixture_message(
        channel_id=fixture.channel_id,
        content="uTrace ingestion fixture: duplicate delivery.",
    )
    receipt = await receipt_for(
        guild_id=fixture.guild_id,
        channel_id=fixture.channel_id,
        message_id=message_id,
    )

    first = await record_message(
        database,
        client_id=fixture.client_id,
        release=fixture.release,
        receipt=receipt,
        window=_WINDOW,
    )
    second = await record_message(
        database,
        client_id=fixture.client_id,
        release=fixture.release,
        receipt=receipt,
        window=_WINDOW,
    )
    assert not first.duplicate
    assert second.duplicate
    assert second.intake_batch_id == first.intake_batch_id

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as total from public.intake_messages where client_id = %s",
            (fixture.client_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert as_int(row, "total") == 1


async def test_one_author_in_one_channel_debounces_into_one_ordered_item(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    fixture = await _fixture(database, auth_users)
    await attribute_author(
        database,
        client_id=fixture.client_id,
        author_provider_identity=fixture.bot_user_id,
        participant=fixture.maya,
    )
    contents = [
        "uTrace ingestion fixture: first half of the thought.",
        "uTrace ingestion fixture: and the second half.",
    ]
    batch_ids = []
    for content in contents:
        message_id = await post_fixture_message(
            channel_id=fixture.channel_id, content=content
        )
        receipt = await receipt_for(
            guild_id=fixture.guild_id,
            channel_id=fixture.channel_id,
            message_id=message_id,
        )
        recorded = await record_message(
            database,
            client_id=fixture.client_id,
            release=fixture.release,
            receipt=receipt,
            window=_WINDOW,
        )
        batch_ids.append(recorded.intake_batch_id)
    assert batch_ids[0] == batch_ids[1]

    sealed = await seal_batch(database, intake_batch_id=batch_ids[0])
    assert sealed.message_count == 2

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select payload, client_user_id, customer_account_id, state "
            "from public.intake_items where id = %s",
            (sealed.intake_item_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    ordered = [message["content"] for message in row["payload"]["messages"]]
    assert ordered == contents
    assert row["client_user_id"] == fixture.maya.client_user_id
    assert row["customer_account_id"] == fixture.dudulemon_account_id
    assert as_str(row, "state") == "received"

    # Sealing is idempotent: the batch is closed and names the item it produced.
    again = await seal_batch(database, intake_batch_id=batch_ids[0])
    assert again.intake_item_id == sealed.intake_item_id


async def test_an_unwatched_channel_is_refused_before_anything_is_written(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    fixture = await _fixture(database, auth_users)
    await attribute_author(
        database,
        client_id=fixture.client_id,
        author_provider_identity=fixture.bot_user_id,
        participant=fixture.maya,
    )
    message_id = await post_fixture_message(
        channel_id=fixture.channel_id,
        content="uTrace ingestion fixture: wrong channel claim.",
    )
    receipt = await receipt_for(
        guild_id=fixture.guild_id,
        channel_id=fixture.channel_id,
        message_id=message_id,
    )
    elsewhere = receipt.model_copy(update={"channel_id": "123456789012345678"})

    release = await load_active_release(database, client_id=fixture.client_id)
    with pytest.raises(UnwatchedSurfaceError):
        await record_message(
            database,
            client_id=fixture.client_id,
            release=release,
            receipt=elsewhere,
            window=_WINDOW,
        )
