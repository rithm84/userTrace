"""Preview runtime configuration.

The preview area reads its own variables rather than widening the control-plane
``Settings``. Every value is required: a route lifetime or a sandbox lifetime that
silently defaulted would change who can reach a seeded customer dataset and for
how long.

Identity belongs to the pinned runtime template row, not here. These values are
operational policy for the process: how long a route and a handoff live, how
long a runtime may run, where a prepared runtime's Server SDK reports, and whether
the public front door is deployed yet.

``PreviewSettings`` is what the API and the worker need. The template build is a
separate, workstation-only job that stages a fixture checkout, so its inputs are
``TemplateBuildSettings``: a deployed worker has no fixture repository and must
not be asked to name one before it can start.

| Variable | Meaning |
| --- | --- |
| ``UTRACE_PREVIEW_ROUTE_TTL_SECONDS`` | Lifetime of one committed route revision. |
| ``UTRACE_PREVIEW_HANDOFF_CODE_TTL_SECONDS`` | Lifetime of a one-time front-door claim code. |
| ``UTRACE_PREVIEW_SESSION_CREDENTIAL_TTL_SECONDS`` | Lifetime of the door's origin-bound session credential. |
| ``UTRACE_PREVIEW_EXECUTION_TIME_LIMIT_SECONDS`` | Longest a preview sandbox may run. |
| ``UTRACE_PREVIEW_READINESS_TIMEOUT_SECONDS`` | Deadline for one readiness probe request. |
| ``UTRACE_PREVIEW_PUBLIC_PATH_ENABLED`` | ``1`` once the front door serves session origins. |
| ``UTRACE_PREVIEW_SERVER_CONTROL_URL`` | ``wss://`` base URL, may include a path, a claimed runtime's Server SDK connects to. |
| ``UTRACE_PREVIEW_SERVER_INGEST_URL`` | ``https://`` base URL, may include a path, a claimed runtime's Server SDK exports to. |
| ``UTRACE_PREVIEW_SERVER_SERVICE_NAME`` | The signed manifest ``service_key`` the runtime claims. |

Template build only:

| Variable | Meaning |
| --- | --- |
| ``UTRACE_PREVIEW_TEMPLATE_RECIPE`` | Path to the committed template recipe JSON. |
| ``UTRACE_PREVIEW_FIXTURE_REPOSITORY`` | Checkout the template build stages its context from. |
| ``UTRACE_PREVIEW_IMAGE_NAMESPACE`` | Registry namespace the image is pushed under, ``vcr.vercel.com/<team>/<project>``. |
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.settings import ConfigurationError, _offending_variables

__all__ = [
    "PreviewSettings",
    "TemplateBuildSettings",
    "get_preview_settings",
    "get_template_build_settings",
]


class PreviewSettings(BaseSettings):
    """Preview runtime policy read from the process environment."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False, frozen=True)

    route_ttl_seconds: int = Field(validation_alias="UTRACE_PREVIEW_ROUTE_TTL_SECONDS", gt=0)
    handoff_code_ttl_seconds: int = Field(
        validation_alias="UTRACE_PREVIEW_HANDOFF_CODE_TTL_SECONDS", gt=0
    )
    session_credential_ttl_seconds: int = Field(
        validation_alias="UTRACE_PREVIEW_SESSION_CREDENTIAL_TTL_SECONDS", gt=0
    )

    execution_time_limit_seconds: int = Field(
        validation_alias="UTRACE_PREVIEW_EXECUTION_TIME_LIMIT_SECONDS", gt=0
    )
    readiness_timeout_seconds: float = Field(
        validation_alias="UTRACE_PREVIEW_READINESS_TIMEOUT_SECONDS", gt=0
    )
    public_path_enabled: bool = Field(validation_alias="UTRACE_PREVIEW_PUBLIC_PATH_ENABLED")

    server_control_url: str = Field(
        validation_alias="UTRACE_PREVIEW_SERVER_CONTROL_URL", min_length=1
    )
    server_ingest_url: str = Field(
        validation_alias="UTRACE_PREVIEW_SERVER_INGEST_URL", min_length=1
    )
    server_service_name: str = Field(
        validation_alias="UTRACE_PREVIEW_SERVER_SERVICE_NAME", min_length=1
    )


class TemplateBuildSettings(BaseSettings):
    """Inputs the workstation-only template build reads."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False, frozen=True)

    template_recipe_path: Path = Field(validation_alias="UTRACE_PREVIEW_TEMPLATE_RECIPE")
    fixture_repository_path: Path = Field(
        validation_alias="UTRACE_PREVIEW_FIXTURE_REPOSITORY"
    )
    image_namespace: str = Field(
        validation_alias="UTRACE_PREVIEW_IMAGE_NAMESPACE", pattern=r"^vcr\.vercel\.com/[^/]+/[^/]+$"
    )


@lru_cache(maxsize=1)
def get_preview_settings() -> PreviewSettings:
    """Return the process-wide preview settings, read once.

    A validation failure is re-raised as the shared configuration error without
    its submitted values, exactly as the control-plane settings do.
    """
    try:
        return PreviewSettings()  # type: ignore[call-arg]
    except ValidationError as error:
        raise ConfigurationError(_offending_variables(error)) from None


@lru_cache(maxsize=1)
def get_template_build_settings() -> TemplateBuildSettings:
    """Return the template build's settings, read once."""
    try:
        return TemplateBuildSettings()  # type: ignore[call-arg]
    except ValidationError as error:
        raise ConfigurationError(_offending_variables(error)) from None
