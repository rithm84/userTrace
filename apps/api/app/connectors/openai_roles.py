"""The pinned OpenAI configuration of every reasoning role.

Every role runs GPT-6.1 Sol, chosen by measurement on the demo's change: code
investigation and implementation inside Codex in the workspace sandbox, and the
Implementation Gate proposal, intake assistance, and Case File synthesis through the
Responses API. The Implementation Gate proposal and implementation, which the
participant waits on between agreeing and validating, run on the Ultrafast tier;
the others run on the Fast tier. The roles a waiting
participant feels in the conversation run at low effort; the two that interpret or
write for a reader run at medium.

The API key is read from `OPENAI_API_KEY`, a Supabase Compute project secret on a
deployed compute. It never enters a workspace: the sandbox firewall adds it at egress.
"""

from collections.abc import Mapping
from enum import StrEnum
from functools import lru_cache
from typing import Final

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.connectors.openai_models import (
    GPT_6_1_SOL,
    OpenAIEffort,
    OpenAIModelConfiguration,
    OpenAIResponsesGateway,
    OpenAIServiceTier,
)

__all__ = [
    "OpenAIConfigurationError",
    "OpenAIRole",
    "openai_api_key",
    "openai_configuration_for",
    "openai_gateway_for",
]

_CREDENTIAL_REFERENCE: Final[str] = "supabase-compute/secrets/OPENAI_API_KEY"
_REQUEST_TIMEOUT_SECONDS: Final[float] = 120.0


class OpenAIRole(StrEnum):
    """The reasoning roles."""

    INTAKE_ASSISTANCE = "intake_assistance"
    """Interprets a batch intake routing could not resolve from supplied facts."""
    CASE_FILE_SYNTHESIS = "case_file_synthesis"
    """Drafts the Case File's typed fields from the frozen bundle."""
    CODE_INVESTIGATION = "code_investigation"
    """Investigates the checkout read-only before the gate, in Codex."""
    GATE_PROPOSAL = "gate_proposal"
    """Proposes the Implementation Gate decision the controller then owns."""
    IMPLEMENTATION = "implementation"
    """Implements the approved change in Codex."""


_ROLE_CONFIGURATION: Final[Mapping[OpenAIRole, tuple[str, OpenAIEffort, OpenAIServiceTier]]] = {
    OpenAIRole.INTAKE_ASSISTANCE: (GPT_6_1_SOL, OpenAIEffort.MEDIUM, OpenAIServiceTier.FAST),
    OpenAIRole.CASE_FILE_SYNTHESIS: (GPT_6_1_SOL, OpenAIEffort.MEDIUM, OpenAIServiceTier.FAST),
    OpenAIRole.CODE_INVESTIGATION: (GPT_6_1_SOL, OpenAIEffort.LOW, OpenAIServiceTier.FAST),
    OpenAIRole.GATE_PROPOSAL: (GPT_6_1_SOL, OpenAIEffort.LOW, OpenAIServiceTier.ULTRAFAST),
    OpenAIRole.IMPLEMENTATION: (GPT_6_1_SOL, OpenAIEffort.LOW, OpenAIServiceTier.ULTRAFAST),
}
"""GPT-6.1 Sol at low effort except the two roles that interpret or write for a reader.
On the demo's change, Sol on the Fast tier matched GPT-6 Astra on the Fast tier and ran
about 30 seconds behind Astra on the Ultrafast tier, at a sixth and a seventeenth of
their cost."""


class OpenAIConfigurationError(RuntimeError):
    """The OpenAI API key is not configured."""

    def __init__(self) -> None:
        super().__init__(
            "OPENAI_API_KEY is not set; every reasoning role needs it to call OpenAI"
        )


class _OpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=True)

    api_key: str = Field(validation_alias="OPENAI_API_KEY", min_length=1)


@lru_cache(maxsize=1)
def _settings() -> _OpenAISettings:
    try:
        return _OpenAISettings()  # type: ignore[call-arg]
    except ValidationError:
        raise OpenAIConfigurationError() from None


def openai_configuration_for(role: OpenAIRole) -> OpenAIModelConfiguration:
    """Return the pinned configuration one role runs under."""
    model_id, effort, service_tier = _ROLE_CONFIGURATION[role]
    return OpenAIModelConfiguration(
        model_id=model_id,
        effort=effort,
        service_tier=service_tier,
        credential_reference=_CREDENTIAL_REFERENCE,
    )


def openai_api_key() -> str:
    """The key the workspace firewall adds at egress; it never enters a sandbox."""
    return _settings().api_key


def openai_gateway_for(role: OpenAIRole) -> OpenAIResponsesGateway:
    """Open a Responses API gateway for one role under its pinned configuration."""
    return OpenAIResponsesGateway(
        configuration=openai_configuration_for(role),
        api_key=_settings().api_key,
        request_timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
    )
