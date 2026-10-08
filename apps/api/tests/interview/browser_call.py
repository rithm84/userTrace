"""A voice call through the real session tab, API, and worker, on the local stack.

The opt-in browser voice tests run every process a participant's call touches:
the API (uvicorn), the worker's two Job loops as `utrace-worker` runs them (its
main lane, and its voice lane process `app.execution.voice_lane`), without the
Discord consumers so a local run never opens a gateway session with the
production bot token, and the web app as a production
build (`next build`, `next start`), all against the local Supabase stack. A
development server would mount every effect twice and open each call twice. Chrome is driven by
`tests/e2e-browser/voice/drive.ts`, which signs in as the session's participant,
speaks the participant's recorded lines through a synthetic microphone, and
measures what the session tab actually plays.

`UTRACE_E2E_VOICE_LANE_COMMAND` replaces how the voice lane starts, for running it
from the production image under a CPU limit (`docker run --cpus ...`).

Run as `python -m tests.interview.browser_call worker` to start the main lane alone.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from logging import INFO
from pathlib import Path
from typing import Final
from uuid import UUID

import httpx
import numpy as np
from openai import AsyncOpenAI

from app.db.pool import Database
from app.execution.handlers import VOICE_JOB_TYPES, build_exhausted_handlers, build_handlers
from app.execution.worker import Worker, default_config
from app.ids import uuid7
from app.logging import configure_logging
from app.settings import get_settings

__all__ = [
    "BrowserReport",
    "LocalStack",
    "Participant",
    "local_stack",
    "participant_user",
    "run_browser",
    "synthesize",
]

_API_ROOT: Final = Path(__file__).resolve().parents[2]
_REPOSITORY: Final = _API_ROOT.parents[1]
_WEB_ROOT: Final = _REPOSITORY / "apps" / "web"
_DRIVER: Final = _REPOSITORY / "tests" / "e2e-browser" / "voice" / "drive.ts"
_API_PORT: Final = 8155
_WEB_PORT: Final = 3171
_WEB_READY_SECONDS: Final = 60.0
_PROCESS_READY_SECONDS: Final = 60.0
#: Any 32 lowercase a-p letters form a syntactically valid extension identifier.
_EXTENSION_ID: Final = "abcdefghijklmnopabcdefghijklmnop"


@dataclass(frozen=True, slots=True)
class Participant:
    """A local Supabase user signed in through the participant's provider, Discord."""

    auth_user_id: UUID
    access_token: str
    refresh_token: str


@dataclass(frozen=True, slots=True)
class BrowserReport:
    """What the driver saw: its timeline, the transcript the tab showed, and playback."""

    events: tuple[Mapping[str, object], ...]
    transcript: str

    def kinds(self, kind: str) -> list[Mapping[str, object]]:
        return [event for event in self.events if event["kind"] == kind]

    def first(self, kind: str, *, after: float) -> float | None:
        """When the first event of a kind came at or after a moment of the run."""
        times = [float(str(event["at"])) for event in self.kinds(kind)]
        return next((at for at in times if at >= after), None)

    def text(self, kind: str) -> list[str]:
        return [str(event["detail"]) for event in self.kinds(kind)]


def participant_user(*, supabase_url: str, secret_key: str, publishable_key: str) -> Participant:
    """Create a confirmed local user whose sessions carry the Discord provider.

    The session tab admits a participant only through Discord, so the user's
    `app_metadata` names that provider the way a Discord sign-in records it. This is
    a local Supabase account for the test; it is never created anywhere else.
    """
    admin = {"apikey": secret_key, "Authorization": f"Bearer {secret_key}"}
    email = f"utrace-voice-{uuid7().hex[:12]}@example.com"
    password = f"Ut-{uuid7().hex}"
    created = httpx.post(
        f"{supabase_url}/auth/v1/admin/users",
        headers=admin,
        json={
            "email": email,
            "password": password,
            "email_confirm": True,
            "app_metadata": {"provider": "discord", "providers": ["discord"]},
        },
        timeout=30.0,
    )
    if created.status_code >= 300:
        raise RuntimeError(f"local participant creation failed: {created.status_code} {created.text}")
    signed_in = httpx.post(
        f"{supabase_url}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": publishable_key},
        json={"email": email, "password": password},
        timeout=30.0,
    )
    if signed_in.status_code >= 300:
        raise RuntimeError(f"local participant sign-in failed: {signed_in.status_code} {signed_in.text}")
    body = signed_in.json()
    provider = body["user"]["app_metadata"].get("provider")
    if provider != "discord":
        raise RuntimeError(f"the local participant signs in as {provider!r}, not Discord")
    return Participant(
        auth_user_id=UUID(body["user"]["id"]),
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
    )


async def synthesize(client: AsyncOpenAI, text: str, path: Path) -> Path:
    """Write one participant line as 48 kHz PCM16, the synthetic microphone's rate."""
    speech = await client.audio.speech.create(
        model="gpt-4o-mini-tts", voice="coral", input=text, response_format="pcm"
    )
    samples = np.frombuffer(speech.content, dtype="<i2").astype(np.float32)
    upsampled = np.interp(
        np.arange(len(samples) * 2) / 2.0, np.arange(len(samples)), samples
    )
    path.write_bytes(upsampled.astype("<i2").tobytes())
    return path


@dataclass
class LocalStack:
    """The local processes one call runs on, and where their logs are."""

    logs: Path
    web_origin: str
    processes: dict[str, subprocess.Popen[bytes]]

    def start_worker(self) -> None:
        """Start the worker's two lanes; a test may start them late to delay the agent."""
        if "worker" in self.processes:
            raise RuntimeError("the worker is already running")
        self.processes["worker"] = _spawn(
            "worker",
            [sys.executable, "-m", "tests.interview.browser_call", "worker"],
            cwd=_API_ROOT,
            env=dict(os.environ),
            logs=self.logs,
        )
        override = os.environ.get("UTRACE_E2E_VOICE_LANE_COMMAND")
        self.processes["voice"] = _spawn(
            "voice",
            override.split() if override else [sys.executable, "-m", "app.execution.voice_lane"],
            cwd=_API_ROOT,
            env=dict(os.environ),
            logs=self.logs,
        )

    def log(self, name: str) -> str:
        return (self.logs / f"{name}.log").read_text(errors="replace")


@asynccontextmanager
async def local_stack(logs: Path, *, start_worker: bool) -> AsyncIterator[LocalStack]:
    """Run the API, the worker's Job loop, and the web app until the block ends."""
    settings = get_settings()
    api_env = dict(os.environ)
    web_env = {
        **os.environ,
        "NEXT_PUBLIC_SUPABASE_URL": settings.supabase_url,
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": settings.supabase_publishable_key,
        "NEXT_PUBLIC_UTRACE_EXTENSION_ID": _EXTENSION_ID,
        "UTRACE_API_BASE_URL": f"http://127.0.0.1:{_API_PORT}",
        "UTRACE_CLIENT_ID": str(uuid7()),
        "UTRACE_CONTROL_PLANE_FIXTURES": "0",
    }
    stack = LocalStack(logs=logs, web_origin=f"http://localhost:{_WEB_PORT}", processes={})
    try:
        stack.processes["api"] = _spawn(
            "api",
            [
                sys.executable, "-m", "uvicorn", "--factory", "app.main:create_default_app",
                "--host", "127.0.0.1", "--port", str(_API_PORT),
            ],
            cwd=_API_ROOT,
            env=api_env,
            logs=logs,
        )
        _build_web(web_env, logs)
        stack.processes["web"] = _spawn(
            "web",
            [str(_NEXT), "start", "-p", str(_WEB_PORT)],
            cwd=_WEB_ROOT,
            env=web_env,
            logs=logs,
        )
        if start_worker:
            stack.start_worker()
        await _ready(f"http://127.0.0.1:{_API_PORT}/health", _PROCESS_READY_SECONDS, stack, "api")
        await _ready(stack.web_origin, _WEB_READY_SECONDS, stack, "web")
        yield stack
    finally:
        for process in stack.processes.values():
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
        for process in stack.processes.values():
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def run_browser(
    *,
    stack: LocalStack,
    session_id: UUID,
    participant: Participant,
    clips: Mapping[str, Path],
    steps: Sequence[Mapping[str, object]],
    work: Path,
    timeout_seconds: float,
) -> BrowserReport:
    """Drive Chrome through the steps against the session tab and return its report."""
    settings = get_settings()
    report = work / "browser-report.json"
    config = work / "browser-config.json"
    config.write_text(
        json.dumps(
            {
                "webOrigin": stack.web_origin,
                "sessionPath": f"/sessions/{session_id}",
                "supabaseUrl": settings.supabase_url,
                "publishableKey": settings.supabase_publishable_key,
                "accessToken": participant.access_token,
                "refreshToken": participant.refresh_token,
                "clips": {name: str(path) for name, path in clips.items()},
                "steps": list(steps),
                "reportPath": str(report),
                "headless": True,
            }
        )
    )
    completed = subprocess.run(
        ["node", str(_DRIVER), str(config)],
        cwd=_DRIVER.parent.parent,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    (work / "browser-driver.log").write_bytes(completed.stdout + completed.stderr)
    if completed.returncode != 0:
        raise RuntimeError(
            f"the browser driver failed ({completed.returncode}): "
            f"{completed.stderr.decode(errors='replace')[-2000:]}"
        )
    body = json.loads(report.read_text())
    return BrowserReport(events=tuple(body["events"]), transcript=str(body["transcript"]))


_NEXT: Final = _WEB_ROOT / "node_modules" / ".bin" / "next"


def _build_web(env: Mapping[str, str], logs: Path) -> None:
    """Build the web app for production with this stack's public settings inlined."""
    subprocess.run(
        ["node", "scripts/sync-agent-character-assets.mjs"],
        cwd=_WEB_ROOT, env=dict(env), check=True, capture_output=True,
    )
    built = subprocess.run(
        [str(_NEXT), "build", "--webpack"],
        cwd=_WEB_ROOT, env=dict(env), capture_output=True, check=False,
    )
    (logs / "web-build.log").write_bytes(built.stdout + built.stderr)
    if built.returncode != 0:
        raise RuntimeError(f"the web app did not build:\n{built.stdout.decode(errors='replace')[-3000:]}")


def _spawn(
    name: str, command: Sequence[str], *, cwd: Path, env: Mapping[str, str], logs: Path
) -> subprocess.Popen[bytes]:
    output = (logs / f"{name}.log").open("wb")
    return subprocess.Popen(command, cwd=cwd, env=dict(env), stdout=output, stderr=subprocess.STDOUT)


async def _ready(url: str, seconds: float, stack: LocalStack, name: str) -> None:
    deadline = time.monotonic() + seconds
    async with httpx.AsyncClient(timeout=10.0) as client:
        while time.monotonic() < deadline:
            if stack.processes[name].poll() is not None:
                raise RuntimeError(f"{name} exited early:\n{stack.log(name)[-3000:]}")
            try:
                if (await client.get(url)).status_code < 500:
                    return
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.5)
    raise RuntimeError(f"{name} was not ready at {url} within {seconds}s:\n{stack.log(name)[-3000:]}")


async def _run_worker() -> None:
    """The worker's main lane, as `utrace-worker` runs it, without the Discord consumers."""
    configure_logging(INFO)
    settings = get_settings()
    database = Database(
        dsn=settings.database_url,
        min_size=settings.database_pool_min_size,
        max_size=settings.database_pool_max_size,
    )
    await database.open()
    handlers = {
        job_type: handler
        for job_type, handler in build_handlers().items()
        if job_type not in VOICE_JOB_TYPES
    }
    worker = Worker(database, default_config(), handlers, build_exhausted_handlers())
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, worker.request_stop)
    try:
        await worker.run()
    finally:
        await database.close()


if __name__ == "__main__" and sys.argv[1:] == ["worker"]:
    asyncio.run(_run_worker())
