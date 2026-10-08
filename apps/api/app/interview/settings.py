"""Configuration the interview runtime reads from the process environment.

The control plane's own `app.settings.Settings` stays the kernel's contract. This
module adds only what the interview runtime, context assembly, and extension
control need, and it is read where it is used rather than at import, so a tool
that only needs the route shapes still starts without provider credentials.

Every value is required. `InterviewConfigurationError` names the missing
variables and nothing else: a validation error carries submitted values, and
those values are credentials.
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "AgentCharacter",
    "InterviewConfigurationError",
    "InterviewSettings",
    "JevSettings",
    "UnknownAgentCharacterError",
    "VoiceSettings",
    "get_interview_settings",
    "get_jev_settings",
    "get_voice_settings",
]

_AGENT_CHARACTER_VARIABLE = "UTRACE_AGENT_CHARACTER"


class AgentCharacter(StrEnum):
    """The Rive rigs `packages/utrace-agent-character/assets` actually ships.

    One `utrace-agent-<name>.riv` file exists per member, the web application
    packages byte-identical copies of exactly these seven, and the session
    projection's `agent_character` is the name the session tab and the extension
    load. A name outside this set would leave both surfaces with no character to
    render, so configuration cannot name one.
    """

    CORAL = "coral"
    BLACK = "black"
    MINT = "mint"
    SKY = "sky"
    LILAC = "lilac"
    PEACH = "peach"
    AQUA = "aqua"


class InterviewConfigurationError(RuntimeError):
    """Interview-runtime configuration is missing or invalid."""

    def __init__(self, variables: tuple[str, ...]) -> None:
        super().__init__(
            "interview-runtime configuration is incomplete; "
            f"check these variables: {', '.join(variables)}"
        )
        self.variables = variables


class UnknownAgentCharacterError(RuntimeError):
    """`UTRACE_AGENT_CHARACTER` names a character no shipped Rive rig provides.

    The rejected value stays out of the message, as it does for every other
    variable in this module; the allowed set is what the operator needs.
    """

    def __init__(self) -> None:
        shipped = ", ".join(character.value for character in AgentCharacter)
        super().__init__(
            f"{_AGENT_CHARACTER_VARIABLE} must name one of the shipped agent "
            f"characters: {shipped}"
        )


class _EnvSettings(BaseSettings):
    """Base for every environment-read settings group in this package."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)


class InterviewSettings(_EnvSettings):
    """Tuning the interview runtime applies to its own durable records."""

    extension_protocol_version: str = Field(
        default="1.0", validation_alias="UTRACE_EXTENSION_PROTOCOL_VERSION"
    )
    extension_previous_protocol_version: str = Field(
        default="1.0", validation_alias="UTRACE_EXTENSION_PREVIOUS_PROTOCOL_VERSION"
    )
    extension_api_audience: str = Field(
        default="https://api.utrace.dev", validation_alias="UTRACE_EXTENSION_API_AUDIENCE"
    )
    extension_socket_audience: str = Field(
        default="wss://api.utrace.dev",
        validation_alias="UTRACE_EXTENSION_SOCKET_AUDIENCE",
    )
    extension_offer_lifetime_seconds: int = Field(
        default=300, validation_alias="UTRACE_EXTENSION_OFFER_LIFETIME_SECONDS"
    )
    extension_link_code_lifetime_seconds: int = Field(
        default=600, validation_alias="UTRACE_EXTENSION_LINK_CODE_LIFETIME_SECONDS"
    )
    possession_proof_lifetime_seconds: int = Field(
        default=120, validation_alias="UTRACE_EXTENSION_POP_LIFETIME_SECONDS"
    )
    visual_command_deadline_seconds: int = Field(
        default=20, validation_alias="UTRACE_VISUAL_COMMAND_DEADLINE_SECONDS"
    )
    context_budget_items: int = Field(
        default=96, validation_alias="UTRACE_CONTEXT_BUDGET_ITEMS"
    )
    recent_conversation_fragments: int = Field(
        default=600, validation_alias="UTRACE_CONTEXT_RECENT_FRAGMENTS"
    )
    selected_observation_limit: int = Field(
        default=20, validation_alias="UTRACE_CONTEXT_OBSERVATION_LIMIT"
    )
    tool_deadline_seconds: int = Field(
        default=45, validation_alias="UTRACE_VOICE_TOOL_DEADLINE_SECONDS"
    )
    agent_display_name: str = Field(
        default="Wren", validation_alias="UTRACE_AGENT_DISPLAY_NAME"
    )
    agent_character: AgentCharacter = Field(
        default=AgentCharacter.CORAL, validation_alias=_AGENT_CHARACTER_VARIABLE
    )


class VoiceSettings(_EnvSettings):
    """The voice call's providers and their pinned configuration.

    Daily carries the call's audio, Deepgram Flux hears the participant, Claude
    writes the agent's words, and ElevenLabs speaks them. On a deployed compute each
    key is a Supabase Compute project secret.
    """

    daily_api_key: str = Field(validation_alias="DAILY_API_KEY", min_length=1)
    deepgram_api_key: str = Field(validation_alias="DEEPGRAM_API_KEY", min_length=1)
    anthropic_api_key: str = Field(validation_alias="ANTHROPIC_API_KEY", min_length=1)
    elevenlabs_api_key: str = Field(validation_alias="ELEVENLABS_API_KEY", min_length=1)
    text_model: str = Field(default="claude-sonnet-5-5", validation_alias="UTRACE_VOICE_TEXT_MODEL")
    speech_model: str = Field(
        default="eleven_v4_turbo", validation_alias="UTRACE_VOICE_SPEECH_MODEL"
    )
    voice_id: str = Field(
        # ElevenLabs' shared "Siren - Natural realistic conversational voice".
        default="eXpIbVcVbLo8ZJQDlDnl",
        validation_alias="UTRACE_VOICE_ID",
    )
    end_of_turn_threshold: float = Field(default=0.6, validation_alias="UTRACE_VOICE_EOT_THRESHOLD")
    eager_end_of_turn_threshold: float = Field(
        default=0.4, validation_alias="UTRACE_VOICE_EAGER_EOT_THRESHOLD"
    )
    room_lifetime_seconds: int = Field(
        default=7200, validation_alias="UTRACE_VOICE_ROOM_LIFETIME_SECONDS"
    )
    request_timeout_seconds: float = Field(
        default=15.0, validation_alias="UTRACE_VOICE_REQUEST_TIMEOUT_SECONDS"
    )


class JevSettings(_EnvSettings):
    """The Vercel AI Gateway credential and the pinned Jev gateway model id."""

    api_key: str = Field(validation_alias="VERCEL_AI_GATEWAY_API_KEY")
    model_id: str = Field(default="typesafe-ai/jev", validation_alias="UTRACE_JEV_MODEL_ID")
    timeout_seconds: float = Field(validation_alias="UTRACE_JEV_TIMEOUT_SECONDS", gt=0)


def _offending_variables(error: ValidationError) -> tuple[str, ...]:
    """Return the environment variable names a validation error complained about."""
    names: list[str] = []
    for item in error.errors():
        location = item["loc"]
        if location:
            names.append(str(location[0]))
    return tuple(dict.fromkeys(names))


@lru_cache(maxsize=1)
def get_interview_settings() -> InterviewSettings:
    """Return the process-wide interview tuning, read once."""
    try:
        return InterviewSettings()
    except ValidationError as error:
        variables = _offending_variables(error)
        if _AGENT_CHARACTER_VARIABLE in variables:
            raise UnknownAgentCharacterError() from None
        raise InterviewConfigurationError(variables) from None


@lru_cache(maxsize=1)
def get_voice_settings() -> VoiceSettings:
    """Return the voice providers' credentials and pinned configuration, read once."""
    try:
        return VoiceSettings()  # type: ignore[call-arg]
    except ValidationError as error:
        raise InterviewConfigurationError(_offending_variables(error)) from None


@lru_cache(maxsize=1)
def get_jev_settings() -> JevSettings:
    """Return the pinned Jev configuration, read once."""
    try:
        return JevSettings()  # type: ignore[call-arg]
    except ValidationError as error:
        raise InterviewConfigurationError(_offending_variables(error)) from None
