"""Posting into the client's configured Customer Surface.

The rehearsal puts a real message in a real watched channel so the intake path
that runs is the one the Discord connector drives, not a reconstruction of it.
The harness only writes the message; every decision after that belongs to the
control plane.
"""

from __future__ import annotations

from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, Field

from demo_harness.settings import DiscordSettings

__all__ = ["DiscordError", "PostedMessage", "post_message"]

_API: Final[str] = "https://discord.com/api/v10"
_TIMEOUT_SECONDS: Final[float] = 30.0


class DiscordError(RuntimeError):
    """Discord refused the request, or answered something unreadable."""


class Author(BaseModel):
    """The identity Discord attributes a message to."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str = Field(min_length=1)
    username: str
    bot: bool = False


class PostedMessage(BaseModel):
    """One message this harness posted into a watched channel."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    content: str
    author: Author


async def post_message(settings: DiscordSettings, *, content: str) -> PostedMessage:
    """Post one message into the configured feature-request channel."""
    url = f"{_API}/channels/{settings.feature_request_channel_id}/messages"
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as http:
        response = await http.post(
            url,
            headers={
                "Authorization": f"Bot {settings.bot_token}",
                "Content-Type": "application/json",
            },
            json={"content": content},
        )
    if response.status_code not in (httpx.codes.OK, httpx.codes.CREATED):
        raise DiscordError(
            f"Discord refused to post into channel "
            f"{settings.feature_request_channel_id}: {response.status_code} {response.text}"
        )
    return PostedMessage.model_validate(response.json())
