"""Starting voice agent sessions on Pipecat Cloud.

The voice agent runs on Pipecat Cloud (`app.interview.voice.cloud_session`). The API
starts one session for each `interview.voice_agent` Job with the Pipecat Cloud
public API key, passing only the Job's id: the session reads the rest from the Job,
and joins the connection's own Daily room, so Pipecat Cloud creates no room. See
[starting sessions](https://docs.pipecat.ai/api-reference/pipecat-cloud/rest-reference/endpoint/start).
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

import httpx
import structlog

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
from app.connectors.retry import RetryPolicy, retry_safe_call

__all__ = ["PIPECAT_CLOUD_PUBLIC_API_URL", "PipecatCloudAgents"]

_logger = structlog.get_logger(__name__)

PIPECAT_CLOUD_PUBLIC_API_URL: Final[str] = "https://api.pipecat.daily.co/v1/public"
_PROVIDER: Final[str] = "pipecat_cloud"
_START_RETRY: Final[RetryPolicy] = RetryPolicy(
    max_attempts=3,
    initial_delay_seconds=0.25,
    backoff_multiplier=2.0,
    safety_justification=(
        "a session started twice for one Job claims it once: the second session finds "
        "the Job leased and ends"
    ),
)


class PipecatCloudAgents:
    """Starts the deployed voice agent for one `interview.voice_agent` Job at a time."""

    def __init__(
        self, *, api_key: str, agent_name: str, base_url: str, request_timeout_seconds: float
    ) -> None:
        self._agent_name = agent_name
        self._timeout = request_timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=request_timeout_seconds,
        )

    async def aclose(self) -> None:
        """Close the HTTP transport."""
        await self._client.aclose()

    async def start(self, *, voice_agent_job_id: UUID) -> None:
        """Start one agent session that claims and runs the named Job."""

        async def start_once() -> None:
            await self._start(voice_agent_job_id)

        await retry_safe_call(operation="start_session", policy=_START_RETRY, call=start_once)

    async def _start(self, voice_agent_job_id: UUID) -> None:
        path = f"/{self._agent_name}/start"
        parameters = redacted_parameters(
            {"agent": self._agent_name, "voice_agent_job_id": str(voice_agent_job_id)}
        )
        try:
            response = await self._client.post(
                path,
                json={
                    "createDailyRoom": False,
                    "body": {"voice_agent_job_id": str(voice_agent_job_id)},
                },
            )
        except httpx.TimeoutException as error:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="start_session",
                    category=TechnicalCategory.TIMEOUT,
                    retry_advice=RetryAdvice.AFTER_CONDITION_CHANGE,
                    effect_state=EffectState.INDETERMINATE,
                    message=f"Pipecat Cloud POST {path} timed out after {self._timeout}s",
                    request_parameters=parameters,
                )
            ) from error
        except httpx.TransportError as error:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="start_session",
                    category=TechnicalCategory.PROVIDER_UNAVAILABLE,
                    retry_advice=RetryAdvice.AFTER_CONDITION_CHANGE,
                    effect_state=EffectState.INDETERMINATE,
                    message=f"Pipecat Cloud POST {path} failed in transport: {type(error).__name__}",
                    request_parameters=parameters,
                )
            ) from error
        if response.status_code >= 400:
            category = technical_category_for_status(response.status_code)
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="start_session",
                    category=category,
                    retry_advice=default_retry_advice(category),
                    effect_state=EffectState.ABSENT,
                    message=f"Pipecat Cloud rejected POST {path}",
                    request_parameters=parameters,
                    status_code=response.status_code,
                    response_body=response.text[:2000],
                )
            )
        _logger.info(
            "pipecat_cloud.session_started",
            agent=self._agent_name,
            voice_agent_job_id=str(voice_agent_job_id),
            session_id=response.json().get("sessionId"),
        )
