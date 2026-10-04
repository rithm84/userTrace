"""Build the implementation workspace template snapshot.

The workspace is Vercel's default sandbox image (a non-root user, Node 24, Python,
and git) plus the pinned Codex CLI under `/opt/utrace/codex` and Codex's own model
catalog for that release, with GPT-6 Astra's `ultrafast` service tier added: Codex
sends a tier only when the model's catalog entry advertises it. The snapshot is
client-independent: the repository is cloned at attempt time, and nothing here holds
a credential. It is taken with no expiry, and its ID is what a client's
implementation policy pins as `workspace_snapshot_id`.

Run it from the repository root::

    set -a; source .env.local; set +a
    uv run --project apps/api python apps/api/scripts/build_workspace_snapshot.py
"""

from __future__ import annotations

import asyncio
import json
from logging import INFO
from typing import Final

import httpx

from app.connectors.openai_models import GPT_6_ASTRA, OpenAIServiceTier
from app.connectors.vercel_sandboxes import (
    EgressPolicy,
    VercelSandboxes,
    sandbox_grant_from_environment,
)
from app.ids import uuid7
from app.implementation.environment import (
    CODEX_BINARY,
    CODEX_MODEL_CATALOG,
    CODEX_ROOT,
    CODEX_VERSION,
    REGISTRY_DOMAINS,
)
from app.logging import configure_logging, get_logger

_log = get_logger(__name__)

_BUILD_EGRESS = EgressPolicy(allowed_domains=REGISTRY_DOMAINS, injections=())
"""The build installs from the npm registry and reaches nothing else."""

_CATALOG_URL: Final[str] = (
    f"https://raw.githubusercontent.com/openai/codex/rust-v{CODEX_VERSION}"
    "/codex-rs/models-manager/models.json"
)
_ULTRAFAST_TIER: Final[dict[str, str]] = {
    "id": OpenAIServiceTier.ULTRAFAST.value,
    "name": "Ultrafast",
    "description": "The fastest API service tier",
}

_SETUP = (
    "set -euo pipefail\n"
    f"mkdir -p {CODEX_ROOT}\n"
    f"npm install --prefix {CODEX_ROOT} --no-audit --no-fund '@openai/codex@{CODEX_VERSION}'\n"
    # The download cache would double the snapshot's Codex bytes, and a larger
    # snapshot boots several times slower.
    "npm cache clean --force\n"
    "chown -R $(stat -c %U /vercel/sandbox):$(stat -c %G /vercel/sandbox) /opt/utrace\n"
    "git config --system user.name 'uTrace implementation workspace'\n"
    "git config --system user.email 'implementation@utrace.invalid'\n"
    "git config --system --add safe.directory '*'\n"
)
_VERIFY = (
    "set -euo pipefail\n"
    "whoami; git --version; node --version; npm --version\n"
    f"{CODEX_BINARY} --version\n"
    f"python3 -c 'import json; json.load(open(\"{CODEX_MODEL_CATALOG}\"))'\n"
)


def _model_catalog() -> bytes:
    """Codex's catalog for the pinned release, with Astra's `ultrafast` tier added."""
    response = httpx.get(_CATALOG_URL, timeout=60.0)
    response.raise_for_status()
    catalog = response.json()
    patched = 0
    for model in catalog["models"]:
        if model["slug"] != GPT_6_ASTRA:
            continue
        tiers = model.setdefault("service_tiers", [])
        if all(tier["id"] != _ULTRAFAST_TIER["id"] for tier in tiers):
            tiers.append(_ULTRAFAST_TIER)
        patched += 1
    if patched != 1:
        raise RuntimeError(
            f"Codex {CODEX_VERSION}'s catalog at {_CATALOG_URL} lists {patched} "
            f"{GPT_6_ASTRA} entries, not one"
        )
    return json.dumps(catalog).encode("utf-8")


async def _run() -> int:
    async with VercelSandboxes(grant=sandbox_grant_from_environment()) as sandboxes:
        handle = await sandboxes.create_from_default_image(
            name=f"utrace-workspace-template-{uuid7().hex}",
            env={},
            egress=_BUILD_EGRESS,
            execution_time_limit_seconds=1800,
            vcpus=2,
        )
        try:
            setup = await sandboxes.run(
                handle, script=_SETUP, cwd="/", timeout_seconds=900, sudo=True
            )
            if setup.exit_code != 0:
                raise RuntimeError(f"workspace setup exited {setup.exit_code}: {setup.stdout[-3000:]}")
            await sandboxes.write_file(handle, path=CODEX_MODEL_CATALOG, content=_model_catalog())
            verified = await sandboxes.run(
                handle, script=_VERIFY, cwd="/", timeout_seconds=120, sudo=False
            )
            if verified.exit_code != 0:
                raise RuntimeError(f"workspace verification failed: {verified.stdout[-3000:]}")
            snapshot_id = await sandboxes.snapshot(handle)
        finally:
            await sandboxes.delete(handle)
    print(json.dumps({"workspace_snapshot_id": snapshot_id, "toolchain": verified.stdout.split()}, indent=2))
    return 0


def main() -> int:
    """Entry point."""
    configure_logging(INFO)
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
