"""Typed decoding of the GPT-Live server event stream on the OpenAI API.

Field names and payload shapes are verified against OpenAI's
[sideband WebSocket reference](https://developers.openai.com/api/reference/resources/live/sideband-websocket),
[Managing GPT-Live sessions](https://developers.openai.com/api/docs/guides/live-conversations),
and [Server-side controls](https://developers.openai.com/api/docs/guides/voice-server-controls?api=live)
(integration references).

Four properties of the provider contract shape this module:

- A transcript event is a timed *fragment*, not a turn. The reference says fragments
  have no item ID and no event marks a completed conversational turn, so the models
  carry `start_ms`/`end_ms` and never imply a completed turn.
- An `appended` acknowledgement arrives when the session timeline reaches the estimated
  end of the added context. It does not prove the model used the context, spoke it, or
  that the person heard it, so `ContextAppendAcknowledged` is named for acceptance
  rather than delivery.
- A sideband receives *reflected* audio: `session.output_audio.delta` with a
  session-timeline range for generated speech, and `session.input_audio.append`, with no
  timing, for received caller audio. Both are base64 mono PCM16LE at 24 kHz. Output
  ranges can have gaps for dropped frames and do not say when the caller heard the
  audio, so delivery stays unknown. The decoded records keep only timing, the
  decoded byte length, and the chunk's loudness, which is what turn-taking needs;
  the audio itself never enters a record, a log, or model context.
- Usage is a running total in fractional seconds (`usage.seconds`, for example `32.5`),
  never an increment to sum.

Unrecognized event types decode to `UnrecognizedEvent`, which preserves the raw payload
without letting an unknown shape masquerade as a known fact.
"""

from __future__ import annotations

import base64
import math
import sys
from array import array
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

__all__ = [
    "ContextAppendAcknowledged",
    "DelegationCreated",
    "GptLiveEvent",
    "InputAudioMuteChanged",
    "InputAudioReflected",
    "OutputAudioSegment",
    "ResponseDelegationEnvelope",
    "SessionClosed",
    "SessionError",
    "SessionInfo",
    "SessionStarted",
    "SessionUpdated",
    "SessionUsageUpdated",
    "TranscriptFragment",
    "UnrecognizedEvent",
    "decode_event",
]


class _Event(BaseModel):
    """Immutable base that ignores provider fields uTrace does not consume."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class SessionStarted(_Event):
    """Startup acknowledgement carrying the provider session resource."""

    type: Literal["session.started"]
    session_id: str = Field(validation_alias="__session_id")
    model: str
    """The GPT-Live model the session runs, for example `gpt-live-1`."""
    expires_at: int | None = None
    delegation_type: str | None = None
    """`client` for uTrace sessions; a `responses` value means the session was misconfigured."""


class SessionUpdated(_Event):
    """A sparse `session.update` was applied; the payload holds the full public resource."""

    type: Literal["session.updated"]
    session_id: str = Field(validation_alias="__session_id")
    delegation_type: str | None = None


class TranscriptFragment(_Event):
    """One timed transcript fragment from either speaker.

    A fragment is provisional transport evidence. Application evidence claims cite spans of
    fragments; a fragment alone establishes no consent, scope, or validation outcome.
    """

    type: Literal["session.input_transcript.delta", "session.output_transcript.delta"]
    delta: str
    start_ms: int
    end_ms: int

    def speaker(self) -> Literal["participant", "agent"]:
        """Whose audio produced this fragment."""
        return "participant" if self.type == "session.input_transcript.delta" else "agent"


class OutputAudioSegment(_Event):
    """Generated-audio evidence: what the service emitted, on the service timeline.

    A sideband receives these as reflected copies while WebRTC carries the live media.
    Generated speech, transmitted audio, and heard speech are different facts; this record
    proves the first one alone.
    """

    type: Literal["session.output_audio.delta"]
    start_ms: int
    end_ms: int
    audio_byte_length: int
    """Decoded PCM16 byte count. The audio itself is discarded at decode time."""
    level: float
    """Root-mean-square loudness of the chunk, from 0 (silence) to 1 (full scale)."""


class InputAudioReflected(_Event):
    """Caller audio the service received, reflected to the sideband before input muting.

    The reference gives it no timing and states it does not confirm the model consumed
    the samples, so it is evidence of receipt only. It is not an acknowledgement of a
    command: the sideband never sends audio.
    """

    type: Literal["session.input_audio.append"]
    audio_byte_length: int
    """Decoded PCM16 byte count. The audio itself is discarded at decode time."""
    level: float
    """Root-mean-square loudness of the chunk, from 0 (silence) to 1 (full scale)."""


class DelegationCreated(_Event):
    """GPT-Live handed a unit of work to the application or to a Responses backend.

    The delegation object carries metadata, not the task text: the reference is explicit
    that the application must work out the request from transcripts and its own state. The
    id is an opaque correlation handle and never an authorization to act.
    """

    type: Literal["session.delegation.created"]
    delegation_id: str
    target: Literal["client", "responses"]
    offset_ms: int | None = None
    response_id: str | None = None
    """Present for `responses` delegation only; binds the Responses lifecycle."""


class ContextAppendAcknowledged(_Event):
    """The service accepted an instructions, thinking, or commentary append for injection."""

    type: Literal[
        "session.instructions.appended",
        "session.thinking.appended",
        "session.commentary.appended",
    ]
    client_event_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None


class InputAudioMuteChanged(_Event):
    """A mute or unmute command was applied to caller input."""

    type: Literal["session.input_audio.muted", "session.input_audio.unmuted"]

    def muted(self) -> bool:
        """True when caller input is now muted."""
        return self.type == "session.input_audio.muted"


class SessionUsageUpdated(_Event):
    """Cumulative session usage snapshot.

    The reference states these are running totals, not increments to sum.
    """

    type: Literal["session.usage.updated"]
    seconds: float
    context_window_usage_ratio: float | None = None


class SessionClosed(_Event):
    """Graceful close completed, with the final cumulative usage and a reason."""

    type: Literal["session.closed"]
    reason: str
    """`close_requested`, `expired`, `content`, `remote_hangup`, or `connection_lost`."""
    seconds: float | None = None


class SessionError(_Event):
    """A startup, validation, command, or delegated Responses error."""

    type: Literal["error"]
    error_type: str
    code: str | None = None
    message: str
    param: str | None = None
    client_event_id: str | None = None
    """Present only when the error correlates to a command this connection sent."""


class SessionInfo(_Event):
    """An informational notice, such as the permissions applied to the browser data channel."""

    type: Literal["info"]
    code: str
    message: str


class ResponseDelegationEnvelope(_Event):
    """A nested Responses delegation event.

    uTrace configures client delegation, so this envelope is not expected. It is decoded
    rather than dropped so a misconfigured session is visible instead of silent.
    """

    type: Literal["response.event"]
    delegation_id: str | None = None
    nested_type: str | None = None


class UnrecognizedEvent(_Event):
    """An event type this connector does not model.

    Storing the raw payload keeps the session record complete without letting an unknown
    shape be mistaken for a decoded fact.
    """

    type: Literal["__unrecognized__"]
    provider_type: str
    payload: dict[str, object]


type GptLiveEvent = Annotated[
    SessionStarted
    | SessionUpdated
    | TranscriptFragment
    | OutputAudioSegment
    | InputAudioReflected
    | DelegationCreated
    | ContextAppendAcknowledged
    | InputAudioMuteChanged
    | SessionUsageUpdated
    | SessionClosed
    | SessionError
    | SessionInfo
    | ResponseDelegationEnvelope
    | UnrecognizedEvent,
    Field(discriminator="type"),
]
"""Every decoded server event, discriminated by its provider `type`."""


def decode_event(payload: dict[str, object]) -> GptLiveEvent:
    """Decode one server event payload into its typed record.

    Returns `UnrecognizedEvent` for an unmodelled or malformed payload rather than raising,
    because a single odd event must not tear down a live Interview Session; the caller
    decides what an unrecognized event means for readiness.
    """
    event_type = payload.get("type")
    if not isinstance(event_type, str):
        return UnrecognizedEvent(
            type="__unrecognized__", provider_type="", payload=dict(payload)
        )
    flattened = _flatten(event_type=event_type, payload=payload)
    if flattened is None:
        return UnrecognizedEvent(
            type="__unrecognized__", provider_type=event_type, payload=dict(payload)
        )
    try:
        return _EventAdapter.model_validate({"event": flattened}).event
    except ValidationError:
        return UnrecognizedEvent(
            type="__unrecognized__", provider_type=event_type, payload=dict(payload)
        )


class _EventAdapter(BaseModel):
    """Single-field wrapper so the discriminated union validates in one place."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    event: GptLiveEvent


def _flatten(*, event_type: str, payload: dict[str, object]) -> dict[str, object] | None:
    """Lift nested provider objects into the flat shape the event models declare.

    Returns `None` when the type is not modelled.
    """
    if event_type in ("session.started", "session.updated"):
        session = payload.get("session")
        session_fields = session if isinstance(session, dict) else {}
        delegation = session_fields.get("delegation")
        return {
            "type": event_type,
            "__session_id": session_fields.get("id"),
            "model": session_fields.get("model"),
            "expires_at": session_fields.get("expires_at"),
            "delegation_type": (
                delegation.get("type") if isinstance(delegation, dict) else None
            ),
        }
    if event_type in (
        "session.input_transcript.delta",
        "session.output_transcript.delta",
    ):
        return {
            "type": event_type,
            "delta": payload.get("delta"),
            "start_ms": payload.get("start_ms"),
            "end_ms": payload.get("end_ms"),
        }
    if event_type == "session.output_audio.delta":
        length, level = _decoded_audio(payload.get("delta"))
        return {
            "type": event_type,
            "start_ms": payload.get("start_ms"),
            "end_ms": payload.get("end_ms"),
            "audio_byte_length": length,
            "level": level,
        }
    if event_type == "session.input_audio.append":
        length, level = _decoded_audio(payload.get("audio"))
        return {
            "type": event_type,
            "audio_byte_length": length,
            "level": level,
        }
    if event_type == "session.delegation.created":
        delegation = payload.get("delegation")
        delegation_fields = delegation if isinstance(delegation, dict) else {}
        return {
            "type": event_type,
            "delegation_id": delegation_fields.get("id"),
            "target": delegation_fields.get("target"),
            "offset_ms": payload.get("offset_ms"),
            "response_id": delegation_fields.get("response_id"),
        }
    if event_type in (
        "session.instructions.appended",
        "session.thinking.appended",
        "session.commentary.appended",
    ):
        return {
            "type": event_type,
            "client_event_id": payload.get("client_event_id"),
            "start_ms": payload.get("start_ms"),
            "end_ms": payload.get("end_ms"),
        }
    if event_type in ("session.input_audio.muted", "session.input_audio.unmuted"):
        return {"type": event_type}
    if event_type == "session.usage.updated":
        usage = payload.get("usage")
        context_window = payload.get("context_window")
        return {
            "type": event_type,
            "seconds": usage.get("seconds") if isinstance(usage, dict) else None,
            "context_window_usage_ratio": (
                context_window.get("usage_ratio")
                if isinstance(context_window, dict)
                else None
            ),
        }
    if event_type == "session.closed":
        usage = payload.get("usage")
        return {
            "type": event_type,
            "reason": payload.get("reason"),
            "seconds": usage.get("seconds") if isinstance(usage, dict) else None,
        }
    if event_type == "error":
        error = payload.get("error")
        error_fields = error if isinstance(error, dict) else {}
        return {
            "type": event_type,
            "error_type": error_fields.get("type"),
            "code": error_fields.get("code"),
            "message": error_fields.get("message"),
            "param": error_fields.get("param"),
            "client_event_id": error_fields.get("client_event_id"),
        }
    if event_type == "info":
        return {
            "type": event_type,
            "code": payload.get("code"),
            "message": payload.get("message"),
        }
    if event_type == "response.event":
        nested = payload.get("event")
        return {
            "type": event_type,
            "delegation_id": payload.get("delegation_id"),
            "nested_type": nested.get("type") if isinstance(nested, dict) else None,
        }
    return None


#: Every fourth sample is enough to measure a chunk's loudness.
_LEVEL_STRIDE = 4


def _decoded_audio(delta: object) -> tuple[int, float]:
    """Return the decoded byte length and loudness of base64 PCM16, or zeros when unusable.

    The audio bytes are never retained: only their length and loudness reach a record.
    """
    if not isinstance(delta, str):
        return 0, 0.0
    try:
        audio = base64.b64decode(delta, validate=True)
    except (ValueError, TypeError):
        return 0, 0.0
    samples = array("h", audio[: len(audio) - len(audio) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    measured = samples[::_LEVEL_STRIDE]
    if not measured:
        return len(audio), 0.0
    mean_square = sum(sample * sample for sample in measured) / len(measured)
    return len(audio), math.sqrt(mean_square) / 32768
