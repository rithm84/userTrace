"""Daily rooms for the voice call, through Daily's REST API.

Verified against Daily's REST reference for
[rooms](https://docs.daily.co/reference/rest-api/rooms/create-room) and
[meeting tokens](https://docs.daily.co/reference/rest-api/meeting-tokens/create-meeting-token)
(integration references):

- Each voice connection gets its own private room (`privacy: "private"`), so only a
  holder of one of its meeting tokens can join. The room expires (`exp`) and ejects
  everyone at that moment (`eject_at_room_exp`), so an abandoned call cannot outlive
  its Job.
- The participant and the agent each join with their own meeting token, scoped to
  that room by `room_name` and expiring with it. The participant's token is returned
  to the participant's browser; the agent's never leaves the worker.
- `DELETE /rooms/{name}` ends the room for everyone in it, which is how a superseded
  or unused connection is cleaned up. Deleting a room that no longer exists returns
  404, which counts as done.

The API key is server-side only and never appears in a log, a failure, or a response.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Final, Self

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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

__all__ = ["DAILY_API_BASE_URL", "DailyRoom", "DailyRooms"]

_logger = structlog.get_logger(__name__)

DAILY_API_BASE_URL: Final[str] = "https://api.daily.co/v1"
_PROVIDER: Final[str] = "daily"
_READ_RETRY: Final[RetryPolicy] = RetryPolicy(
    max_attempts=3,
    initial_delay_seconds=0.5,
    backoff_multiplier=2.0,
    safety_justification=(
        "an HTTP error status from Daily means the room or token was not created, and "
        "deleting a room is idempotent"
    ),
)


@dataclass(frozen=True, slots=True)
class DailyRoom:
    """One created room."""

    name: str
    url: str
    expires_at: datetime


class _RoomPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str = Field(min_length=1)
    url: str = Field(min_length=1)


class _TokenPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    token: str = Field(min_length=1)


class DailyRooms:
    """Creates, joins, and deletes the private rooms voice calls run in."""

    def __init__(self, *, api_key: str, base_url: str, request_timeout_seconds: float) -> None:
        self._timeout = request_timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=request_timeout_seconds,
        )

    async def __aenter__(self) -> Self:
        """Enter the scope whose exit closes the HTTP transport."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the HTTP transport."""
        await self._client.aclose()

    async def create_room(self, *, lifetime: timedelta) -> DailyRoom:
        """Create a private room that ejects everyone and closes after `lifetime`."""
        expires_at = datetime.now(tz=UTC) + lifetime
        body = {
            "privacy": "private",
            "properties": {
                "exp": int(expires_at.timestamp()),
                "eject_at_room_exp": True,
                "enable_prejoin_ui": False,
                "enable_chat": False,
                "start_video_off": True,
            },
        }

        async def create_once() -> DailyRoom:
            response = await self._request(
                "POST", "/rooms", operation="create_room", json=body, missing_ok=False
            )
            payload = self._parse(_RoomPayload, response, operation="create_room")
            return DailyRoom(name=payload.name, url=payload.url, expires_at=expires_at)

        room = await retry_safe_call(operation="create_room", policy=_READ_RETRY, call=create_once)
        _logger.info(
            "daily.room.created", room_name=room.name, expires_at=room.expires_at.isoformat()
        )
        return room

    async def meeting_token(
        self, *, room_name: str, expires_at: datetime, user_name: str, user_id: str, owner: bool
    ) -> str:
        """A token that admits one participant to the named room until `expires_at`.

        `user_id` is who the room knows the holder as; `eject` removes by it.
        """
        body = {
            "properties": {
                "room_name": room_name,
                "user_name": user_name,
                "user_id": user_id,
                "is_owner": owner,
                "exp": int(expires_at.timestamp()),
                "eject_at_token_exp": True,
            }
        }

        async def create_once() -> str:
            response = await self._request(
                "POST",
                "/meeting-tokens",
                operation="create_meeting_token",
                json=body,
                missing_ok=False,
            )
            return self._parse(_TokenPayload, response, operation="create_meeting_token").token

        return await retry_safe_call(
            operation="create_meeting_token", policy=_READ_RETRY, call=create_once
        )

    async def eject(self, *, room_name: str, user_id: str) -> None:
        """Remove every participant the room knows as `user_id`.

        Daily answers 404 for a room hosting no call, which has nobody to remove.
        """

        async def eject_once() -> None:
            await self._request(
                "POST",
                f"/rooms/{room_name}/eject",
                operation="eject_participant",
                json={"user_ids": [user_id]},
                missing_ok=True,
            )

        await retry_safe_call(operation="eject_participant", policy=_READ_RETRY, call=eject_once)
        _logger.info("daily.participant.ejected", room_name=room_name)

    async def delete_room(self, *, room_name: str) -> None:
        """End a room for everyone in it; a room that is already gone counts as deleted."""

        async def delete_once() -> None:
            await self._request(
                "DELETE", f"/rooms/{room_name}", operation="delete_room", json=None, missing_ok=True
            )

        await retry_safe_call(operation="delete_room", policy=_READ_RETRY, call=delete_once)
        _logger.info("daily.room.deleted", room_name=room_name)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        json: Mapping[str, object] | None,
        missing_ok: bool,
    ) -> httpx.Response:
        """Send one request, mapping every failure onto a typed `ConnectorFailure`."""
        parameters = redacted_parameters({"method": method, "path": path})
        try:
            response = await self._client.request(method, path, json=json)
        except httpx.TimeoutException as error:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation=operation,
                    category=TechnicalCategory.TIMEOUT,
                    retry_advice=RetryAdvice.AFTER_CONDITION_CHANGE,
                    effect_state=EffectState.INDETERMINATE,
                    message=f"Daily {method} {path} timed out after {self._timeout}s",
                    request_parameters=parameters,
                )
            ) from error
        except httpx.TransportError as error:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation=operation,
                    category=TechnicalCategory.PROVIDER_UNAVAILABLE,
                    retry_advice=RetryAdvice.AFTER_CONDITION_CHANGE,
                    effect_state=EffectState.INDETERMINATE,
                    message=f"Daily {method} {path} failed in transport: {type(error).__name__}",
                    request_parameters=parameters,
                )
            ) from error
        if missing_ok and response.status_code == 404:
            return response
        if response.status_code >= 400:
            category = technical_category_for_status(response.status_code)
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation=operation,
                    category=category,
                    retry_advice=default_retry_advice(category),
                    effect_state=EffectState.ABSENT,
                    message=f"Daily rejected {method} {path}",
                    request_parameters=parameters,
                    status_code=response.status_code,
                    response_body=response.text[:2000]
                    if operation != "create_meeting_token"
                    else None,
                )
            )
        return response

    def _parse[PayloadT: BaseModel](
        self, model: type[PayloadT], response: httpx.Response, *, operation: str
    ) -> PayloadT:
        """Validate a success body; a malformed one reports a completed effect."""
        try:
            return model.model_validate(response.json())
        except (ValidationError, ValueError) as error:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation=operation,
                    category=TechnicalCategory.VALIDATION,
                    retry_advice=RetryAdvice.NEVER,
                    effect_state=EffectState.COMPLETED,
                    message=f"Daily's {operation} response failed validation: {type(error).__name__}",
                )
            ) from error
