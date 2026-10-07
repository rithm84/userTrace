"""Test configuration for the telemetry ingestion edges.

Every test runs against the real Supabase Postgres, the real Supabase Storage
bucket, and a real uvicorn server on a real socket. There are no mocks: an
acknowledgement that is not backed by a committed row, or a WebSocket frame that
never crosses a socket, would prove nothing about the contract.
"""

from __future__ import annotations

import os
import socket
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import closing
from typing import Final

import pytest
import pytest_asyncio
import uvicorn

from app.db.pool import Database
from app.ingestion.artifacts import ArtifactStore
from app.ingestion.settings import get_ingestion_settings
from app.main import create_app
from app.ports import PortRegistry
from app.settings import Settings
from tests.conftest import require_env
from tests.ingestion.keys import SigningKeys, install_test_keys

#: The ingestion workstream's port range starts here.
INGEST_PORT: Final[int] = 8110

_TELEMETRY_BUCKET = "utrace-telemetry"


def _install_ingestion_environment() -> SigningKeys:
    """Export the ingestion configuration this run needs before settings are read."""
    keys = install_test_keys()
    os.environ.setdefault(
        "UTRACE_INGEST_WEBSOCKET_ORIGIN", f"ws://127.0.0.1:{INGEST_PORT}"
    )
    os.environ.setdefault("UTRACE_TELEMETRY_BUCKET", _TELEMETRY_BUCKET)
    get_ingestion_settings.cache_clear()
    return keys


_KEYS = _install_ingestion_environment()


@pytest.fixture(scope="session")
def keys() -> SigningKeys:
    """The run's ephemeral signing keys."""
    return _KEYS


@pytest.fixture(scope="session")
def ingest_base_url() -> str:
    """The HTTP origin the ingestion server listens on."""
    return f"http://127.0.0.1:{INGEST_PORT}"


@pytest.fixture(scope="session")
def ingest_websocket_url() -> str:
    """The browser ingestion endpoint, exactly as the credential advertises it."""
    return f"ws://127.0.0.1:{INGEST_PORT}/v1/ingest/browser"


#: One server per process. The evidence package re-exports this fixture, which
#: gives pytest two session-scoped instances of it; only the first starts a
#: server, and the rest share its socket.
_RUNNING: dict[str, str] = {}


@pytest.fixture(scope="session")
def ingest_server(settings: Settings) -> Iterator[str]:
    """Run the real application on a real socket for the whole test session.

    uvicorn runs in its own thread with its own event loop, so the server and the
    tests never share one. A test therefore exercises the same start-up path,
    routing, and WebSocket handshake that a deployment does.
    """
    require_env("UTRACE_SUPABASE_SECRET_KEY")
    if "url" in _RUNNING:
        yield _RUNNING["url"]
        return
    application = create_app(settings, PortRegistry(voice=None, observation=None, evidence=None))
    config = uvicorn.Config(
        application,
        host="127.0.0.1",
        port=INGEST_PORT,
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="ingest-server")
    thread.start()
    _wait_for_port(INGEST_PORT)
    url = f"http://127.0.0.1:{INGEST_PORT}"
    _RUNNING["url"] = url
    try:
        yield url
    finally:
        _RUNNING.clear()
        server.should_exit = True
        thread.join(timeout=20)


def _wait_for_port(port: int) -> None:
    """Block until the server accepts connections, or fail with the port named."""
    deadline = threading.Event()
    for _ in range(200):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        deadline.wait(0.1)
    raise RuntimeError(f"the ingestion server did not open port {port}")


@pytest_asyncio.fixture
async def artifacts(settings: Settings) -> AsyncIterator[ArtifactStore]:
    """One artifact store against the real private Storage bucket."""
    store = ArtifactStore(
        supabase_url=settings.supabase_url,
        service_key=settings.supabase_secret_key,
        bucket=_TELEMETRY_BUCKET,
    )
    try:
        yield store
    finally:
        await store.aclose()


@pytest_asyncio.fixture
async def telemetry_database(settings: Settings) -> AsyncIterator[Database]:
    """An open pool with the telemetry tables clean before and after each test."""
    connector = Database(dsn=settings.database_url, min_size=1, max_size=4)
    await connector.open()
    try:
        await _truncate(connector)
        yield connector
        await _truncate(connector)
    finally:
        await connector.close()


async def _truncate(database: Database) -> None:
    """Truncate exactly the tables these tests own."""
    from tests.conftest import OWNED_TABLES

    statement = "truncate table " + ", ".join(f"public.{name}" for name in OWNED_TABLES)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(statement)
