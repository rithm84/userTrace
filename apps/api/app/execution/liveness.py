"""The worker's liveness listener on Supabase Compute.

Compute's launcher serves whatever listens on ``$PORT``. The worker has no API of
its own, so while its loops run it answers every request on that port with
``200 ok``: the listener shows the process is up, and nothing it serves carries
state or authority.
"""

import asyncio
from typing import Final

__all__ = ["serve_liveness"]

_REQUEST_READ_SECONDS: Final[float] = 5.0
_RESPONSE: Final[bytes] = (
    b"HTTP/1.1 200 OK\r\ncontent-type: text/plain\r\ncontent-length: 2\r\n"
    b"connection: close\r\n\r\nok"
)


async def serve_liveness(port: int) -> None:
    """Answer every request on ``port`` until cancelled."""
    server = await asyncio.start_server(_answer, host="0.0.0.0", port=port)
    async with server:
        await server.serve_forever()


async def _answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Read the request head, then answer ``ok`` and close."""
    try:
        await asyncio.wait_for(
            reader.readuntil(b"\r\n\r\n"), timeout=_REQUEST_READ_SECONDS
        )
    except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        writer.close()
        return
    writer.write(_RESPONSE)
    await writer.drain()
    writer.close()
    await writer.wait_closed()
