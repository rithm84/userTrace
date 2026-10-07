"""Provider-neutral connector interfaces the control plane adopts at integration.

Each port names one architectural role from the
[active provider lookup](../../../../docs/ARCHITECTURE.md#active-provider-lookup) and
exposes only the operations that role needs. Ports are `Protocol`s so the control plane
depends on the role, not on a vendor class, and so a second provider for the same role
can be added without touching callers.

Two rules hold for every port:

- Failures are `ConnectorFailure` from `app.connectors.failures`. A port never returns a
  sentinel or a partially valid object in place of an error.
- A port never decides authority. It executes an already-authorized request and reports
  what the provider did. Provider identifiers stay scoped strings and never stand in for
  uTrace domain identity.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

__all__ = [
    "ChannelMessageReceipt",
    "CustomerSurfacePort",
    "DirectMessageReceipt",
    "InboundDirectMessageReceipt",
    "MessageReceipt",
    "TypingReceipt",
    "WorkspaceExecution",
]


class _Frozen(BaseModel):
    """Immutable, strictly validated base for every neutral port payload.

    `extra="ignore"` keeps a provider free to add unrelated fields without breaking
    ingestion, while every field uTrace needs stays required.
    """

    model_config = ConfigDict(frozen=True, extra="ignore", strict=False)


class WorkspaceExecution(_Frozen):
    """The result of one process execution inside an isolated workspace."""

    command: str
    exit_code: int
    stdout: str
    duration_seconds: float


class MessageReceipt(_Frozen):
    """An immutable record of one observed Customer Surface message.

    The receipt is the intake source record. It preserves reply and thread references so
    routing context can be assembled without a second provider read.
    """

    provider: Literal["discord"]
    message_id: str
    channel_id: str
    guild_id: str | None
    author_id: str
    author_is_bot: bool
    content: str
    created_at: datetime
    edited_at: datetime | None
    reply_to_message_id: str | None
    thread_id: str | None


class TypingReceipt(_Frozen):
    """One author composing a message in a watched channel, as the provider reported it.

    A timing hint only: it carries no content, is never a customer report, and only
    keeps that author's open intake batch from sealing before the message arrives.
    """

    provider: Literal["discord"]
    channel_id: str
    guild_id: str
    author_id: str
    active_until: datetime
    """When the provider's typing indicator lapses unless the author keeps typing."""


class ChannelMessageReceipt(_Frozen):
    """Proof that the provider accepted one public reply in a watched channel."""

    provider: Literal["discord"]
    message_id: str
    channel_id: str
    sent_at: datetime


class InboundDirectMessageReceipt(_Frozen):
    """One private message a person sent the bot.

    A direct message is never an intake surface: it can answer the person's own
    invitation or session, or carry a preference such as stopping contact, and
    nothing else.
    """

    provider: Literal["discord"]
    message_id: str
    author_id: str
    content: str
    sent_at: datetime


class DirectMessageReceipt(_Frozen):
    """Proof that the provider accepted one private message for one recipient.

    Acceptance is a durable delivery result, not proof that the person read it, and never
    a Product Signal state change.
    """

    provider: Literal["discord"]
    message_id: str
    channel_id: str
    recipient_id: str
    sent_at: datetime


@runtime_checkable
@runtime_checkable
class CustomerSurfacePort(Protocol):
    """Read and private-write access to one configured Customer Surface."""

    async def fetch_message(self, *, channel_id: str, message_id: str) -> MessageReceipt:
        """Re-read one message by its provider identity."""
        ...

    async def send_direct_message(self, *, user_id: str, content: str) -> DirectMessageReceipt:
        """Send one private message and return its delivery receipt."""
        ...

    async def reply_in_channel(
        self, *, channel_id: str, message_id: str, content: str
    ) -> ChannelMessageReceipt:
        """Reply publicly to one watched-channel message and return its receipt."""
        ...
