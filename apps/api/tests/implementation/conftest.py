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
from collections.abc import AsyncIterator, Mapping, Sequence

import pytest
import pytest_asyncio

from app.connectors.vercel_sandboxes import (
    EgressPolicy,
    SandboxGrant,
    SandboxHandle,
    VercelSandboxes,
    sandbox_grant_from_environment,
)
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


class _CleaningSandboxes(VercelSandboxes):
    """The connector, deleting at teardown every sandbox a test created and left running.

    A test that forgets to delete its workspace, or dies before its own `finally`, would
    otherwise leave a sandbox idling until its deadline.
    """

    def __init__(self, *, grant: SandboxGrant) -> None:
        super().__init__(grant=grant)
        self._created: list[str] = []

    async def create_from_default_image(
        self,
        *,
        name: str,
        env: Mapping[str, str],
        egress: EgressPolicy,
        execution_time_limit_seconds: int,
        vcpus: int,
    ) -> SandboxHandle:
        handle = await super().create_from_default_image(
            name=name,
            env=env,
            egress=egress,
            execution_time_limit_seconds=execution_time_limit_seconds,
            vcpus=vcpus,
        )
        self._created.append(name)
        return handle

    async def create_from_snapshot(
        self,
        *,
        name: str,
        snapshot_id: str,
        ports: Sequence[int],
        env: Mapping[str, str],
        egress: EgressPolicy,
        execution_time_limit_seconds: int,
        vcpus: int,
    ) -> SandboxHandle:
        handle = await super().create_from_snapshot(
            name=name,
            snapshot_id=snapshot_id,
            ports=ports,
            env=env,
            egress=egress,
            execution_time_limit_seconds=execution_time_limit_seconds,
            vcpus=vcpus,
        )
        self._created.append(name)
        return handle

    async def delete_leftovers(self) -> None:
        for name in self._created:
            handle = await self.find(name=name)
            if handle is not None:
                await self.delete(handle)


@pytest_asyncio.fixture
async def sandboxes() -> AsyncIterator[VercelSandboxes]:
    """A Vercel Sandbox client under the configured grant.

    Every sandbox a test created through it is deleted at teardown if it still exists.
    """
    for name in ("UTRACE_VERCEL_SANDBOX_TOKEN", "UTRACE_VERCEL_TEAM_ID", "UTRACE_VERCEL_PROJECT_ID"):
        require(name)
    async with _CleaningSandboxes(grant=sandbox_grant_from_environment()) as connector:
        try:
            yield connector
        finally:
            await connector.delete_leftovers()


@pytest.fixture(scope="session")
def openai_key() -> Secret:
    """The OpenAI API key the workspace firewall injects at egress."""
    return Secret(value=require("OPENAI_API_KEY"))


@pytest.fixture
def artifact_store(database: Database, settings: Settings) -> ArtifactStore:
    require("UTRACE_SUPABASE_SECRET_KEY")
    return ArtifactStore(
        database=database,
        supabase_url=settings.supabase_url,
        service_key=settings.supabase_secret_key,
    )
