"""Environment-backed configuration, split by the concern that needs it.

Each command loads only the settings it uses, so a missing Discord token never
blocks ``settle-invoice`` and a missing Stripe key never blocks ``watch``. A
missing variable raises immediately and names itself; nothing is defaulted and
nothing is guessed.
"""

from __future__ import annotations

import os
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ControlPlaneSettings",
    "DatabaseSettings",
    "DiscordSettings",
    "MissingVariableError",
    "StripeSettings",
    "require",
]

_STAFF_EMAIL: Final[str] = "UTRACE_DEMO_STAFF_EMAIL"
_STAFF_PASSWORD: Final[str] = "UTRACE_DEMO_STAFF_PASSWORD"


class MissingVariableError(RuntimeError):
    """A required environment variable is unset or empty."""

    def __init__(self, variable: str) -> None:
        """Name the variable so the operator can fix it without reading code."""
        super().__init__(
            f"{variable} is not set; source the repository .env.local before running the harness"
        )
        self.variable = variable


def require(variable: str) -> str:
    """Return one required environment variable, or raise naming it."""
    value = os.environ.get(variable)
    if value is None or not value.strip():
        raise MissingVariableError(variable)
    return value.strip()


class Frozen(BaseModel):
    """Base for every settings block: immutable and closed to extra fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ControlPlaneSettings(Frozen):
    """Where the target control plane is and how the demo staff user signs in."""

    api_base_url: str = Field(min_length=1)
    supabase_url: str = Field(min_length=1)
    supabase_publishable_key: str = Field(min_length=1)
    staff_email: str = Field(min_length=3)
    staff_password: str = Field(min_length=1)

    @staticmethod
    def from_environment() -> ControlPlaneSettings:
        """Read the control-plane block from the process environment."""
        return ControlPlaneSettings(
            api_base_url=require("UTRACE_DEMO_API_BASE_URL").rstrip("/"),
            supabase_url=require("SUPABASE_URL").rstrip("/"),
            supabase_publishable_key=require("UTRACE_SUPABASE_PUBLISHABLE_KEY"),
            staff_email=require(_STAFF_EMAIL),
            staff_password=require(_STAFF_PASSWORD),
        )


class DiscordSettings(Frozen):
    """The Customer Surface the rehearsal posts its canonical message into."""

    bot_token: str = Field(min_length=1)
    guild_id: str = Field(min_length=1)
    feature_request_channel_id: str = Field(min_length=1)
    maya_discord_user_id: str = Field(min_length=1)

    @staticmethod
    def from_environment() -> DiscordSettings:
        """Read the Discord block from the process environment."""
        return DiscordSettings(
            bot_token=require("UTRACE_DISCORD_BOT_TOKEN"),
            guild_id=require("UTRACE_DISCORD_SERVER_ID"),
            feature_request_channel_id=require("UTRACE_DISCORD_FEATURE_REQUEST_CHANNEL_ID"),
            maya_discord_user_id=require("UTRACE_DEMO_MAYA_DISCORD_USER_ID"),
        )


class StripeSettings(Frozen):
    """The uTrace-controlled sandbox that stands in for Shopalytics' billing."""

    secret_key: str = Field(min_length=1)
    dudulemon_customer_id: str = Field(min_length=1)

    @staticmethod
    def from_environment() -> StripeSettings:
        """Read the Stripe block from the process environment."""
        return StripeSettings(
            secret_key=require("STRIPE_SANDBOX_SECRET_KEY"),
            dudulemon_customer_id=require("UTRACE_STRIPE_DUDULEMON_CUSTOMER_ID"),
        )


class DatabaseSettings(Frozen):
    """The target's Postgres DSN. Only ``reset-target`` reads this block."""

    dsn: str = Field(min_length=1)

    @staticmethod
    def from_environment() -> DatabaseSettings:
        """Read the database block from the process environment."""
        return DatabaseSettings(dsn=require("UTRACE_DATABASE_URL"))
