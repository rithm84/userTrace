"""Live smoke: Discord bot identity, guild channels, and one private direct message.

The read smokes prove the bot token, the guild grant, and that the configured watched
channel exists in the configured server.

The direct-message smoke really delivers a message to a person, so it is opt-in: set
`UTRACE_DISCORD_ALLOW_TEST_DM=1` to run it. The connector is additionally constructed with
a DM allowlist containing only `UTRACE_DISCORD_DM_TEST_USER_ID`, so even a mistaken
recipient argument is refused before it reaches Discord.
"""

from __future__ import annotations

import pytest

from app.connectors.discord_connector import DiscordConnector, scoped_provider_identity
from app.connectors.failures import ConnectorFailure, TechnicalCategory
from tests.connectors.conftest import optional_env, require_env

_DM_OPT_IN = "UTRACE_DISCORD_ALLOW_TEST_DM"


def _connector() -> DiscordConnector:
    values = require_env(
        "UTRACE_DISCORD_BOT_TOKEN",
        "UTRACE_DISCORD_SERVER_ID",
        "UTRACE_DISCORD_FEATURE_REQUEST_CHANNEL_ID",
        "UTRACE_DISCORD_DM_TEST_USER_ID",
    )
    return DiscordConnector(
        bot_token=values["UTRACE_DISCORD_BOT_TOKEN"],
        guild_id=values["UTRACE_DISCORD_SERVER_ID"],
        watched_channel_ids=[values["UTRACE_DISCORD_FEATURE_REQUEST_CHANNEL_ID"]],
        direct_message_allowlist=[values["UTRACE_DISCORD_DM_TEST_USER_ID"]],
    )


async def test_bot_user_and_guild_channels_are_readable() -> None:
    values = require_env(
        "UTRACE_DISCORD_SERVER_ID", "UTRACE_DISCORD_FEATURE_REQUEST_CHANNEL_ID"
    )
    async with _connector() as connector:
        bot = await connector.fetch_bot_user()
        assert bot.is_bot
        assert bot.user_id.isdigit()
        assert bot.scoped_key() == scoped_provider_identity(
            guild_id=values["UTRACE_DISCORD_SERVER_ID"], user_id=bot.user_id
        )

        channels = await connector.list_guild_channels()
        assert channels
        watched = [channel for channel in channels if channel.is_watched]
        assert len(watched) == 1
        assert watched[0].channel_id == values["UTRACE_DISCORD_FEATURE_REQUEST_CHANNEL_ID"]


async def test_a_recipient_outside_the_allowlist_is_refused_before_any_send() -> None:
    async with _connector() as connector:
        with pytest.raises(ConnectorFailure) as raised:
            await connector.send_direct_message(
                user_id="000000000000000000", content="this must never be delivered"
            )
        assert raised.value.category is TechnicalCategory.AUTHORIZATION


async def test_a_call_before_open_is_refused() -> None:
    connector = _connector()
    with pytest.raises(ConnectorFailure) as raised:
        await connector.fetch_bot_user()
    assert raised.value.category is TechnicalCategory.CONFIGURATION


async def test_one_direct_message_to_the_designated_test_user() -> None:
    """Opt-in: this delivers a real Discord message to the configured test account."""
    if optional_env(_DM_OPT_IN) != "1":
        pytest.skip(
            f"sending a real direct message is opt-in; set {_DM_OPT_IN}=1 to run it"
        )
    values = require_env("UTRACE_DISCORD_DM_TEST_USER_ID")
    async with _connector() as connector:
        receipt = await connector.send_direct_message(
            user_id=values["UTRACE_DISCORD_DM_TEST_USER_ID"],
            content="uTrace connector smoke test: private outreach path is working.",
        )
    assert receipt.provider == "discord"
    assert receipt.recipient_id == values["UTRACE_DISCORD_DM_TEST_USER_ID"]
    assert receipt.message_id.isdigit()
    assert receipt.sent_at is not None


async def test_watched_channel_messages_can_be_fetched_by_id() -> None:
    """Re-read one message by provider identity, when a message id is configured."""
    message_id = optional_env("UTRACE_DISCORD_SMOKE_MESSAGE_ID")
    if message_id is None:
        pytest.skip(
            "set UTRACE_DISCORD_SMOKE_MESSAGE_ID to a message in the watched channel "
            "to exercise fetch_message"
        )
    values = require_env("UTRACE_DISCORD_FEATURE_REQUEST_CHANNEL_ID")
    async with _connector() as connector:
        receipt = await connector.fetch_message(
            channel_id=values["UTRACE_DISCORD_FEATURE_REQUEST_CHANNEL_ID"],
            message_id=message_id,
        )
    assert receipt.message_id == message_id
    assert receipt.channel_id == values["UTRACE_DISCORD_FEATURE_REQUEST_CHANNEL_ID"]
    assert receipt.created_at is not None
