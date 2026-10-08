"""The real processes the live preview journey needs beside the control plane.

A Vercel Sandbox preview runtime is not reachable from a workstation and cannot reach
one: the sandbox's Server SDK opens a control socket and exports OTLP to a public
origin, and a laptop has none. The journey therefore publishes the running
control plane through a Cloudflare quick tunnel for the length of the run, starts
the real preview front door against it, and drives a real Chrome at the session
origin. Nothing here simulates a component: every process is the one a
deployment runs.

Every helper is a context manager that stops what it started, because a leaked
tunnel or front door would answer for a control plane that is no longer there.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import socket
import ssl
from base64 import b64encode
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from tempfile import mkdtemp

import httpx
import pytest
import websockets
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from cryptography.x509.oid import NameOID

__all__ = [
    "FRONT_DOOR_LOG_PATH",
    "FRONT_DOOR_PORT",
    "TLS_PORT",
    "FrontDoor",
    "PreviewBrowser",
    "PreviewTls",
    "PublicTunnel",
    "chrome_binary",
    "front_door",
    "open_preview_in_chrome",
    "preview_tls",
    "public_tunnel",
    "require_free_port",
]

#: This workstream's front-door port, and the TLS port in front of it.
FRONT_DOOR_PORT = 3170
TLS_PORT = 3171

#: Where the front door's own output is kept for the length of a live run.
FRONT_DOOR_LOG_PATH = Path("/tmp/utrace-preview-front-door.log")

_TUNNEL_HOST = re.compile(r"https://([a-z0-9-]+\.trycloudflare\.com)")
_TUNNEL_START_SECONDS = 60.0
_FRONT_DOOR_BUILD_SECONDS = 600.0
_FRONT_DOOR_START_SECONDS = 120.0
_CHROME_DEBUG_PORT = 9270
_CHROME_START_SECONDS = 30.0
_PAGE_SETTLE_SECONDS = 90.0
_POLL_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class PublicTunnel:
    """A public origin that reaches the control plane running on this machine."""

    host: str

    @property
    def control_url(self) -> str:
        """The ``wss://`` origin a prepared runtime's Server SDK connects to."""
        return f"wss://{self.host}"

    @property
    def ingest_url(self) -> str:
        """The ``https://`` origin that runtime exports OTLP batches to."""
        return f"https://{self.host}"


async def _wait_for_line(
    process: asyncio.subprocess.Process,
    *,
    pattern: re.Pattern[str],
    timeout_seconds: float,
    what: str,
) -> str:
    """Read the process's merged output until one line matches, or fail."""
    if process.stdout is None:
        raise RuntimeError(f"{what} was started without a readable pipe")
    seen: list[str] = []

    async def scan() -> str:
        assert process.stdout is not None
        while True:
            raw = await process.stdout.readline()
            if not raw:
                raise RuntimeError(
                    f"{what} exited before it was ready:\n" + "".join(seen[-20:])
                )
            line = raw.decode("utf-8", errors="replace")
            seen.append(line)
            found = pattern.search(line)
            if found is not None:
                return found.group(1)

    try:
        return await asyncio.wait_for(scan(), timeout=timeout_seconds)
    except TimeoutError as error:
        raise RuntimeError(
            f"{what} was not ready within {timeout_seconds:.0f}s:\n"
            + "".join(seen[-20:])
        ) from error


async def _stop(process: asyncio.subprocess.Process) -> None:
    """Stop a helper and everything it spawned, then wait for it.

    The signal goes to the process group rather than to the process: ``npx``
    execs a server of its own, and a wrapper that exits while its child keeps
    the port would leave a door answering for a control plane that is gone.
    """
    if process.returncode is not None:
        return
    for send in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(process.pid), send)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
            return
        except TimeoutError:
            continue
    await process.wait()


def require_free_port(port: int, *, what: str) -> None:
    """Refuse to start when something already serves this port.

    A leaked helper from an earlier run answers on the same port with the
    previous run's configuration, and every request to it hangs or is refused
    for reasons that have nothing to do with the code under test.
    """
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(
                f"127.0.0.1:{port} is already in use, so {what} cannot start"
            ) from error


@asynccontextmanager
async def public_tunnel(*, origin_port: int) -> AsyncIterator[PublicTunnel]:
    """Publish ``127.0.0.1:<origin_port>`` at a temporary public HTTPS origin.

    The runtime's control socket and OTLP exporter both address the control
    plane by name, so the journey needs a real name that resolves from inside a
    Vercel sandbox. A Cloudflare quick tunnel is anonymous, lives only as long
    as this process, and forwards WebSocket upgrades, which the control socket
    needs.
    """
    binary = shutil.which("cloudflared")
    if binary is None:
        pytest.skip("cloudflared is not installed, so no public origin can be published")
    process = await asyncio.create_subprocess_exec(
        binary,
        "tunnel",
        "--no-autoupdate",
        "--url",
        f"http://127.0.0.1:{origin_port}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        host = await _wait_for_line(
            process,
            pattern=_TUNNEL_HOST,
            timeout_seconds=_TUNNEL_START_SECONDS,
            what="the Cloudflare quick tunnel",
        )
        yield PublicTunnel(host=host)
    finally:
        await _stop(process)


@dataclass(frozen=True, slots=True)
class FrontDoor:
    """The preview front door serving session origins on this machine."""

    port: int
    apex: str

    @property
    def base_url(self) -> str:
        """Where a request arrives when a session origin resolves to loopback."""
        return f"http://127.0.0.1:{self.port}"

    def headers_for(self, host: str) -> dict[str, str]:
        """The ``Host`` header that makes a loopback request a session request."""
        return {"host": host}


@asynccontextmanager
async def front_door(
    *,
    repository_root: Path,
    control_plane_base_url: str,
    service_token: str,
    apex: str,
    application_port: int,
) -> AsyncIterator[FrontDoor]:
    """Run the real preview front door against the control plane under test.

    A production build, not the dev server: the journey asserts on the door's
    latency-sensitive behaviour behind the same probe deadlines a deployment
    has, and a dev server compiles each route on its first request instead.
    """
    directory = repository_root / "apps" / "preview-front-door"
    if not (directory / "node_modules").is_dir():
        pytest.skip(f"{directory}/node_modules is absent; run npm ci there first")
    environment = {
        **os.environ,
        "UTRACE_CONTROL_PLANE_BASE_URL": control_plane_base_url,
        "UTRACE_CONTROL_PLANE_SERVICE_TOKEN": service_token,
        "UTRACE_CONTROL_PLANE_TIMEOUT_MS": "10000",
        "UTRACE_CONTROL_PLANE_ATTEMPTS": "3",
        "UTRACE_PREVIEW_APEX": apex,
        "UTRACE_SESSION_COOKIE_NAME": "__Host-utrace-preview-session",
        "UTRACE_SESSION_COOKIE_MAX_AGE_SECONDS": "43200",
        "UTRACE_UPSTREAM_APPLICATION_PORT": str(application_port),
        "UTRACE_ROUTE_CONTEXT_TTL_SECONDS": "60",
        "PORT": str(FRONT_DOOR_PORT),
    }
    require_free_port(FRONT_DOOR_PORT, what="the preview front door")
    await _build_front_door(directory, environment=environment)
    process = await asyncio.create_subprocess_exec(
        "npx",
        "next",
        "start",
        "-p",
        str(FRONT_DOOR_PORT),
        cwd=directory,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        await _wait_for_line(
            process,
            pattern=re.compile(r"(Ready|ready) in"),
            timeout_seconds=_FRONT_DOOR_START_SECONDS,
            what="the preview front door",
        )
        # The door's own log is the only account of why it refused a relay, and
        # a journey that fails at the browser has no other way to read it.
        drain = asyncio.create_task(_drain(process, FRONT_DOOR_LOG_PATH))
        try:
            yield FrontDoor(port=FRONT_DOOR_PORT, apex=apex)
        finally:
            drain.cancel()
    finally:
        await _stop(process)


async def _drain(process: asyncio.subprocess.Process, path: Path) -> None:
    """Write a helper's remaining output to a file, line by line."""
    if process.stdout is None:
        return
    with path.open("w", encoding="utf-8") as sink:
        while True:
            raw = await process.stdout.readline()
            if not raw:
                return
            sink.write(raw.decode("utf-8", errors="replace"))
            sink.flush()


async def _build_front_door(
    directory: Path, *, environment: Mapping[str, str]
) -> None:
    """Produce the door's production build, or fail with its own output."""
    build = await asyncio.create_subprocess_exec(
        "npx",
        "next",
        "build",
        cwd=directory,
        env=dict(environment),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await asyncio.wait_for(
        build.communicate(), timeout=_FRONT_DOOR_BUILD_SECONDS
    )
    if build.returncode != 0:
        raise RuntimeError(
            "the preview front door did not build:\n"
            + output.decode("utf-8", errors="replace")[-4000:]
        )


@dataclass(frozen=True, slots=True)
class PreviewTls:
    """TLS in front of the local door, and the key Chrome is told to accept."""

    spki_sha256_base64: str

    @property
    def port(self) -> int:
        """Where a session origin's HTTPS request arrives."""
        return TLS_PORT


@asynccontextmanager
async def preview_tls(*, apex: str) -> AsyncIterator[PreviewTls]:
    """Terminate TLS for every session origin in front of the local front door.

    The door issues a ``__Host-`` session cookie, which a browser stores only on
    a secure origin, and a session origin is a subdomain so ``localhost``'s
    exemption does not reach it. The
    ``--unsafely-treat-insecure-origin-as-secure`` flag does not change that: Chrome
    still dropped the cookie, and every request after the claim would then
    arrive without a session and prove nothing about routing. A run-scoped
    certificate is generated here and Chrome is told to accept that one key,
    which is what the deployed door's certificate does for real.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"*.{apex}")])
    now = datetime.now(tz=UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(f"*.{apex}"), x509.DNSName(apex)]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    directory = Path(mkdtemp(prefix="utrace-preview-tls-"))
    certificate_path = directory / "certificate.pem"
    key_path = directory / "key.pem"
    certificate_path.write_bytes(certificate.public_bytes(Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate_path, key_path)
    spki = b64encode(
        sha256(
            key.public_key().public_bytes(
                Encoding.DER, PublicFormat.SubjectPublicKeyInfo
            )
        ).digest()
    ).decode("ascii")

    async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Pump one accepted connection to the door and back, byte for byte."""
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                "127.0.0.1", FRONT_DOOR_PORT
            )
        except OSError:
            writer.close()
            return
        await asyncio.gather(
            _pump(reader, upstream_writer),
            _pump(upstream_reader, writer),
            return_exceptions=True,
        )

    require_free_port(TLS_PORT, what="the preview TLS front")
    server = await asyncio.start_server(relay, "127.0.0.1", TLS_PORT, ssl=context)
    try:
        yield PreviewTls(spki_sha256_base64=spki)
    finally:
        server.close()
        await server.wait_closed()
        shutil.rmtree(directory, ignore_errors=True)


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy one direction of a proxied connection until it ends."""
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, ssl.SSLError):
        return
    finally:
        writer.close()


def chrome_binary() -> Path:
    """Return the newest Chrome for Testing build in the Playwright cache.

    The journey needs Chrome 137 or newer: every activation envelope is verified
    with WebCrypto Ed25519, which older builds cannot do, and a missing key is
    reported as an activation failure rather than a browser one.
    """
    cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    builds = sorted(
        (
            path
            for path in cache.glob(
                "chromium-*/chrome-mac-arm64/Google Chrome for Testing.app"
                "/Contents/MacOS/Google Chrome for Testing"
            )
            if path.is_file()
        ),
        key=lambda path: int(path.parents[4].name.removeprefix("chromium-")),
    )
    if not builds:
        pytest.skip(f"no Chrome for Testing build under {cache}")
    return builds[-1]


class PreviewBrowser:
    """One real Chrome page, driven over its own DevTools WebSocket.

    Only what the journey needs: open a URL, wait for the browser SDK's
    activation gate to release the application, make a same-origin request the
    product's own code would make, and read what rendered.
    """

    def __init__(self, socket: websockets.ClientConnection, *, origin: str) -> None:
        self._socket = socket
        self._origin = origin
        self._next_id = 0

    async def _call(
        self, method: str, params: Mapping[str, object]
    ) -> Mapping[str, object]:
        """Send one command and return its result."""
        self._next_id += 1
        request_id = self._next_id
        await self._socket.send(
            json.dumps({"id": request_id, "method": method, "params": dict(params)})
        )
        while True:
            raw = await asyncio.wait_for(self._socket.recv(), timeout=60)
            message = json.loads(raw)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"{method} failed: {message['error']}")
            result = message["result"]
            if not isinstance(result, dict):
                raise TypeError(f"{method} returned no result object")
            return result

    async def enable(self) -> None:
        """Turn on the domains this driver reads from."""
        await self._call("Runtime.enable", {})
        await self._call("Page.enable", {})

    async def evaluate(self, expression: str) -> object:
        """Evaluate one expression in the page and return its JSON value."""
        result = await self._call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        payload = result.get("result")
        if not isinstance(payload, dict):
            raise TypeError("Runtime.evaluate returned no result value")
        if "exceptionDetails" in result:
            raise RuntimeError(f"the page threw: {result['exceptionDetails']}")
        return payload.get("value")

    async def gate(self, url: str) -> tuple[str, str]:
        """Navigate, and return the activation gate's terminal state and text.

        The gate wraps the whole application in a ``data-utrace-gate`` element,
        so its presence is proof that the client product's own document reached
        this browser through the door rather than one of the door's surfaces.
        The gate is a client component, so Next.js renders a first pass on the
        server where there is no ``window`` and activation cannot run; that pass
        arrives as ``failed`` and is replaced when hydration starts the real
        gate, so the state is read until it stops changing.
        """
        await self._call("Page.navigate", {"url": url})
        deadline = asyncio.get_running_loop().time() + _PAGE_SETTLE_SECONDS
        state, text = "absent", ""
        while asyncio.get_running_loop().time() < deadline:
            answer = str(
                await self.evaluate(
                    "(() => {"
                    "  const gate = document.querySelector('[data-utrace-gate]');"
                    "  if (!gate) return 'absent|' + (document.body ? document.body.innerText : '');"
                    "  return gate.dataset.utraceGate + '|' + gate.innerText;"
                    "})()"
                )
            )
            state, _, text = answer.partition("|")
            if state == "ready":
                return state, text
            await asyncio.sleep(_POLL_SECONDS)
        return state, text

    async def open(self, url: str, *, expect: str) -> None:
        """Navigate, and wait for the product itself to render.

        The gate withholds the application subtree until the shared observation
        runtime is ready and renders its blocked surface if activation failed,
        so waiting on it is how this driver knows the browser SDK activated.
        ``expect`` is text only the client product renders: the front door's own
        surfaces carry no gate markers, so without it a neutral interstitial
        would look exactly like a released gate.

        A blocked gate is not terminal on sight. The gate is a client component,
        so Next.js renders its first pass on the server where there is no
        ``window`` and activation cannot run; that pass reaches the browser as
        the blocked surface and is replaced when hydration starts the real gate.
        Only a gate still blocked at the deadline is a failure.
        """
        await self._call("Page.navigate", {"url": url})
        deadline = asyncio.get_running_loop().time() + _PAGE_SETTLE_SECONDS
        rendered = ""
        while asyncio.get_running_loop().time() < deadline:
            state = await self.evaluate(
                "(() => {"
                "  if (!document.body) return 'loading';"
                "  const blocked = document.querySelector("
                "    '[data-testid=\"utrace-preview-blocked\"]');"
                "  if (blocked) return 'blocked:' + blocked.innerText;"
                "  if (document.querySelector("
                "    '[data-testid=\"utrace-preview-preparing\"]')) return 'preparing';"
                "  return 'released:' + document.body.innerText;"
                "})()"
            )
            answer = str(state)
            if answer.startswith("blocked:"):
                rendered = answer.removeprefix("blocked:")
            if answer.startswith("released:"):
                rendered = answer.removeprefix("released:")
                if expect in rendered:
                    return
            await asyncio.sleep(_POLL_SECONDS)
        raise AssertionError(
            f"{url} never rendered {expect!r}. The page showed:\n{rendered[:2000]}"
        )

    async def post_json(self, path: str, body: Mapping[str, object]) -> int:
        """Make one same-origin POST from the page and return its status."""
        payload = json.dumps(dict(body))
        status = await self.evaluate(
            f"fetch({json.dumps(path)}, {{method:'POST',"
            f"headers:{{'content-type':'application/json'}},"
            f"body:{json.dumps(payload)}}}).then(r => r.status)"
        )
        return int(str(status))

    async def text(self) -> str:
        """The rendered text of the current page."""
        return str(await self.evaluate("document.body ? document.body.innerText : ''"))

    async def url(self) -> str:
        """Where the page ended up."""
        return str(await self.evaluate("window.location.href"))


async def _debugger_endpoint() -> str:
    """Wait for Chrome's DevTools endpoint and return the first page's socket."""
    deadline = asyncio.get_running_loop().time() + _CHROME_START_SECONDS
    async with httpx.AsyncClient(timeout=5.0) as client:
        while asyncio.get_running_loop().time() < deadline:
            try:
                response = await client.get(
                    f"http://127.0.0.1:{_CHROME_DEBUG_PORT}/json/list"
                )
            except httpx.HTTPError:
                await asyncio.sleep(_POLL_SECONDS)
                continue
            for target in response.json():
                if target.get("type") == "page":
                    return str(target["webSocketDebuggerUrl"])
            await asyncio.sleep(_POLL_SECONDS)
    raise RuntimeError("Chrome did not expose a page target in time")


@asynccontextmanager
async def open_preview_in_chrome(
    *, session_origin: str, tls: PreviewTls
) -> AsyncIterator[PreviewBrowser]:
    """Drive one real Chrome at a session origin the local front door serves.

    The resolver rule points every session origin at that door, and the origin
    is declared trustworthy so the ``__Host-`` session cookie is stored and
    WebCrypto is available to verify the activation envelopes. Neither flag
    changes what any component does; both stand in for the TLS a deployed door
    terminates.
    """
    binary = chrome_binary()
    apex = session_origin.split(".", 1)[1]
    profile = Path(os.environ.get("TMPDIR", "/tmp")) / f"utrace-preview-chrome-{os.getpid()}"
    shutil.rmtree(profile, ignore_errors=True)
    profile.mkdir(parents=True)
    arguments: Sequence[str] = (
        str(binary),
        "--headless=new",
        f"--remote-debugging-port={_CHROME_DEBUG_PORT}",
        f"--user-data-dir={profile}",
        f"--host-resolver-rules=MAP *.{apex} 127.0.0.1:{tls.port}",
        f"--ignore-certificate-errors-spki-list={tls.spki_sha256_base64}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "about:blank",
    )
    require_free_port(_CHROME_DEBUG_PORT, what="Chrome's DevTools endpoint")
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        endpoint = await _debugger_endpoint()
        async with websockets.connect(endpoint, max_size=None) as socket:
            browser = PreviewBrowser(socket, origin=session_origin)
            await browser.enable()
            yield browser
    finally:
        await _stop(process)
        shutil.rmtree(profile, ignore_errors=True)
