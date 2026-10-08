"""Fixtures for the extension-control tests.

The extension talks to the control plane over a real HTTP and WebSocket server,
so these tests run the production application with `uvicorn` and drive it with an
ordinary WebSocket client. Nothing in `app/` is replaced.

The audiences the socket and the installation API check are installed in the
environment before the settings that read them are first resolved, which is what
a deployment would do with the real values.

The control socket verifies a page proof with the same signer telemetry
ingestion issued it with, so this package also needs ingestion's configuration.
Importing the ingestion test configuration installs it once for the whole run
and keeps both packages on one set of keys.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI

import tests.ingestion.conftest  # noqa: F401  (installs the ingestion environment)
from app.ingestion.settings import get_ingestion_settings
from app.ingestion.signing import Signer
from app.interview.settings import (
    get_interview_settings,
    get_jev_settings,
    get_openai_voice_settings,
)
from app.main import create_app
from app.ports import PortRegistry
from app.preview.ports import ObservationReadinessPort
from app.settings import Settings

__all__ = [
    "API_AUDIENCE",
    "SOCKET_AUDIENCE",
    "SOCKET_PORT",
    "install_observation_readiness",
    "page_proof_signer",
    "running_api",
]

SOCKET_PORT = 8140
API_AUDIENCE = "https://api.utrace.test"
SOCKET_AUDIENCE = f"ws://127.0.0.1:{SOCKET_PORT}"

os.environ["UTRACE_EXTENSION_API_AUDIENCE"] = API_AUDIENCE
os.environ["UTRACE_EXTENSION_SOCKET_AUDIENCE"] = SOCKET_AUDIENCE
for _cached in (
    get_interview_settings,
    get_openai_voice_settings,
    get_jev_settings,
):
    _cached.cache_clear()


@pytest.fixture(scope="session")
def page_proof_signer() -> Signer:
    """The attestation signer this control plane issues page proofs with.

    Telemetry ingestion signs the page proof at document activation and the
    extension socket verifies the one the extension forwards, so a test signs
    with the same key the running application holds.
    """
    ingestion = get_ingestion_settings()
    return Signer(
        seed=ingestion.attestation_signing_seed(),
        key_id=ingestion.attestation_signing_key_id,
    )


@pytest_asyncio.fixture
async def running_api(settings: Settings) -> AsyncIterator[FastAPI]:
    """Run the production application on a real port for the duration of a test."""
    application = create_app(settings, PortRegistry(voice=None, observation=None, evidence=None))
    config = uvicorn.Config(
        application, host="127.0.0.1", port=SOCKET_PORT, log_level="warning"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(300):
        if server.started:
            break
        await asyncio.sleep(0.02)
    else:
        raise RuntimeError(f"the control plane did not start on port {SOCKET_PORT}")
    try:
        yield application
    finally:
        server.should_exit = True
        await task


def install_observation_readiness(
    application: FastAPI, observation: ObservationReadinessPort
) -> None:
    """Install the observation readiness adapter for one running application.

    `create_app` installs the ingestion-backed adapter over its own connector;
    a test that states readiness directly replaces that one entry and leaves the
    rest of the registry alone.
    """
    application.state.ports = replace(application.state.ports, observation=observation)
