"""Backend-owned GPT-Live session setup and sideband control on the OpenAI API.

Verified against OpenAI's
[Getting started with GPT-Live](https://developers.openai.com/api/docs/guides/live),
[WebRTC](https://developers.openai.com/api/docs/guides/voice-webrtc?api=live),
[Server-side controls](https://developers.openai.com/api/docs/guides/voice-server-controls?api=live),
[Delegation and tools](https://developers.openai.com/api/docs/guides/live-delegation),
[Managing GPT-Live sessions](https://developers.openai.com/api/docs/guides/live-conversations),
and the [sideband WebSocket reference](https://developers.openai.com/api/reference/resources/live/sideband-websocket)
(integration references):

- GPT-Live issues no browser credential. The server exchanges the browser's SDP offer with
  `POST /v1/live/sessions`, authenticated with the project API key, sending
  `{"session": {...}, "transport": {"type": "webrtc", "sdp": offer}}`. HTTP 201 returns only
  `session.id` and `transport.sdp`. The request itself starts the session, so nobody sends
  `session.start`, and it bills 15 seconds of initialization.
- The session configuration is chosen here, never by the browser: `model`, `instructions`,
  `audio.output.voice`, `delegation: {"type": "client"}`, and `client.data_channel`, which
  limits what the untrusted browser frontend may send and receive. WebRTC negotiates the
  audio format, so `audio.format` is omitted.
- The sideband is `wss://api.openai.com/v1/live/sessions/{session_id}/attach` with
  `Authorization: Bearer <API key>`. Attaching creates no session and replays no earlier
  events, and a sideband must not send `session.start` or `session.input_audio.append`;
  this connector has no code path that can.
- Context appends carry a plain-string `content` of at most 500 tokens and a required,
  nullable `delegation_id`, where `null` is general session context.
- `session.input_audio.mute` and `session.input_audio.unmute` gate caller input only.
  Graceful close is `session.close` followed by `session.closed`; the hangup endpoint ends
  SIP calls, so cleanup of a WebRTC session runs over the sideband.
- An HTTP error from session creation means the session never started, so a transient
  error status is safe to retry. A transport failure after the request left is not: the
  session may exist.

The connector calls the API through the official `openai` SDK (`client.live.create` and
`client.live.sideband.connect`) with the SDK's own retries disabled, so `retry_safe_call`
is the only retry rule. Session descriptions and transport credentials are excluded from
logs and model context by the
[voice contract](../../../../docs/arch-components/interview-and-voice.md#webrtc-session-setup):
this module never logs an SDP body or the API key, and never attaches the creation
response body to a failure.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Final, Literal, Self

import structlog
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from openai.resources.live.sideband import AsyncSidebandConnection
from openai.types.live.connect_client_event_param import ConnectClientEventParam
from openai.types.live.data_channel_config_param import DataChannelConfigParam
from openai.types.live.live_create_response import LiveCreateResponse
from openai.types.live.media_session_config_param import MediaSessionConfigParam
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from websockets.exceptions import (
    ConnectionClosedError,
    ConnectionClosedOK,
    InvalidStatus,
    WebSocketException,
)

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
from app.connectors.openai_live_events import GptLiveEvent, decode_event
from app.connectors.retry import RetryPolicy, retry_safe_call

__all__ = [
    "OPENAI_API_BASE_URL",
    "GptLiveSessionHandle",
    "GptLiveSessions",
    "SidebandConnection",
]

_logger = structlog.get_logger(__name__)

OPENAI_API_BASE_URL: Final[str] = "https://api.openai.com/v1"
"""The OpenAI API root; the SDK derives the sideband's `wss://` URL from it."""

_PROVIDER: Final[str] = "openai_gpt_live"

_BROWSER_DATA_CHANNEL: Final[DataChannelConfigParam] = {
    "allowed_client_events": [],
    "allowed_server_events": [
        {"type": "session.input_transcript.delta"},
        {"type": "session.output_transcript.delta"},
    ],
}
"""What the session tab's data channel may carry.

The browser sends no command: pause, context, and close belong to the authenticated
sideband, so a participant cannot inject instructions or unmute input from the page. It
receives only the transcript fragments it captions.
"""

_CREATE_RETRY: Final[RetryPolicy] = RetryPolicy(
    max_attempts=3,
    initial_delay_seconds=0.5,
    backoff_multiplier=2.0,
    safety_justification=(
        "an HTTP error from POST /v1/live/sessions means the session never started"
    ),
)
_ATTACH_RETRY: Final[RetryPolicy] = RetryPolicy(
    max_attempts=3,
    initial_delay_seconds=0.5,
    backoff_multiplier=2.0,
    safety_justification="attaching a sideband creates no session and replays no events",
)


class GptLiveSessionHandle(BaseModel):
    """What backend session creation produced for one participant connection.

    `answer_sdp` is relayed to exactly the browser connection that supplied the offer and
    is never logged or persisted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_session_id: str
    """Bound to the current `voice_run_id` by the caller; not a uTrace identity."""
    answer_sdp: str
    model: str
    """The pinned model the session was created with; the response does not echo it."""


class GptLiveSessions:
    """Connector for creating, observing, and closing GPT-Live voice sessions.

    The OpenAI API key stays server-side; uTrace participant authentication authorizes
    setup rather than authenticating the browser to OpenAI.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        request_timeout_seconds: float,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            timeout=request_timeout_seconds,
        )

    async def __aenter__(self) -> Self:
        """Enter the connector's HTTP client scope."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the HTTP transport."""
        await self._client.close()

    @property
    def model(self) -> str:
        """The pinned GPT-Live model every session this connector creates runs."""
        return self._model

    async def create_session(
        self, *, offer_sdp: str, instructions: str, voice: str
    ) -> GptLiveSessionHandle:
        """Create the provider session from a browser SDP offer.

        Only an HTTP error status is retried: the reference states it means the session
        never started. A transport failure reports an indeterminate effect and the caller
        reconciles before offering the participant another connection.
        """
        session: MediaSessionConfigParam = {
            "model": self._model,
            "instructions": instructions,
            "audio": {"output": {"voice": voice}},
            "delegation": {"type": "client"},
            "client": {"data_channel": _BROWSER_DATA_CHANNEL},
        }

        async def create_once() -> LiveCreateResponse:
            return await self._create_once(session=session, offer_sdp=offer_sdp)

        created = await retry_safe_call(
            operation="create_session", policy=_CREATE_RETRY, call=create_once
        )
        handle = _handle_from(created=created, model=self._model)
        _logger.info(
            "gpt_live.session.created",
            provider_session_id=handle.provider_session_id,
            model=self._model,
            voice=voice,
            delegation="client",
        )
        return handle

    @asynccontextmanager
    async def attach_sideband(
        self, *, provider_session_id: str
    ) -> AsyncIterator[SidebandConnection]:
        """Attach the authenticated sideband WebSocket to a running session.

        The session is already running, so no `session.start` is sent. The connection is
        closed when the context exits, which does not by itself end the provider session.
        """

        async def attach_once() -> AsyncSidebandConnection:
            return await self._attach_once(provider_session_id=provider_session_id)

        connection = await retry_safe_call(
            operation="attach_sideband", policy=_ATTACH_RETRY, call=attach_once
        )
        _logger.info("gpt_live.sideband.attached", provider_session_id=provider_session_id)
        try:
            yield SidebandConnection(
                connection=connection, provider_session_id=provider_session_id
            )
        finally:
            await connection.close()
            _logger.info(
                "gpt_live.sideband.detached", provider_session_id=provider_session_id
            )

    async def close_session(self, *, provider_session_id: str) -> None:
        """End a provider session gracefully and wait for its close acknowledgement.

        Used both for normal teardown and for cleaning up a session whose setup response
        arrived after its participant connection was superseded or revoked.
        """
        async with self.attach_sideband(provider_session_id=provider_session_id) as sideband:
            await sideband.request_close()
            async for event in sideband.events():
                if event.type == "session.closed":
                    _logger.info(
                        "gpt_live.session.closed",
                        provider_session_id=provider_session_id,
                        reason=event.reason,
                        seconds=event.seconds,
                    )
                    return
        _logger.warning(
            "gpt_live.session.close_unconfirmed", provider_session_id=provider_session_id
        )

    async def _create_once(
        self, *, session: MediaSessionConfigParam, offer_sdp: str
    ) -> LiveCreateResponse:
        """Make one creation request and map every SDK error onto a typed failure."""
        try:
            return await self._client.live.create(
                session=session, transport={"type": "webrtc", "sdp": offer_sdp}
            )
        except APIStatusError as error:
            category = technical_category_for_status(error.status_code)
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="create_session",
                    category=category,
                    retry_advice=default_retry_advice(category),
                    effect_state=EffectState.ABSENT,
                    message=(
                        "GPT-Live rejected the session request "
                        f"(x-request-id {error.request_id})"
                    ),
                    request_parameters=self._parameters(),
                    status_code=error.status_code,
                    provider_error_code=error.code,
                    response_body=error.response.text,
                )
            ) from error
        except APITimeoutError as error:
            raise self._transport_failure(
                category=TechnicalCategory.TIMEOUT,
                message="GPT-Live session creation timed out",
                error=error,
            ) from error
        except APIConnectionError as error:
            raise self._transport_failure(
                category=TechnicalCategory.PROVIDER_UNAVAILABLE,
                message="GPT-Live session creation failed before a provider verdict",
                error=error,
            ) from error

    async def _attach_once(self, *, provider_session_id: str) -> AsyncSidebandConnection:
        """Open one sideband WebSocket and map every handshake error onto a typed failure."""
        parameters = redacted_parameters(
            {"provider_session_id": provider_session_id, "base_url": self._base_url}
        )
        try:
            return await self._client.live.sideband.connect(
                session_id=provider_session_id
            ).enter()
        except InvalidStatus as error:
            status = error.response.status_code
            category = technical_category_for_status(status)
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="attach_sideband",
                    category=category,
                    retry_advice=default_retry_advice(category),
                    effect_state=EffectState.ABSENT,
                    message="sideband attach was rejected by the provider",
                    request_parameters=parameters,
                    status_code=status,
                    response_body=error.response.body.decode("utf-8", errors="replace"),
                )
            ) from error
        except (WebSocketException, OSError, TimeoutError) as error:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="attach_sideband",
                    category=TechnicalCategory.PROVIDER_UNAVAILABLE,
                    retry_advice=RetryAdvice.AFTER_DELAY,
                    effect_state=EffectState.ABSENT,
                    message=f"sideband attach failed: {type(error).__name__}: {error}",
                    request_parameters=parameters,
                )
            ) from error

    def _transport_failure(
        self, *, category: TechnicalCategory, message: str, error: Exception
    ) -> ConnectorFailure:
        """Build the failure for a creation request that produced no provider verdict.

        The session may have been created before the transport failed, so the effect state
        is indeterminate and the caller reconciles rather than repeating.
        """
        return ConnectorFailure(
            FailureDetail(
                provider=_PROVIDER,
                operation="create_session",
                category=category,
                retry_advice=RetryAdvice.AFTER_CONDITION_CHANGE,
                effect_state=EffectState.INDETERMINATE,
                message=f"{message}: {type(error).__name__}",
                request_parameters=self._parameters(),
            )
        )

    def _parameters(self) -> dict[str, str]:
        """The redacted request parameters a creation failure carries."""
        return redacted_parameters({"base_url": self._base_url, "model": self._model})


class SidebandConnection:
    """One attached sideband WebSocket for a running GPT-Live session.

    The connection reads typed server events and sends the session-control and
    context-append commands the backend owns. It never sends `session.start`, never sends
    audio, and never relays a browser-supplied event: browser-supplied voice events cannot
    authorize backend tools or replace authenticated sideband results.
    """

    def __init__(
        self, *, connection: AsyncSidebandConnection, provider_session_id: str
    ) -> None:
        self._connection = connection
        self._provider_session_id = provider_session_id

    @property
    def provider_session_id(self) -> str:
        """The provider session this sideband observes."""
        return self._provider_session_id

    async def events(self) -> AsyncIterator[GptLiveEvent]:
        """Yield decoded server events until the provider closes the connection."""
        while True:
            try:
                frame = await self._connection.recv_bytes()
            except ConnectionClosedOK:
                return
            except (ConnectionClosedError, WebSocketException) as error:
                raise ConnectorFailure(
                    FailureDetail(
                        provider=_PROVIDER,
                        operation="sideband_events",
                        category=TechnicalCategory.PROVIDER_UNAVAILABLE,
                        retry_advice=RetryAdvice.AFTER_DELAY,
                        effect_state=EffectState.INDETERMINATE,
                        message=f"sideband stream ended abnormally: {type(error).__name__}",
                        request_parameters=redacted_parameters(
                            {"provider_session_id": self._provider_session_id}
                        ),
                    )
                ) from error
            try:
                payload = json.loads(frame)
            except json.JSONDecodeError:
                _logger.warning(
                    "gpt_live.sideband.undecodable_frame",
                    provider_session_id=self._provider_session_id,
                    frame_length=len(frame),
                )
                continue
            if isinstance(payload, dict):
                yield decode_event(payload)

    async def append_thinking(self, *, content: str, delegation_id: str | None) -> None:
        """Send quiet context the model may use without speaking it on append.

        `delegation_id` is the client delegation this result answers, or `None` for general
        session context. The acknowledgement proves acceptance for injection only.
        """
        await self._send(
            {
                "type": "session.thinking.append",
                "delegation_id": delegation_id,
                "content": content,
            }
        )

    async def append_commentary(self, *, content: str, delegation_id: str | None) -> None:
        """Send content the model should say aloud, which it may paraphrase."""
        await self._send(
            {
                "type": "session.commentary.append",
                "delegation_id": delegation_id,
                "content": content,
            }
        )

    async def append_instructions(self, *, content: str, delegation_id: str | None) -> None:
        """Send trusted application instructions that change behaviour or speech."""
        await self._send(
            {
                "type": "session.instructions.append",
                "delegation_id": delegation_id,
                "content": content,
            }
        )

    async def set_input_muted(self, *, muted: bool) -> None:
        """Mute or unmute caller input for the participant's Pause control."""
        if muted:
            await self._send({"type": "session.input_audio.mute"})
        else:
            await self._send({"type": "session.input_audio.unmute"})

    async def request_close(self) -> None:
        """Begin graceful shutdown; read events until `session.closed` arrives."""
        await self._send({"type": "session.close"})

    async def _send(self, command: ConnectClientEventParam) -> None:
        """Send one command, mapping transport failures to a typed failure."""
        try:
            await self._connection.send(command)
        except WebSocketException as error:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="sideband_send",
                    category=TechnicalCategory.PROVIDER_UNAVAILABLE,
                    retry_advice=RetryAdvice.AFTER_CONDITION_CHANGE,
                    effect_state=EffectState.INDETERMINATE,
                    message=f"sideband command send failed: {type(error).__name__}",
                    request_parameters=redacted_parameters(
                        {
                            "provider_session_id": self._provider_session_id,
                            "command_type": command["type"],
                        }
                    ),
                )
            ) from error


class _CreatedSession(BaseModel):
    """The `session` object in the creation response."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str = Field(min_length=1)


class _AnswerTransport(BaseModel):
    """The `transport` object carrying the SDP answer."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    type: Literal["webrtc"]
    sdp: str = Field(min_length=1)


class _CreationPayload(BaseModel):
    """The documented HTTP 201 body of `POST /v1/live/sessions`."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    session: _CreatedSession
    transport: _AnswerTransport


def _handle_from(*, created: LiveCreateResponse, model: str) -> GptLiveSessionHandle:
    """Validate the creation response the SDK returned without validating.

    The body is not attached to a failure: it carries the SDP answer. The session exists
    at this point, so a malformed answer reports a completed effect.
    """
    try:
        payload = _CreationPayload.model_validate(created.to_dict())
    except ValidationError as error:
        raise ConnectorFailure(
            FailureDetail(
                provider=_PROVIDER,
                operation="create_session",
                category=TechnicalCategory.VALIDATION,
                retry_advice=RetryAdvice.NEVER,
                effect_state=EffectState.COMPLETED,
                message=(
                    f"session creation response failed validation: {error.error_count()} errors"
                ),
            )
        ) from error
    return GptLiveSessionHandle(
        provider_session_id=payload.session.id,
        answer_sdp=payload.transport.sdp,
        model=model,
    )
