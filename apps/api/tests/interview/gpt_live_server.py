"""A GPT-Live server that speaks the OpenAI API's documented protocol, for tests.

The voice runtime's durable behaviour is proven against a local server that
implements the contract `app.connectors.openai_live` records:
`POST /v1/live/sessions` returning HTTP 201 with only the session ID and the SDP
answer, and an authenticated sideband WebSocket at
`/v1/live/sessions/{id}/attach` that replays nothing from before it attached,
emits transcript fragments, reflected audio, delegations, and session control
events, and never expects `session.start`.

This is not a mock of uTrace code. The connector, through the official `openai`
SDK, and the whole interview runtime run unmodified against a real socket; only
the provider on the far end is local. `tests/connectors/test_openai_voice_live.py`
covers the same connector against the real OpenAI API.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from app.envelope import JsonValue

__all__ = ["GptLiveTestServer", "ScriptedSession", "run_test_server"]

_HOST = "127.0.0.1"
_ANSWER_SDP = (
    "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"
    "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=recvonly\r\n"
)


@dataclass
class ScriptedSession:
    """What the server emits on attach, and what it recorded from the backend."""

    events: Sequence[Mapping[str, JsonValue]]
    received_commands: list[Mapping[str, object]] = field(default_factory=list)
    created_sessions: list[Mapping[str, object]] = field(default_factory=list)
    attached: asyncio.Event = field(default_factory=asyncio.Event)
    drained: asyncio.Event = field(default_factory=asyncio.Event)
    close_after_command_types: tuple[str, ...] = ()
    """Command types after which the server ends the session, as `session.close` would.

    Tests use it to end a run at a known point without racing the backend."""
    replies: Mapping[str, Sequence[Mapping[str, JsonValue]]] = field(default_factory=dict)
    """Events the server emits after a command of each type, as the model answers a cue."""


class GptLiveTestServer:
    """One running test server and the script it serves."""

    def __init__(self, *, port: int, script: ScriptedSession) -> None:
        self._port = port
        self._script = script
        self._app = _build_app(script)
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def base_url(self) -> str:
        """The API root the connector uses in place of `https://api.openai.com/v1`."""
        return f"http://{_HOST}:{self._port}/v1"

    @property
    def script(self) -> ScriptedSession:
        """The script, for assertions about what the backend sent."""
        return self._script

    async def start(self) -> None:
        """Start the server and wait until it accepts connections."""
        config = uvicorn.Config(
            self._app, host=_HOST, port=self._port, log_level="warning"
        )
        server = uvicorn.Server(config)
        self._server = server
        self._task = asyncio.create_task(server.serve())
        for _ in range(200):
            if server.started:
                return
            await asyncio.sleep(0.02)
        raise RuntimeError(f"the test GPT-Live server did not start on port {self._port}")

    async def stop(self) -> None:
        """Ask the server to exit and wait for its task."""
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            await self._task


@asynccontextmanager
async def run_test_server(
    *, port: int, script: ScriptedSession
) -> AsyncIterator[GptLiveTestServer]:
    """Run one test GPT-Live server for the duration of a test."""
    server = GptLiveTestServer(port=port, script=script)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


def _build_app(script: ScriptedSession) -> FastAPI:
    """Build the ASGI app that serves the documented endpoints."""
    app = FastAPI()
    sessions: set[str] = set()

    @app.post("/v1/live/sessions")
    async def create_session(request: Request) -> JSONResponse:
        """Create a session from the browser's SDP offer and return the answer."""
        if not request.headers.get("authorization", "").startswith("Bearer "):
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        body = await request.json()
        script.created_sessions.append(body)
        session = body.get("session", {})
        if session.get("delegation", {}).get("type") != "client":
            return JSONResponse(
                {"error": "this test server serves client delegation only"},
                status_code=400,
            )
        if body.get("transport", {}).get("type") != "webrtc":
            return JSONResponse({"error": "webrtc transport required"}, status_code=400)
        session_id = f"live_{len(sessions) + 1:04d}"
        sessions.add(session_id)
        return JSONResponse(
            {
                "session": {"id": session_id},
                "transport": {"type": "webrtc", "sdp": _ANSWER_SDP},
            },
            status_code=201,
        )

    @app.websocket("/v1/live/sessions/{session_id}/attach")
    async def attach(websocket: WebSocket, session_id: str) -> None:
        """Serve one sideband: emit the script, then answer backend commands.

        Attaching replays nothing, so no `session.started` is sent.
        """
        if not websocket.headers.get("authorization", "").startswith("Bearer "):
            await websocket.close(code=1008, reason="unauthenticated")
            return
        if session_id not in sessions:
            await websocket.close(code=1008, reason="unknown session")
            return
        await websocket.accept()
        script.attached.set()
        for event in script.events:
            await websocket.send_text(json.dumps(dict(event)))
            await asyncio.sleep(0.01)
        script.drained.set()
        try:
            while True:
                command = json.loads(await websocket.receive_text())
                script.received_commands.append(command)
                kind = command.get("type")
                if kind == "session.close":
                    await websocket.send_text(json.dumps(_closed(session_id, command)))
                    await websocket.close()
                    return
                await websocket.send_text(json.dumps(_acknowledgement(command)))
                for reply in script.replies.get(str(kind), ()):
                    await websocket.send_text(json.dumps(reply))
                if kind in script.close_after_command_types:
                    await websocket.send_text(json.dumps(_closed(session_id, None)))
                    await websocket.close()
                    return
        except WebSocketDisconnect:
            return

    return app


_CONTEXT_APPENDS = (
    "session.instructions.append",
    "session.thinking.append",
    "session.commentary.append",
)


def _correlation(command: Mapping[str, object] | None) -> dict[str, JsonValue]:
    """The `client_event_id` a reply carries when the command supplied an `event_id`."""
    if command is None:
        return {}
    event_id = command.get("event_id")
    return {"client_event_id": event_id} if isinstance(event_id, str) else {}


def _closed(
    session_id: str, command: Mapping[str, object] | None
) -> Mapping[str, JsonValue]:
    """The terminal `session.closed` event with its snapshot and fractional usage."""
    return {
        "type": "session.closed",
        "event_id": f"evt_closed_{session_id}",
        **_correlation(command),
        "reason": "close_requested",
        "session": {"id": session_id, "model": "gpt-live-1", "status": "active"},
        "usage": {"seconds": 12.5},
    }


def _error(
    command: Mapping[str, object], *, code: str, message: str
) -> Mapping[str, JsonValue]:
    """An `error` event for a command the server refused."""
    return {
        "type": "error",
        "event_id": "evt_error",
        "error": {
            "type": "invalid_request_error",
            "code": code,
            "message": message,
            **_correlation(command),
        },
    }


def _acknowledgement(command: Mapping[str, object]) -> Mapping[str, JsonValue]:
    """Build the acknowledgement the reference documents for one command."""
    kind = str(command.get("type", ""))
    if kind in _CONTEXT_APPENDS:
        if "delegation_id" not in command:
            return _error(
                command,
                code="missing_required_parameter",
                message="delegation_id is required; use null for general session context",
            )
        return {
            "type": kind.replace(".append", ".appended"),
            "event_id": "evt_appended",
            **_correlation(command),
            "start_ms": 0,
            "end_ms": 0,
        }
    if kind == "session.input_audio.mute":
        return {"type": "session.input_audio.muted", "event_id": "evt_muted", **_correlation(command)}
    if kind == "session.input_audio.unmute":
        return {
            "type": "session.input_audio.unmuted",
            "event_id": "evt_unmuted",
            **_correlation(command),
        }
    return _error(
        command, code="unknown_command", message=f"this test server does not implement {kind!r}"
    )
