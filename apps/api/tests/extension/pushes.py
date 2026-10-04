"""Changes the push tests make from outside the extension's own socket.

Each one reaches the control plane the way a real producer does: a gesture from
another operating-system process, as the worker's voice sideband dispatches it,
and a participant control posted to the session API, as the session tab posts
it. The socket under test learns of either only from committed state.
"""

import asyncio
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from app.interview.ports import RegisteredTarget
from tests.extension.conftest import SOCKET_PORT

__all__ = ["dispatch_point", "has_command", "post_participant_control"]

_API_ROOT = Path(__file__).resolve().parents[2]
_API_URL = f"http://127.0.0.1:{SOCKET_PORT}"
_PROCESS_TIMEOUT_SECONDS = 60.0


async def dispatch_point(
    *, client_id: UUID, session_id: UUID, target: RegisteredTarget
) -> Mapping[str, Any]:
    """Point at one target from a separate process, and return its outcome."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.extension.dispatch_process",
        str(client_id),
        str(session_id),
        target.target_id,
        str(target.target_revision),
        cwd=_API_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(
        process.communicate(), timeout=_PROCESS_TIMEOUT_SECONDS
    )
    if process.returncode != 0:
        raise AssertionError(
            f"the dispatching process exited {process.returncode}: "
            f"{stderr.decode('utf-8')[-2000:]}"
        )
    outcome = json.loads(stdout.decode("utf-8").strip().splitlines()[-1])
    if not isinstance(outcome, dict):
        raise TypeError(f"the dispatching process printed {outcome!r}")
    return outcome


def has_command(
    command_id: str,
) -> Callable[[Sequence[Mapping[str, Any]]], bool]:
    """True once a `visual_command` for ``command_id`` has arrived."""

    def arrived(frames: Sequence[Mapping[str, Any]]) -> bool:
        return any(
            frame["kind"] == "visual_command"
            and frame["payload"]["command_id"] == command_id
            for frame in frames
        )

    return arrived


async def post_participant_control(
    *, access_token: str, session_id: UUID, action: str
) -> None:
    """Post one participant control to the session API, as the session tab does."""
    async with httpx.AsyncClient(base_url=_API_URL, timeout=30) as client:
        response = await client.post(
            f"/v1/sessions/{session_id}/controls",
            headers={"authorization": f"Bearer {access_token}"},
            json={"action": action},
        )
    if response.status_code != 200:
        raise AssertionError(
            f"POST /v1/sessions/{session_id}/controls {action} returned "
            f"{response.status_code}: {response.text}"
        )
