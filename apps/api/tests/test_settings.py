"""Configuration hygiene.

A misconfigured process must fail closed and say which variable is wrong without
printing what was submitted, because those values are credentials.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from app.interview.settings import (
    AgentCharacter,
    UnknownAgentCharacterError,
    get_interview_settings,
)
from app.settings import ConfigurationError, Settings, get_settings

_REQUIRED = (
    "UTRACE_DATABASE_URL",
    "SUPABASE_URL",
    "UTRACE_SUPABASE_PUBLISHABLE_KEY",
    "UTRACE_SUPABASE_SECRET_KEY",
    "UTRACE_LANE_SIGNING_KEY",
    "UTRACE_FRONT_DOOR_SERVICE_TOKEN",
    "UTRACE_PREVIEW_ORIGIN_SUFFIX",
)


@pytest.fixture
def cleared_environment() -> Iterator[None]:
    """Remove every required variable for the duration of one test."""
    saved = {name: os.environ.pop(name) for name in _REQUIRED if name in os.environ}
    get_settings.cache_clear()
    try:
        yield
    finally:
        os.environ.update(saved)
        get_settings.cache_clear()


def test_missing_configuration_names_variables_without_echoing_values(
    cleared_environment: None,
) -> None:
    """The refusal lists variable names only."""
    os.environ["UTRACE_DATABASE_URL"] = "postgresql://user:hunter2@example.invalid/db"
    try:
        with pytest.raises(ConfigurationError) as refusal:
            get_settings()
    finally:
        os.environ.pop("UTRACE_DATABASE_URL", None)
    message = str(refusal.value)
    assert "SUPABASE_URL" in message
    assert "UTRACE_LANE_SIGNING_KEY" in message
    assert "hunter2" not in message
    assert "postgresql://" not in message
    assert "UTRACE_DATABASE_URL" not in refusal.value.variables


def test_the_agent_character_must_name_a_shipped_rive_rig() -> None:
    """A character with no packaged `.riv` file fails start-up with the allowed set."""
    saved = os.environ.get("UTRACE_AGENT_CHARACTER")
    get_interview_settings.cache_clear()
    try:
        os.environ["UTRACE_AGENT_CHARACTER"] = "chartreuse"
        with pytest.raises(UnknownAgentCharacterError) as refusal:
            get_interview_settings()
        message = str(refusal.value)
        assert "chartreuse" not in message
        for character in AgentCharacter:
            assert character.value in message

        os.environ["UTRACE_AGENT_CHARACTER"] = "lilac"
        get_interview_settings.cache_clear()
        assert get_interview_settings().agent_character is AgentCharacter.LILAC
    finally:
        if saved is None:
            os.environ.pop("UTRACE_AGENT_CHARACTER", None)
        else:
            os.environ["UTRACE_AGENT_CHARACTER"] = saved
        get_interview_settings.cache_clear()


def test_derived_supabase_endpoints_follow_the_project_url() -> None:
    """The issuer and JWKS endpoint come from the project URL, never a guess."""
    settings = Settings.model_construct(supabase_url="https://project.supabase.co/")
    assert settings.supabase_jwt_issuer == "https://project.supabase.co/auth/v1"
    assert (
        settings.supabase_jwks_url
        == "https://project.supabase.co/auth/v1/.well-known/jwks.json"
    )
