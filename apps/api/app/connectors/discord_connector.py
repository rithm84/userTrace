"""Discord Customer Surface: watched-channel intake and private outreach.

Library choice: `discord.py`. A raw gateway client would have to reimplement the identify
and resume handshake, heartbeat and zombied-connection detection, session invalidation,
gateway rate limits, and REST bucket accounting before it could observe a single message
reliably. None of that is uTrace's problem to solve, and the
[intake contract](../../../../docs/arch-components/signal-and-outreach.md#discord-ingestion-and-identity)
depends on not losing or duplicating messages across reconnects. `discord.py` is the
maintained implementation of exactly that layer, exposes both a gateway client and the REST
surface this connector needs, and ships type hints. The cost is a framework-shaped
dependency, which is contained here: the rest of uTrace sees `MessageReceipt` and
`DirectMessageReceipt` from `app.connectors.ports`, never a `discord.Message`.

What this connector does and does not do:

- Only configured watched channels are intake surfaces. A message from any other channel is
  ignored at the boundary, so an unwatched channel cannot become a Product Signal.
- Receipts are immutable records of what the provider reported, including reply and thread
  references. The authenticated Discord message id is the provider idempotency key;
  deduplication and debouncing belong to the control plane, which owns Intake Item ordering.
- Direct messages are private outreach to one recipient. A send returns a durable delivery
  receipt, which is a delivery result and never a Product Signal state change, and never
  proof that the person read it.
- Identity mapping is mechanical: a Discord user id plus its guild becomes a scoped
  provider identity string. Resolving that to a `client_user_id`, a customer account, or a
  membership is the control plane's decision, not this connector's.

Typing in a watched channel is reported as a `TypingReceipt`, a content-free timing
hint the control plane uses to keep an author's batch open while they compose the next
message. It needs the non-privileged guild typing intent.

`message_content` is a privileged gateway intent. Without it enabled for the bot, message
content arrives empty and intake has nothing to route, so `consume` checks the configured
intents before connecting.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta
from types import TracebackType
from typing import Final, Self

import discord
import structlog
from pydantic import BaseModel, ConfigDict

from app.connectors.failures import (
    ConnectorFailure,
    EffectState,
    FailureDetail,
    RetryAdvice,
    TechnicalCategory,
    default_retry_advice,
    redacted_parameters,
    technical_category_for_status,
)
from app.connectors.ports import (
    ChannelMessageReceipt,
    DirectMessageReceipt,
    MessageReceipt,
    TypingReceipt,
)

__all__ = [
    "DiscordConnector",
    "GuildChannelSummary",
    "ProviderIdentity",
    "scoped_provider_identity",
]

_logger = structlog.get_logger(__name__)
_PROVIDER: Final[str] = "discord"
_RECEIPT_QUEUE_SIZE: Final[int] = 1000
#: Discord shows a typing indicator for ten seconds after each typing event, and a
#: client still typing sends another before it lapses.
_TYPING_INDICATOR: Final[timedelta] = timedelta(seconds=10)

type GatewayReceipt = MessageReceipt | TypingReceipt


class ProviderIdentity(BaseModel):
    """A Discord account observed inside one guild.

    This is a scoped provider reference. Knowing it grants no participation authority and
    it never substitutes for a uTrace `client_user_id`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    guild_id: str
    user_id: str
    username: str
    is_bot: bool

    def scoped_key(self) -> str:
        """Return the stable lookup key the control plane maps to a client-scoped user."""
        return scoped_provider_identity(guild_id=self.guild_id, user_id=self.user_id)


def scoped_provider_identity(*, guild_id: str, user_id: str) -> str:
    """Build the provider identity key for one Discord user in one guild.

    Guild-scoped rather than global, because participation and data are scoped per client:
    the same person in two clients' servers is two client-scoped identities.
    """
    return f"discord:{guild_id}:{user_id}"


class GuildChannelSummary(BaseModel):
    """One channel in the configured guild, as intake configuration sees it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    channel_id: str
    name: str
    channel_type: str
    is_watched: bool


class DiscordConnector:
    """Connector for one client's configured Discord server.

    One instance serves one guild. Watched channels are fixed at construction so the set
    of intake surfaces is configuration, not a runtime decision.
    """

    def __init__(
        self,
        *,
        bot_token: str,
        guild_id: str,
        watched_channel_ids: Sequence[str],
        direct_message_allowlist: Sequence[str] | None,
    ) -> None:
        self._bot_token = bot_token
        self._guild_id = guild_id
        self._watched_channel_ids = frozenset(watched_channel_ids)
        self._direct_message_allowlist = (
            frozenset(direct_message_allowlist)
            if direct_message_allowlist is not None
            else None
        )
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.guild_typing = True
        intents.message_content = True
        intents.dm_messages = True
        self._receipts: asyncio.Queue[GatewayReceipt] = asyncio.Queue(
            maxsize=_RECEIPT_QUEUE_SIZE
        )
        self._client = _WatchingClient(
            intents=intents,
            guild_id=guild_id,
            watched_channel_ids=self._watched_channel_ids,
            receipts=self._receipts,
        )
        self._logged_in = False

    async def __aenter__(self) -> Self:
        """Authenticate the bot over REST without opening a gateway connection."""
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the HTTP session and any gateway connection."""
        await self.close()

    async def open(self) -> None:
        """Log in over REST.

        REST login is enough for fetching identities, channels, and messages, and for
        sending a direct message. Gateway consumption is a separate, explicit step.
        """
        try:
            await self._client.login(self._bot_token)
        except discord.LoginFailure as error:
            raise self._failure(
                operation="open",
                category=TechnicalCategory.AUTHENTICATION,
                message="Discord rejected the bot token",
                error=error,
                status=401,
            ) from error
        except discord.HTTPException as error:
            raise self._failure(
                operation="open",
                category=technical_category_for_status(error.status),
                message="Discord login failed",
                error=error,
                status=error.status,
            ) from error
        self._logged_in = True

    async def close(self) -> None:
        """Close the client's transports."""
        if not self._client.is_closed():
            await self._client.close()
        self._logged_in = False

    async def fetch_bot_user(self) -> ProviderIdentity:
        """Return the authenticated bot account.

        Proves the token and the REST path before any intake or outreach runs.
        """
        self._require_login(operation="fetch_bot_user")
        user = self._client.user
        if user is None:
            raise self._contract_violation(
                operation="fetch_bot_user",
                message="login succeeded but no bot account was reported",
            )
        return ProviderIdentity(
            guild_id=self._guild_id,
            user_id=str(user.id),
            username=user.name,
            is_bot=True,
        )

    async def list_guild_channels(self) -> tuple[GuildChannelSummary, ...]:
        """List the configured guild's channels and mark which ones uTrace watches."""
        self._require_login(operation="list_guild_channels")
        try:
            guild = await self._client.fetch_guild(int(self._guild_id))
            channels = await guild.fetch_channels()
        except discord.HTTPException as error:
            raise self._failure(
                operation="list_guild_channels",
                category=technical_category_for_status(error.status),
                message="Discord rejected the guild channel read",
                error=error,
                status=error.status,
            ) from error
        summaries = tuple(
            GuildChannelSummary(
                channel_id=str(channel.id),
                name=channel.name,
                channel_type=str(channel.type),
                is_watched=str(channel.id) in self._watched_channel_ids,
            )
            for channel in channels
        )
        _logger.info(
            "discord.channels.listed",
            guild_id=self._guild_id,
            channel_count=len(summaries),
            watched_count=sum(1 for channel in summaries if channel.is_watched),
        )
        return summaries

    async def fetch_message(self, *, channel_id: str, message_id: str) -> MessageReceipt:
        """Re-read one message by its provider identity.

        Used to rebuild an Intake Item's source record and to fetch the nearby context the
        routing contract permits fetching when a batch explicitly requires it.
        """
        self._require_login(operation="fetch_message")
        try:
            channel = await self._client.fetch_channel(int(channel_id))
            if not isinstance(channel, discord.abc.Messageable):
                raise self._contract_violation(
                    operation="fetch_message",
                    message=f"channel {channel_id} does not carry messages",
                )
            message = await channel.fetch_message(int(message_id))
        except discord.HTTPException as error:
            raise self._failure(
                operation="fetch_message",
                category=technical_category_for_status(error.status),
                message="Discord rejected the message read",
                error=error,
                status=error.status,
            ) from error
        return _to_receipt(message=message, guild_id=self._guild_id)

    async def send_direct_message(
        self, *, user_id: str, content: str
    ) -> DirectMessageReceipt:
        """Send one private message and return its delivery receipt.

        Never retried: Discord offers no idempotency key for a message send, so a repeat
        would deliver a second invitation. An uncertain send is reconciled by reading the
        DM channel before any resend.

        When `direct_message_allowlist` is configured, a recipient outside it is refused.
        The allowlist exists so a non-production configuration cannot message a real person.
        """
        self._require_login(operation="send_direct_message")
        if (
            self._direct_message_allowlist is not None
            and user_id not in self._direct_message_allowlist
        ):
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="send_direct_message",
                    category=TechnicalCategory.AUTHORIZATION,
                    retry_advice=RetryAdvice.AFTER_CONDITION_CHANGE,
                    effect_state=EffectState.ABSENT,
                    message=f"recipient {user_id} is not in the configured DM allowlist",
                    request_parameters=redacted_parameters(
                        {"user_id": user_id, "guild_id": self._guild_id}
                    ),
                )
            )
        try:
            user = await self._client.fetch_user(int(user_id))
            message = await user.send(content)
        except discord.Forbidden as error:
            raise self._failure(
                operation="send_direct_message",
                category=TechnicalCategory.AUTHORIZATION,
                message="the recipient does not accept direct messages from this bot",
                error=error,
                status=error.status,
            ) from error
        except discord.HTTPException as error:
            raise self._failure(
                operation="send_direct_message",
                category=technical_category_for_status(error.status),
                message="Discord rejected the direct message",
                error=error,
                status=error.status,
            ) from error
        receipt = DirectMessageReceipt(
            provider="discord",
            message_id=str(message.id),
            channel_id=str(message.channel.id),
            recipient_id=user_id,
            sent_at=message.created_at,
        )
        _logger.info(
            "discord.dm.sent",
            recipient_id=user_id,
            message_id=receipt.message_id,
            content_length=len(content),
        )
        return receipt

    async def reply_in_channel(
        self, *, channel_id: str, message_id: str, content: str
    ) -> ChannelMessageReceipt:
        """Reply publicly to one watched-channel message without pinging its author.

        Never retried, for the same reason as a direct message. Only a watched channel
        is answered: anywhere else the bot has no business posting.
        """
        self._require_login(operation="reply_in_channel")
        if channel_id not in self._watched_channel_ids:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="reply_in_channel",
                    category=TechnicalCategory.AUTHORIZATION,
                    retry_advice=RetryAdvice.NEVER,
                    effect_state=EffectState.ABSENT,
                    message=f"channel {channel_id} is not a watched channel",
                    request_parameters=redacted_parameters(
                        {"channel_id": channel_id, "guild_id": self._guild_id}
                    ),
                )
            )
        try:
            channel = await self._client.fetch_channel(int(channel_id))
            if not isinstance(channel, discord.abc.Messageable):
                raise self._contract_violation(
                    operation="reply_in_channel",
                    message=f"channel {channel_id} does not accept messages",
                )
            message = await channel.send(
                content,
                reference=discord.MessageReference(
                    message_id=int(message_id),
                    channel_id=int(channel_id),
                    fail_if_not_exists=False,
                ),
                mention_author=False,
            )
        except discord.Forbidden as error:
            raise self._failure(
                operation="reply_in_channel",
                category=TechnicalCategory.AUTHORIZATION,
                message="the bot may not post in this channel",
                error=error,
                status=error.status,
            ) from error
        except discord.HTTPException as error:
            raise self._failure(
                operation="reply_in_channel",
                category=technical_category_for_status(error.status),
                message="Discord rejected the channel reply",
                error=error,
                status=error.status,
            ) from error
        receipt = ChannelMessageReceipt(
            provider="discord",
            message_id=str(message.id),
            channel_id=channel_id,
            sent_at=message.created_at,
        )
        _logger.info(
            "discord.channel_reply.sent",
            channel_id=channel_id,
            message_id=receipt.message_id,
            reply_to_message_id=message_id,
        )
        return receipt

    @asynccontextmanager
    async def consume(self) -> AsyncIterator[AsyncIterator[GatewayReceipt]]:
        """Open the gateway and yield an iterator of watched-channel message and typing receipts.

        The gateway connection lives for the duration of the context. Leaving the context
        closes it; `discord.py` handles resume and reconnect inside it.
        """
        self._require_login(operation="consume")
        if not self._client.intents.message_content:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="consume",
                    category=TechnicalCategory.CONFIGURATION,
                    retry_advice=RetryAdvice.AFTER_CONDITION_CHANGE,
                    effect_state=EffectState.ABSENT,
                    message=(
                        "the message_content privileged intent is off; intake would "
                        "receive empty message bodies"
                    ),
                )
            )
        gateway = asyncio.create_task(self._client.connect(reconnect=True))
        _logger.info(
            "discord.gateway.opened",
            guild_id=self._guild_id,
            watched_channels=len(self._watched_channel_ids),
        )
        try:
            yield self._iterate_receipts(gateway)
        finally:
            gateway.cancel()
            await asyncio.gather(gateway, return_exceptions=True)
            _logger.info("discord.gateway.closed", guild_id=self._guild_id)

    async def _iterate_receipts(
        self, gateway: asyncio.Task[None]
    ) -> AsyncIterator[GatewayReceipt]:
        """Yield receipts as the gateway observes them, and raise when it stops.

        The gateway task owns `discord.py`'s own resume and reconnect loop, so its
        completion means the connection ended for a reason that loop could not recover
        from. Watching it here is what makes the consumer runnable: otherwise the
        iterator would wait forever on a queue nothing can fill any more, and intake
        would stall with no error anywhere.
        """
        while True:
            receipt = asyncio.ensure_future(self._receipts.get())
            try:
                done, _ = await asyncio.wait(
                    (receipt, gateway), return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                if not receipt.done():
                    receipt.cancel()
            if receipt in done:
                yield receipt.result()
                continue
            raise self._gateway_lost(gateway)

    def _gateway_lost(self, gateway: asyncio.Task[None]) -> ConnectorFailure:
        """Build the failure for a gateway connection that ended on its own."""
        error = None if gateway.cancelled() else gateway.exception()
        return ConnectorFailure(
            FailureDetail(
                provider=_PROVIDER,
                operation="consume",
                category=TechnicalCategory.PROVIDER_UNAVAILABLE,
                retry_advice=RetryAdvice.AFTER_DELAY,
                effect_state=EffectState.ABSENT,
                message=(
                    "the Discord gateway connection ended; watched channels are no "
                    "longer delivering messages"
                ),
                request_parameters=redacted_parameters({"guild_id": self._guild_id}),
                provider_error_code=None if error is None else type(error).__name__,
                response_body=None if error is None else str(error),
            )
        )

    def _require_login(self, *, operation: str) -> None:
        """Refuse a call made before `open()`."""
        if not self._logged_in:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation=operation,
                    category=TechnicalCategory.CONFIGURATION,
                    retry_advice=RetryAdvice.AFTER_CONDITION_CHANGE,
                    effect_state=EffectState.ABSENT,
                    message="connector is not logged in; call open() first",
                )
            )

    def _failure(
        self,
        *,
        operation: str,
        category: TechnicalCategory,
        message: str,
        error: Exception,
        status: int | None,
    ) -> ConnectorFailure:
        """Build a typed failure from a Discord HTTP error."""
        return ConnectorFailure(
            FailureDetail(
                provider=_PROVIDER,
                operation=operation,
                category=category,
                retry_advice=default_retry_advice(category),
                effect_state=EffectState.ABSENT,
                message=message,
                request_parameters=redacted_parameters({"guild_id": self._guild_id}),
                status_code=status,
                provider_error_code=type(error).__name__,
                response_body=str(error),
            )
        )

    def _contract_violation(self, *, operation: str, message: str) -> ConnectorFailure:
        """Build the failure for a Discord answer uTrace cannot accept."""
        return ConnectorFailure(
            FailureDetail(
                provider=_PROVIDER,
                operation=operation,
                category=TechnicalCategory.VALIDATION,
                retry_advice=RetryAdvice.NEVER,
                effect_state=EffectState.ABSENT,
                message=message,
                request_parameters=redacted_parameters({"guild_id": self._guild_id}),
            )
        )


class _WatchingClient(discord.Client):
    """Gateway client that turns watched-channel messages into receipts.

    Filtering happens here, at the boundary: a message from an unwatched channel, another
    guild, or the bot itself never becomes a receipt.
    """

    def __init__(
        self,
        *,
        intents: discord.Intents,
        guild_id: str,
        watched_channel_ids: frozenset[str],
        receipts: asyncio.Queue[GatewayReceipt],
    ) -> None:
        super().__init__(intents=intents)
        self._guild_id = guild_id
        self._watched_channel_ids = watched_channel_ids
        self._receipt_queue = receipts

    async def on_message(self, message: discord.Message) -> None:
        """Queue a receipt for each watched-channel message."""
        if message.guild is None or str(message.guild.id) != self._guild_id:
            return
        if str(message.channel.id) not in self._watched_channel_ids:
            return
        if self.user is not None and message.author.id == self.user.id:
            return
        receipt = _to_receipt(message=message, guild_id=self._guild_id)
        try:
            self._receipt_queue.put_nowait(receipt)
        except asyncio.QueueFull:
            _logger.warning(
                "discord.receipt.dropped",
                guild_id=self._guild_id,
                channel_id=receipt.channel_id,
                message_id=receipt.message_id,
                queue_size=_RECEIPT_QUEUE_SIZE,
            )

    async def on_raw_typing(self, payload: discord.RawTypingEvent) -> None:
        """Queue a typing receipt for someone composing in a watched channel.

        The raw event arrives whether or not the member is cached.
        """
        if payload.guild_id is None or str(payload.guild_id) != self._guild_id:
            return
        if str(payload.channel_id) not in self._watched_channel_ids:
            return
        if self.user is not None and payload.user_id == self.user.id:
            return
        receipt = TypingReceipt(
            provider="discord",
            channel_id=str(payload.channel_id),
            guild_id=self._guild_id,
            author_id=str(payload.user_id),
            active_until=payload.timestamp + _TYPING_INDICATOR,
        )
        try:
            self._receipt_queue.put_nowait(receipt)
        except asyncio.QueueFull:
            _logger.warning(
                "discord.typing.dropped",
                guild_id=self._guild_id,
                channel_id=receipt.channel_id,
                queue_size=_RECEIPT_QUEUE_SIZE,
            )


def _to_receipt(*, message: discord.Message, guild_id: str) -> MessageReceipt:
    """Convert a provider message into the immutable intake source record."""
    reference = message.reference
    reply_to = (
        str(reference.message_id)
        if reference is not None and reference.message_id is not None
        else None
    )
    thread_id = str(message.thread.id) if message.thread is not None else None
    if thread_id is None and isinstance(message.channel, discord.Thread):
        thread_id = str(message.channel.id)
    return MessageReceipt(
        provider="discord",
        message_id=str(message.id),
        channel_id=str(message.channel.id),
        guild_id=guild_id,
        author_id=str(message.author.id),
        author_is_bot=message.author.bot,
        content=message.content,
        created_at=message.created_at,
        edited_at=message.edited_at,
        reply_to_message_id=reply_to,
        thread_id=thread_id,
    )
