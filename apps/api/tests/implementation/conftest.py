"""Fixtures for the live implementation tests.

Every test here runs against the real providers: the real Supabase database and
Storage, real Vercel sandboxes, and the real OpenAI API. There are no mocks,
because a mocked workspace proves only that the mock matches the guess. Each test
skips with the exact missing variable named, so a skipped run is diagnosable.

The repository clone credential is the one exception to "configuration comes
from `.env.local`": these tests read the `gh` CLI's user token at runtime and hand
it to the checkout egress policy only. It is never written to a file, a log, an
artifact, or the sandbox. Production uses the App installation token.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from app.connectors.vercel_sandboxes import VercelSandboxes, sandbox_grant_from_environment
from app.db.pool import Database
from app.implementation.artifact_store import ArtifactStore
from app.settings import Settings
from tests.implementation.driver import Secret


def require(name: str) -> str:
    """Return one environment value, or skip the test naming the variable."""
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"live implementation test needs {name}; it is unset")
    return value


@pytest.fixture(scope="session")
def workspace_snapshot_id() -> str:
    """The workspace template `scripts/build_workspace_snapshot.py` printed."""
    return require("UTRACE_IMPLEMENTATION_WORKSPACE_SNAPSHOT_ID")


@pytest.fixture(scope="session")
def github_token() -> Secret:
    """The repository read credential, resolved at runtime and never stored.

    Skips when no `gh` login is available, because a clone of the authorized
    repository cannot be faked.
    """
    try:
        completed = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("live implementation test needs the gh CLI to resolve a GitHub token")
    token = completed.stdout.strip()
    if completed.returncode != 0 or not token:
        pytest.skip("live implementation test needs `gh auth login`; no token is available")
    return Secret(value=token)


@pytest_asyncio.fixture
async def sandboxes() -> AsyncIterator[VercelSandboxes]:
    """A Vercel Sandbox client under the configured grant."""
    for name in ("UTRACE_VERCEL_SANDBOX_TOKEN", "UTRACE_VERCEL_TEAM_ID", "UTRACE_VERCEL_PROJECT_ID"):
        require(name)
    async with VercelSandboxes(grant=sandbox_grant_from_environment()) as connector:
        yield connector


@pytest.fixture(scope="session")
def openai_key() -> Secret:
    """The OpenAI API key the workspace firewall injects at egress."""
    return Secret(value=require("OPENAI_API_KEY"))


@pytest.fixture
def artifact_store(database: Database, settings: Settings) -> ArtifactStore:
    """The real Supabase Storage artifact store for this project."""
    require("UTRACE_SUPABASE_SECRET_KEY")
    return ArtifactStore(
        database=database,
        supabase_url=settings.supabase_url,
        service_key=settings.supabase_secret_key,
    )
