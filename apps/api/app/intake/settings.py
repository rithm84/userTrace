"""Configuration this workstream's worker and outreach path read.

Every value is required. An unset variable fails start-up with the variable's
name rather than letting a debounce window, a bot token, or the session link
origin default to something wrong.

The control plane's own configuration stays in `app.settings`; these values are
additional, so they live beside the code that reads them.
"""

from functools import lru_cache

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.settings import ConfigurationError, _offending_variables

__all__ = ["IntakeOutreachSettings", "get_intake_settings"]


class IntakeOutreachSettings(BaseSettings):
    """Intake ingestion, routing, and private outreach configuration."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    discord_bot_token: str = Field(validation_alias="UTRACE_DISCORD_BOT_TOKEN")
    jev_api_key: str = Field(validation_alias="VERCEL_AI_GATEWAY_API_KEY")
    jev_model_id: str = Field(validation_alias="UTRACE_JEV_MODEL_ID")
    jev_timeout_seconds: float = Field(
        validation_alias="UTRACE_JEV_TIMEOUT_SECONDS", gt=0
    )
    web_origin: str = Field(validation_alias="UTRACE_WEB_ORIGIN")
    debounce_quiet_seconds: int = Field(
        validation_alias="UTRACE_INTAKE_DEBOUNCE_QUIET_SECONDS", gt=0
    )
    debounce_maximum_seconds: int = Field(
        validation_alias="UTRACE_INTAKE_DEBOUNCE_MAX_SECONDS", gt=0
    )

    def session_link(self, token: str) -> str:
        """Return the uTrace Session Link for one invitation token."""
        return f"{self.web_origin.rstrip('/')}/invitations/{token}"


@lru_cache(maxsize=1)
def get_intake_settings() -> IntakeOutreachSettings:
    try:
        return IntakeOutreachSettings()  # type: ignore[call-arg]
    except ValidationError as error:
        raise ConfigurationError(_offending_variables(error)) from None
