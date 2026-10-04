"""A minimal browser exporter that speaks the documented ingestion frames.

It reproduces what `@utrace/browser-sdk`'s `IngestTransport` puts on the wire:
the same subprotocol offer, the same `stream.open`, `observation.batch`, and
`stream.end` frames, and the same acknowledgement handling. It runs over a real
WebSocket against the real server, so nothing about the protocol is simulated.

The SDK itself is TypeScript and cannot be driven from a Python test process, so
this client stands in for it frame for frame. `tests/ingestion/test_frame_contracts.py`
holds it to the shared contract fixtures.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from json import dumps, loads
from types import TracebackType
from typing import Self
from uuid import UUID

from websockets.asyncio.client import ClientConnection, connect

from app.ids import uuid7

__all__ = ["BrowserExporter", "observation", "request_started"]

INGEST_SUBPROTOCOL = "utrace.browser.v1"
CREDENTIAL_PREFIX = "utrace.credential."


def _rfc3339(moment: datetime) -> str:
    """Render an instant as RFC 3339 UTC with a literal ``Z``."""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def observation(
    *,
    observation_type: str,
    runtime_id: UUID,
    document_attachment_id: UUID,
    source_sequence: int,
    export_sequence: int,
    payload: Mapping[str, object],
    trace_id: str | None,
    span_id: str | None,
    target_id: str | None,
    occurred_at: datetime | None,
) -> dict[str, object]:
    """Build one observation envelope exactly as the exporter encodes it."""
    wire: dict[str, object] = {
        "observation_id": str(uuid7()),
        "observation_type": observation_type,
        "runtime_id": str(runtime_id),
        "document_attachment_id": str(document_attachment_id),
        "frame_id": "top",
        "source_sequence": source_sequence,
        "export_sequence": export_sequence,
        "occurred_at": _rfc3339(occurred_at or datetime.now(tz=UTC)),
        "monotonic_offset": float(source_sequence) * 10.0,
        "route_revision": 1,
        "visibility_state": "visible",
        "payload": dict(payload),
    }
    if target_id is not None:
        wire["target_id"] = target_id
        wire["target_revision"] = 1
    if trace_id is not None:
        wire["trace_id"] = trace_id
    if span_id is not None:
        wire["span_id"] = span_id
    return wire


def request_started(browser_request_id: str) -> dict[str, object]:
    """Return a `request.started` payload with its paired request identity."""
    return {
        "browser_request_id": browser_request_id,
        "transport": "fetch",
        "method": "POST",
        "route": {
            "route_template": "/api/saved-views",
            "unknown_route": False,
            "segment_count": 2,
            "has_query": False,
            "has_fragment": False,
            "params": [],
        },
        "same_origin": True,
        "trace_context_injected": True,
        "operation_id": "saved_views.create",
        "actor": "user",
    }


class BrowserExporter:
    """One top-document exporter against the real ingestion WebSocket."""

    def __init__(
        self,
        url: str,
        *,
        token: str,
        scope: Mapping[str, object],
        browser_stream_id: UUID,
        runtime_id: UUID,
        document_attachment_id: UUID,
    ) -> None:
        self._url = url
        self._token = token
        self._scope = dict(scope)
        self.browser_stream_id = browser_stream_id
        self.runtime_id = runtime_id
        self.document_attachment_id = document_attachment_id
        self._socket: ClientConnection | None = None

    async def __aenter__(self) -> Self:
        self._socket = await connect(
            self._url,
            subprotocols=[INGEST_SUBPROTOCOL, f"{CREDENTIAL_PREFIX}{self._token}"],  # type: ignore[list-item]
            open_timeout=10,
            close_timeout=5,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        socket = self._socket
        self._socket = None
        if socket is not None:
            await socket.close()

    def _require_socket(self) -> ClientConnection:
        if self._socket is None:
            raise RuntimeError("the exporter is not connected")
        return self._socket

    async def _send(self, frame: Mapping[str, object]) -> None:
        await self._require_socket().send(dumps(frame))

    async def receive(self) -> dict[str, object]:
        """Read one server frame."""
        raw = await self._require_socket().recv()
        decoded = loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise TypeError("ingestion sent a frame that is not an object")
        return decoded

    async def open_stream(self, *, resume_from: int) -> dict[str, object]:
        """Send ``stream.open`` and return the answer."""
        await self._send(
            {
                "kind": "stream.open",
                "schema_version": "1",
                "protocol_version": "1",
                "browser_stream_id": str(self.browser_stream_id),
                "runtime_id": str(self.runtime_id),
                "document_attachment_id": str(self.document_attachment_id),
                "scope": self._scope,
                "resume_from_export_sequence": resume_from,
            }
        )
        return await self.receive()

    async def send_batch(
        self, observations: Sequence[Mapping[str, object]], *, batch_id: UUID | None
    ) -> dict[str, object]:
        """Send one ``observation.batch`` and return the answer."""
        sequences = [int(str(record["export_sequence"])) for record in observations]
        await self._send(
            {
                "kind": "observation.batch",
                "batch_id": str(batch_id or uuid7()),
                "browser_stream_id": str(self.browser_stream_id),
                "first_export_sequence": min(sequences),
                "last_export_sequence": max(sequences),
                "observations": [dict(record) for record in observations],
            }
        )
        return await self.receive()

    async def end_stream(self, *, reason: str, terminal: int) -> None:
        """Send ``stream.end`` and wait for ingestion to close the socket.

        Ingestion closes only after the terminal sequence is committed, so
        waiting for the close is what makes the end durable from the caller's
        point of view. ``paused`` leaves the stream resumable.
        """
        socket = self._require_socket()
        await self._send(
            {
                "kind": "stream.end",
                "browser_stream_id": str(self.browser_stream_id),
                "terminal_export_sequence": terminal,
                "reason": reason,
            }
        )
        await socket.wait_closed()
