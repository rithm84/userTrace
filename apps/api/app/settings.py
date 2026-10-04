"""Runtime configuration.

Every value is required. An unset variable fails start-up with the variable's
name rather than letting a lane, a signing key, or a database URL default to
something permissive.
"""

from functools import lru_cache

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["ConfigurationError", "Settings", "get_settings"]


class ConfigurationError(RuntimeError):
    """Configuration is missing or invalid.

    The message names the offending variables and nothing else: a validation
    error carries the submitted values, and those values are credentials.
    """

    def __init__(self, variables: tuple[str, ...]) -> None:
        super().__init__(
            "control-plane configuration is incomplete; "
            f"check these variables: {', '.join(variables)}"
        )
        self.variables = variables


class Settings(BaseSettings):
    """Control-plane configuration read from the process environment."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    database_url: str = Field(validation_alias="UTRACE_DATABASE_URL")
    database_pool_min_size: int = Field(default=1, validation_alias="UTRACE_DB_POOL_MIN")
    database_pool_max_size: int = Field(default=8, validation_alias="UTRACE_DB_POOL_MAX")

    supabase_url: str = Field(validation_alias="SUPABASE_URL")
    supabase_publishable_key: str = Field(validation_alias="UTRACE_SUPABASE_PUBLISHABLE_KEY")
    supabase_secret_key: str = Field(validation_alias="UTRACE_SUPABASE_SECRET_KEY")
    supabase_jwt_audience: str = Field(
        default="authenticated", validation_alias="SUPABASE_JWT_AUDIENCE"
    )

    lane_signing_key: str = Field(validation_alias="UTRACE_LANE_SIGNING_KEY")
    front_door_service_token: str = Field(
        validation_alias="UTRACE_FRONT_DOOR_SERVICE_TOKEN"
    )
    preview_origin_suffix: str = Field(validation_alias="UTRACE_PREVIEW_ORIGIN_SUFFIX")

    @property
    def supabase_jwt_issuer(self) -> str:
        """The issuer every Supabase Auth access token for this project carries."""
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def supabase_jwks_url(self) -> str:
        """The project's published asymmetric signing keys."""
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, read once.

    Every field is filled from the environment by pydantic-settings, which the
    type checker cannot see, so the constructor call carries a narrow ignore.
    A validation failure is re-raised without its submitted values.
    """
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        raise ConfigurationError(_offending_variables(error)) from None


def _offending_variables(error: ValidationError) -> tuple[str, ...]:
    """Return the environment variable names a validation error complained about."""
    names: list[str] = []
    for item in error.errors():
        location = item["loc"]
        if location:
            names.append(str(location[0]))
    return tuple(dict.fromkeys(names))
