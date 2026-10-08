"""Pure tests for GPT-Live event decoding.

The payloads are the ones published in OpenAI's sideband WebSocket reference. They pin two
properties the voice contract depends on: an unmodelled or malformed event never
masquerades as a decoded fact, and audio bytes never survive decoding.
"""

from __future__ import annotations

import base64

from app.connectors.openai_live_events import (
    ContextAppendAcknowledged,
    DelegationCreated,
    InputAudioReflected,
    OutputAudioSegment,
    SessionClosed,
    SessionError,
    SessionInfo,
    SessionStarted,
    SessionUsageUpdated,
    TranscriptFragment,
    UnrecognizedEvent,
    decode_event,
)


def test_session_started_lifts_the_nested_session_resource() -> None:
    event = decode_event(
        {
            "type": "session.started",
            "session": {
                "id": "sess_123",
                "expires_at": 1782518400,
                "model": "gpt-live-1",
                "instructions": "Be concise.",
                "audio": {"output": {"voice": "marin"}},
                "delegation": {"type": "client"},
            },
        }
    )
    assert isinstance(event, SessionStarted)
    assert event.session_id == "sess_123"
    assert event.model == "gpt-live-1"
    assert event.delegation_type == "client"
    assert event.expires_at == 1782518400


def test_transcript_fragments_identify_their_speaker() -> None:
    participant = decode_event(
        {
            "type": "session.input_transcript.delta",
            "delta": "What is the",
            "start_ms": 600,
            "end_ms": 800,
        }
    )
    assert isinstance(participant, TranscriptFragment)
    assert participant.speaker() == "participant"
    assert participant.delta == "What is the"

    agent = decode_event(
        {
            "type": "session.output_transcript.delta",
            "delta": "Let me check",
            "start_ms": 900,
            "end_ms": 1100,
        }
    )
    assert isinstance(agent, TranscriptFragment)
    assert agent.speaker() == "agent"


def test_output_audio_keeps_only_timing_and_length() -> None:
    audio = base64.b64encode(b"\x00\x01" * 24).decode("ascii")
    event = decode_event(
        {
            "type": "session.output_audio.delta",
            "delta": audio,
            "start_ms": 0,
            "end_ms": 100,
        }
    )
    assert isinstance(event, OutputAudioSegment)
    assert event.audio_byte_length == 48
    assert "delta" not in event.model_dump()
    assert audio not in str(event.model_dump())


def test_reflected_input_audio_keeps_only_its_length() -> None:
    audio = base64.b64encode(b"\x00\x01" * 12).decode("ascii")
    event = decode_event({"type": "session.input_audio.append", "audio": audio})
    assert isinstance(event, InputAudioReflected)
    assert event.audio_byte_length == 24
    assert audio not in str(event.model_dump())


def test_unusable_audio_decodes_to_zero_length_rather_than_raising() -> None:
    event = decode_event(
        {
            "type": "session.output_audio.delta",
            "delta": "not-base64!!",
            "start_ms": 0,
            "end_ms": 10,
        }
    )
    assert isinstance(event, OutputAudioSegment)
    assert event.audio_byte_length == 0


def test_client_delegation_carries_only_an_opaque_id() -> None:
    event = decode_event(
        {
            "type": "session.delegation.created",
            "event_id": "event_delegation",
            "offset_ms": 1000,
            "delegation": {
                "id": "item_delegation_123",
                "type": "delegation",
                "target": "client",
            },
        }
    )
    assert isinstance(event, DelegationCreated)
    assert event.delegation_id == "item_delegation_123"
    assert event.target == "client"
    assert event.response_id is None


def test_responses_delegation_binds_a_response_id() -> None:
    event = decode_event(
        {
            "type": "session.delegation.created",
            "offset_ms": 1000,
            "delegation": {
                "id": "item_delegation_456",
                "type": "delegation",
                "target": "responses",
                "response_id": "resp_123",
            },
        }
    )
    assert isinstance(event, DelegationCreated)
    assert event.target == "responses"
    assert event.response_id == "resp_123"


def test_context_append_acknowledgement_is_acceptance_not_delivery() -> None:
    event = decode_event(
        {
            "type": "session.commentary.appended",
            "client_event_id": "event_result_1",
            "start_ms": 1200,
            "end_ms": 1600,
        }
    )
    assert isinstance(event, ContextAppendAcknowledged)
    assert event.client_event_id == "event_result_1"


def test_session_closed_carries_reason_and_fractional_final_usage() -> None:
    event = decode_event(
        {
            "type": "session.closed",
            "event_id": "evt_closed_001",
            "client_event_id": "evt_close_001",
            "reason": "close_requested",
            "session": {"id": "live_abc123", "model": "gpt-live-1", "status": "active"},
            "usage": {"seconds": 45.8},
        }
    )
    assert isinstance(event, SessionClosed)
    assert event.reason == "close_requested"
    assert event.seconds == 45.8


def test_usage_is_a_fractional_running_total() -> None:
    event = decode_event(
        {
            "type": "session.usage.updated",
            "event_id": "evt_usage_001",
            "usage": {"seconds": 32.5},
            "context_window": {"usage_ratio": 0.12},
        }
    )
    assert isinstance(event, SessionUsageUpdated)
    assert event.seconds == 32.5
    assert event.context_window_usage_ratio == 0.12


def test_info_notices_are_decoded() -> None:
    event = decode_event(
        {
            "type": "info",
            "event_id": "evt_info_001",
            "code": "data_channel_permissions",
            "message": "The frontend data channel is configured with restricted event permissions.",
        }
    )
    assert isinstance(event, SessionInfo)
    assert event.code == "data_channel_permissions"


def test_error_envelope_is_flattened() -> None:
    event = decode_event(
        {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_audio",
                "message": "PCM16 audio must contain an even number of bytes",
                "param": "audio",
                "client_event_id": "event_audio_1",
            },
        }
    )
    assert isinstance(event, SessionError)
    assert event.error_type == "invalid_request_error"
    assert event.code == "invalid_audio"
    assert event.client_event_id == "event_audio_1"


def test_unknown_event_types_are_preserved_without_being_interpreted() -> None:
    event = decode_event({"type": "session.some.future.event", "detail": {"a": 1}})
    assert isinstance(event, UnrecognizedEvent)
    assert event.provider_type == "session.some.future.event"
    assert event.payload["detail"] == {"a": 1}


def test_a_malformed_known_event_does_not_become_a_decoded_fact() -> None:
    event = decode_event({"type": "session.input_transcript.delta", "delta": "hi"})
    assert isinstance(event, UnrecognizedEvent)
    assert event.provider_type == "session.input_transcript.delta"


def test_a_payload_without_a_type_is_unrecognized() -> None:
    event = decode_event({"delta": "hi"})
    assert isinstance(event, UnrecognizedEvent)
    assert event.provider_type == ""
