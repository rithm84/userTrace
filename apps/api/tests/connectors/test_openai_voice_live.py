"""Live smoke: GPT-Live on the OpenAI API, through a genuine WebRTC call.

`aiortc` plays the session tab: it offers one audio track and the `oai-events` data
channel, and `GptLiveSessions` creates the session from that offer exactly as the voice
route does. The backend sideband attaches before any speech, because a sideband replays
nothing from before it attached. The call then speaks a short phrase, synthesized with
OpenAI's text-to-speech at test time so no recording is committed, and the test follows
the conversation to a graceful close. One short session keeps the cost to about a minute
of voice.
"""

from __future__ import annotations

import asyncio
import fractions
import json
import time
from collections import Counter
from dataclasses import dataclass, field

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaBlackhole
from aiortc.mediastreams import AUDIO_PTIME, MediaStreamError, MediaStreamTrack
from av import AudioFrame
from openai import AsyncOpenAI

from app.connectors.openai_live import OPENAI_API_BASE_URL, GptLiveSessions, SidebandConnection
from app.connectors.openai_live_events import (
    ContextAppendAcknowledged,
    DelegationCreated,
    GptLiveEvent,
    InputAudioReflected,
    OutputAudioSegment,
    SessionClosed,
    SessionError,
    SessionUsageUpdated,
    TranscriptFragment,
    UnrecognizedEvent,
)
from tests.connectors.conftest import require_env

_PHRASE = "Hello. Please check how many saved conversion views my account has."
_PHRASE_WORDS = frozenset({"saved", "conversion", "views", "account"})
_DELEGATION_RESULT = "The account has seven saved conversion views."
_INSTRUCTIONS = (
    "You are a brief test assistant. Answer in one short sentence. For any question about "
    "the caller's account, delegate to the application and say what it returns."
)
_SAMPLE_RATE = 24_000
"""The text-to-speech `pcm` format: raw 24 kHz, 16-bit signed little-endian mono."""
_FRAME_SAMPLES = int(AUDIO_PTIME * _SAMPLE_RATE)
_FRAME_BYTES = _FRAME_SAMPLES * 2
_BROWSER_EVENT_TYPES = frozenset(
    {"session.input_transcript.delta", "session.output_transcript.delta"}
)
_CONNECT_TIMEOUT_SECONDS = 20.0
_CONVERSATION_TIMEOUT_SECONDS = 90.0
_CLOSE_TIMEOUT_SECONDS = 30.0
_QUIET_SECONDS = 2.0
"""How long the conversation must go without a transcript, delegation, or acknowledgement
before the test treats the agent's reply as finished. Reflected audio never goes quiet: the
sideband receives it for every frame, including the silence the microphone keeps sending."""
_POLL_SECONDS = 0.5
_BACKGROUND_EVENTS = (InputAudioReflected, OutputAudioSegment, SessionUsageUpdated)


class _PhraseTrack(MediaStreamTrack):
    """A microphone that sends silence until told to speak, then the phrase, then silence.

    Continuous input matters: GPT-Live's turn-taking needs audio after the phrase ends.
    """

    kind = "audio"

    def __init__(self, *, pcm: bytes) -> None:
        super().__init__()
        self._pcm = pcm
        self._offset = 0
        self._speaking = False
        self._start = 0.0
        self._timestamp: int | None = None

    def speak(self) -> None:
        """Start the phrase at the next frame."""
        self._speaking = True

    def phrase_progress(self) -> str:
        """How much of the phrase has been sent, for failure messages."""
        return f"{min(self._offset, len(self._pcm))}/{len(self._pcm)} bytes"

    async def recv(self) -> AudioFrame:
        """Return the next 20 ms frame, paced to real time."""
        if self.readyState != "live":
            raise MediaStreamError
        if self._timestamp is None:
            self._start = time.time()
            self._timestamp = 0
        else:
            self._timestamp += _FRAME_SAMPLES
            await asyncio.sleep(self._start + self._timestamp / _SAMPLE_RATE - time.time())
        chunk = b""
        if self._speaking and self._offset < len(self._pcm):
            chunk = self._pcm[self._offset : self._offset + _FRAME_BYTES]
            self._offset += _FRAME_BYTES
        frame = AudioFrame(format="s16", layout="mono", samples=_FRAME_SAMPLES)
        plane = frame.planes[0]
        plane.update(chunk.ljust(plane.buffer_size, b"\0"))
        frame.pts = self._timestamp
        frame.sample_rate = _SAMPLE_RATE
        frame.time_base = fractions.Fraction(1, _SAMPLE_RATE)
        return frame


@dataclass
class _Conversation:
    """What the sideband observed, for the assertions."""

    heard: list[str] = field(default_factory=list)
    spoken: list[str] = field(default_factory=list)
    delegations: list[str] = field(default_factory=list)
    commentary_acknowledged: int = 0
    errors: list[SessionError] = field(default_factory=list)
    unrecognized: list[str] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    closed: SessionClosed | None = None

    def heard_words(self) -> set[str]:
        """Phrase words present in the participant's transcript."""
        text = "".join(self.heard).lower()
        return {word for word in _PHRASE_WORDS if word in text}

    def summary(self) -> str:
        """Everything observed so far, for the report and for failure messages."""
        return (
            f"heard={''.join(self.heard)!r} spoken={''.join(self.spoken)!r} "
            f"delegations={len(self.delegations)} "
            f"commentary_acknowledged={self.commentary_acknowledged} closed={self.closed} "
            f"sideband_events={dict(self.counts)} "
            f"unrecognized={sorted(set(self.unrecognized))} "
            f"errors={[(error.code, error.message) for error in self.errors]}"
        )

    def reply_finished(self) -> bool:
        """The phrase was heard, the agent answered, and every delegation was accepted."""
        return (
            len(self.heard_words()) >= 2
            and bool("".join(self.spoken).strip())
            and self.commentary_acknowledged >= len(self.delegations)
        )


async def _synthesize_phrase(api_key: str) -> bytes:
    """Speak the phrase with OpenAI's text-to-speech as raw 24 kHz PCM16."""
    async with AsyncOpenAI(api_key=api_key) as client:
        speech = await client.audio.speech.create(
            model="gpt-4o-mini-tts", voice="coral", input=_PHRASE, response_format="pcm"
        )
    return speech.content


async def _read_events(sideband: SidebandConnection, queue: asyncio.Queue[GptLiveEvent]) -> None:
    """Move decoded sideband events onto a queue so the reader can notice silence."""
    async for event in sideband.events():
        await queue.put(event)


async def _converse(
    sideband: SidebandConnection, track: _PhraseTrack, conversation: _Conversation
) -> None:
    """Speak the phrase, answer any delegation, then close once the reply is finished."""
    queue: asyncio.Queue[GptLiveEvent] = asyncio.Queue()
    reader = asyncio.create_task(_read_events(sideband, queue))
    track.speak()
    loop = asyncio.get_running_loop()
    last_activity = loop.time()
    close_deadline: float | None = None
    try:
        while conversation.closed is None:
            now = loop.time()
            if close_deadline is not None and now > close_deadline:
                raise AssertionError("session.close produced no session.closed in time")
            quiet = now - last_activity >= _QUIET_SECONDS
            if close_deadline is None and quiet and conversation.reply_finished():
                await sideband.request_close()
                close_deadline = now + _CLOSE_TIMEOUT_SECONDS
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_POLL_SECONDS)
            except TimeoutError:
                if reader.done():
                    reader.result()
                    raise AssertionError("the sideband ended without session.closed") from None
                continue
            if not isinstance(event, _BACKGROUND_EVENTS):
                last_activity = loop.time()
            await _observe(sideband, conversation, event, closing=close_deadline is not None)
    finally:
        reader.cancel()


async def _observe(
    sideband: SidebandConnection,
    conversation: _Conversation,
    event: GptLiveEvent,
    *,
    closing: bool,
) -> None:
    """Record one event and answer a client delegation with a commentary append."""
    conversation.counts[event.type] += 1
    if isinstance(event, TranscriptFragment):
        target = conversation.heard if event.speaker() == "participant" else conversation.spoken
        target.append(event.delta)
    elif isinstance(event, DelegationCreated):
        assert event.target == "client"
        if not closing:
            conversation.delegations.append(event.delegation_id)
            await sideband.append_commentary(
                content=_DELEGATION_RESULT, delegation_id=event.delegation_id
            )
    elif isinstance(event, ContextAppendAcknowledged):
        if event.type == "session.commentary.appended":
            conversation.commentary_acknowledged += 1
    elif isinstance(event, SessionError):
        conversation.errors.append(event)
    elif isinstance(event, SessionClosed):
        conversation.closed = event
    elif isinstance(event, UnrecognizedEvent):
        conversation.unrecognized.append(event.provider_type)


async def _wait_connected(peer: RTCPeerConnection, connected: asyncio.Event) -> None:
    """Wait until ICE and DTLS complete, so the phrase reaches the provider."""
    try:
        await asyncio.wait_for(connected.wait(), timeout=_CONNECT_TIMEOUT_SECONDS)
    except TimeoutError:
        raise AssertionError(
            f"the WebRTC call did not connect; state is {peer.connectionState}"
        ) from None


async def test_a_webrtc_call_is_transcribed_delegated_and_closed() -> None:
    """A real call: SDP answer, sideband, both transcripts, delegation, and graceful close."""
    api_key = require_env("OPENAI_API_KEY")["OPENAI_API_KEY"]
    track = _PhraseTrack(pcm=await _synthesize_phrase(api_key))

    peer = RTCPeerConnection()
    conversation = _Conversation()
    blackhole = MediaBlackhole()
    connected = asyncio.Event()
    browser_events: list[str] = []
    peer.addTrack(track)
    channel = peer.createDataChannel("oai-events")

    @channel.on("message")
    def on_message(message: str | bytes) -> None:
        if isinstance(message, str):
            browser_events.append(message)

    @peer.on("track")
    def on_track(remote: MediaStreamTrack) -> None:
        blackhole.addTrack(remote)

    @peer.on("connectionstatechange")
    def on_state() -> None:
        if peer.connectionState == "connected":
            connected.set()

    try:
        await peer.setLocalDescription(await peer.createOffer())
        async with GptLiveSessions(
            api_key=api_key,
            base_url=OPENAI_API_BASE_URL,
            model="gpt-live-1",
            request_timeout_seconds=30.0,
        ) as sessions:
            handle = await sessions.create_session(
                offer_sdp=peer.localDescription.sdp, instructions=_INSTRUCTIONS, voice="marin"
            )
            assert handle.provider_session_id
            assert handle.answer_sdp.startswith("v=0")
            await peer.setRemoteDescription(
                RTCSessionDescription(sdp=handle.answer_sdp, type="answer")
            )
            await blackhole.start()
            async with sessions.attach_sideband(
                provider_session_id=handle.provider_session_id
            ) as sideband:
                await _wait_connected(peer, connected)
                try:
                    await asyncio.wait_for(
                        _converse(sideband, track, conversation),
                        timeout=_CONVERSATION_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    raise AssertionError(
                        f"the conversation did not finish; phrase sent "
                        f"{track.phrase_progress()}; {conversation.summary()}; "
                        f"browser events {len(browser_events)}"
                    ) from None
    finally:
        await blackhole.stop()
        await peer.close()

    browser_types = Counter(str(json.loads(message).get("type")) for message in browser_events)
    print(
        "gpt-live live smoke:",
        f"session={handle.provider_session_id}",
        conversation.summary(),
        f"browser_events={dict(browser_types)}",
    )
    assert len(conversation.heard_words()) >= 2
    assert "".join(conversation.spoken).strip()
    assert conversation.commentary_acknowledged == len(conversation.delegations)
    assert conversation.errors == []
    assert conversation.closed is not None
    assert conversation.closed.reason == "close_requested"
    assert set(browser_types) <= _BROWSER_EVENT_TYPES | {"info"}
    assert browser_types["session.input_transcript.delta"] > 0
