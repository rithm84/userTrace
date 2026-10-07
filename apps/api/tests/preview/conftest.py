"""Fixtures for the preview runtime tests.

Every test runs against the real database, and the live ones against the real
Vercel Sandbox API. There are no mocks: a warm pool, a runtime port URL, a route
context a real Next.js middleware verifies, and a sandbox that is actually
deleted are the only things that prove this area works.

Preview policy is read from the process environment like the rest of the control
plane. These defaults keep the values that are pure operator policy — hold
windows, lifetimes, timeouts — out of every test, while the values that name real
resources still have to be set in the repository ``.env.local``.

The live journey needs the control plane on a real socket, and it starts its own
on this workstream's port rather than sharing the ingestion suite's: a fixed port
another checkout is already serving would be reached silently, and every
credential this run signed would be refused by a control plane holding another
run's keys. The ephemeral signing keys are shared, because two key installs in
one process would leave one suite verifying against the other's public half.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import closing
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import uvicorn
from psycopg.types.json import Jsonb

from app.connectors.vercel_sandboxes import sandbox_grant_from_environment
from app.db.pool import Database
from app.ids import uuid7
from app.ingestion.settings import get_ingestion_settings
from app.main import create_app
from app.ports import PortRegistry
from app.preview.config import (
    PreviewSettings,
    TemplateBuildSettings,
    get_preview_settings,
    get_template_build_settings,
)
from app.preview.recipe import TemplateRecipe
from app.preview.records import RuntimeTemplate, SeedIdentity
from app.preview.runtime import PreviewRuntimeProvider
from app.preview.template import load_current_template
from app.settings import Settings
from tests.conftest import require_env
from tests.ingestion.conftest import keys  # noqa: F401  (one key install per process)
from tests.preview.live_fixtures import ORIGIN_SUFFIX
from tests.preview.live_harness import (
    FrontDoor,
    PreviewTls,
    PublicTunnel,
    front_door,
    preview_tls,
    public_tunnel,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]

_PREVIEW_DEFAULTS = {
    "UTRACE_PREVIEW_TEMPLATE_RECIPE": str(
        REPOSITORY_ROOT / "deploy" / "vercel" / "preview" / "shopalytics-recipe.json"
    ),
    "UTRACE_PREVIEW_IMAGE_NAMESPACE": "vcr.vercel.com/rithm84s-projects/utrace-preview-front-door",
    "UTRACE_PREVIEW_ROUTE_TTL_SECONDS": "3600",
    "UTRACE_PREVIEW_HANDOFF_CODE_TTL_SECONDS": "120",
    "UTRACE_PREVIEW_SESSION_CREDENTIAL_TTL_SECONDS": "14400",
    "UTRACE_PREVIEW_EXECUTION_TIME_LIMIT_SECONDS": "3600",
    "UTRACE_PREVIEW_READINESS_TIMEOUT_SECONDS": "60",
    "UTRACE_PREVIEW_PUBLIC_PATH_ENABLED": "0",
    "UTRACE_PREVIEW_SERVER_SERVICE_NAME": "shopalytics_web",
    # The live test replaces these with the origin its own control plane is
    # reachable at and clears the settings cache; the rest of the suite never
    # creates a runtime, so the deployed origin is the honest default.
    "UTRACE_PREVIEW_SERVER_CONTROL_URL": "wss://api.utrace.dev",
    "UTRACE_PREVIEW_SERVER_INGEST_URL": "https://api.utrace.dev",
}

for _name, _value in _PREVIEW_DEFAULTS.items():
    os.environ.setdefault(_name, _value)

#: This workstream's control-plane port. The ingestion suite keeps its own.
CONTROL_PLANE_PORT = 8170

# The embedded browser SDK connects to the origin its telemetry credential
# advertises, which the deployed value names rather than the server this run
# started. A real browser has to be told where the run's ingestion edge actually
# is; every other suite reaches it by its own fixture URL and is unaffected.
os.environ["UTRACE_INGEST_WEBSOCKET_ORIGIN"] = f"ws://127.0.0.1:{CONTROL_PLANE_PORT}"
get_ingestion_settings.cache_clear()


@pytest.fixture(scope="session")
def control_plane(settings: Settings) -> Iterator[str]:
    """Run the real application on this workstream's socket for the whole run.

    A real socket rather than an in-process transport, because the front door,
    a prepared runtime's Server SDK, and a participant's browser all reach the
    control plane over HTTP and none of them can use an ASGI shortcut. The port
    is claimed before uvicorn starts: a listener another checkout left behind
    would otherwise be reached silently and refuse every credential this run
    signed.
    """
    require_env("UTRACE_SUPABASE_SECRET_KEY")
    _require_free_port(CONTROL_PLANE_PORT)
    application = create_app(
        settings, PortRegistry(voice=None, observation=None, evidence=None)
    )
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host="127.0.0.1",
            port=CONTROL_PLANE_PORT,
            log_level="warning",
            lifespan="on",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True, name="preview-control-plane")
    thread.start()
    _wait_for_port(CONTROL_PLANE_PORT)
    try:
        yield f"http://127.0.0.1:{CONTROL_PLANE_PORT}"
    finally:
        server.should_exit = True
        thread.join(timeout=30)


def _require_free_port(port: int) -> None:
    """Refuse to run when something else already serves this workstream's port."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(
                f"127.0.0.1:{port} is already serving something; this suite's "
                "control plane cannot share a port with another checkout's"
            ) from error


def _wait_for_port(port: int) -> None:
    """Wait until the control plane accepts connections, or fail saying so."""
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"the control plane did not accept connections on {port}")


@pytest.fixture(scope="session")
def preview_settings() -> PreviewSettings:
    """Preview policy for the run."""
    return get_preview_settings()


@pytest.fixture(scope="session")
def build_settings() -> TemplateBuildSettings:
    """The template build's inputs, which name the fixture checkout."""
    require_env("UTRACE_PREVIEW_FIXTURE_REPOSITORY")
    return get_template_build_settings()


@pytest.fixture(scope="session")
def recipe(build_settings: TemplateBuildSettings) -> TemplateRecipe:
    """The committed Shopalytics template recipe."""
    return TemplateRecipe.load(build_settings.template_recipe_path)


@pytest.fixture(scope="session")
def template_snapshot_id() -> str:
    """The template snapshot the live tests create runtimes from."""
    return require_env("UTRACE_PREVIEW_TEMPLATE_SNAPSHOT_ID")


@pytest.fixture(scope="session")
def template_image() -> str:
    """The digest-pinned registry image the template snapshot was taken from."""
    return require_env("UTRACE_PREVIEW_TEMPLATE_IMAGE")


@pytest_asyncio.fixture
async def provider() -> AsyncIterator[PreviewRuntimeProvider]:
    """A real Vercel Sandbox client, skipping with the exact variable that is missing."""
    for name in ("UTRACE_VERCEL_SANDBOX_TOKEN", "UTRACE_VERCEL_TEAM_ID", "UTRACE_VERCEL_PROJECT_ID"):
        require_env(name)
    async with PreviewRuntimeProvider(grant=sandbox_grant_from_environment()) as client:
        yield client


@pytest_asyncio.fixture
async def probe_client(
    preview_settings: PreviewSettings,
) -> AsyncIterator[httpx.AsyncClient]:
    """The HTTP client readiness probes and live assertions use."""
    async with httpx.AsyncClient(
        timeout=preview_settings.readiness_timeout_seconds, follow_redirects=False
    ) as client:
        yield client


@pytest_asyncio.fixture
async def api(control_plane: str) -> AsyncIterator[httpx.AsyncClient]:
    """A client for the control plane running on its own socket.

    The journey needs the real socket rather than an in-process transport: the
    front door, the runtime's Server SDK, and Chrome all reach the control plane
    over HTTP, and an ASGI shortcut would prove none of them.
    """
    async with httpx.AsyncClient(base_url=control_plane, timeout=30.0) as http:
        yield http


@pytest_asyncio.fixture
async def tunnel(control_plane: str) -> AsyncIterator[PublicTunnel]:
    """Publish the running control plane, and point preview policy at it."""
    port = int(control_plane.rsplit(":", 1)[1])
    async with public_tunnel(origin_port=port) as opened:
        os.environ["UTRACE_PREVIEW_SERVER_CONTROL_URL"] = opened.control_url
        os.environ["UTRACE_PREVIEW_SERVER_INGEST_URL"] = opened.ingest_url
        get_preview_settings.cache_clear()
        try:
            yield opened
        finally:
            get_preview_settings.cache_clear()


@pytest_asyncio.fixture
async def door(
    control_plane: str, settings: Settings, recipe: TemplateRecipe
) -> AsyncIterator[FrontDoor]:
    """The real preview front door, serving session origins on this machine."""
    async with front_door(
        repository_root=Path(__file__).resolve().parents[4],
        control_plane_base_url=control_plane,
        service_token=settings.front_door_service_token,
        apex=ORIGIN_SUFFIX,
        application_port=recipe.application_port,
    ) as running:
        yield running


@pytest_asyncio.fixture
async def door_tls() -> AsyncIterator[PreviewTls]:
    """TLS for every session origin, in front of the local door."""
    async with preview_tls(apex=ORIGIN_SUFFIX) as running:
        yield running


async def install_template(
    database: Database,
    *,
    client_id: UUID,
    configuration_release_id: UUID,
    recipe: TemplateRecipe,
    template_snapshot_id: str,
    template_image: str,
    warm_pool_target: int,
) -> RuntimeTemplate:
    """Pin one runtime template for a test client against the real snapshot.

    The seed identity is the fixture's committed manifest, read from the same
    repository the template was built from, so a test can never assert against a
    dataset identity the runtime does not actually hold.
    """
    build = get_template_build_settings()
    manifest_path = build.fixture_repository_path / recipe.seed_manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = manifest["identity"]["growth_lead"]
    seed = SeedIdentity(
        fixture_id=manifest["fixture_id"],
        schema_version=manifest["schema_version"],
        manifest_digest=manifest["content_hash"],
        fixture_clock=manifest["fixture_clock"],
        table_digests={
            table["name"]: table["content_hash"] for table in manifest["tables"]
        },
        authenticated_identity=identity["email"],
    )
    template_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.preview_runtime_templates (
                id, client_id, pinned_configuration_release_id, template_id,
                template_revision, snapshot_id, image_digest, recipe_digest,
                seed_manifest_digest, seed_manifest, fixture_clock, application_port,
                authenticated_identity, sdk_versions, warm_pool_target
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                template_id,
                client_id,
                configuration_release_id,
                recipe.template_id,
                recipe.template_revision,
                template_snapshot_id,
                template_image,
                "sha256:test-recipe-digest",
                seed.manifest_digest,
                Jsonb(
                    {
                        "seed": seed.model_dump(mode="json"),
                        "recipe": recipe.model_dump(mode="json"),
                        "fixture_commit": "test",
                    }
                ),
                seed.fixture_clock,
                recipe.application_port,
                seed.authenticated_identity,
                Jsonb({"@utrace/server-sdk": "0.1.0"}),
                warm_pool_target,
            ),
        )
    return await load_current_template(database, client_id=client_id)
