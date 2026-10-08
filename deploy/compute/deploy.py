#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["python-dotenv>=1.1"]
# ///
"""Deploy the control plane to Supabase Compute.

    uv run deploy/compute/deploy.py secrets   # sync the runtime environment
    uv run deploy/compute/deploy.py push      # build and deploy api + worker

The API and the worker run as two computes in the project `.env.local` names
(`SUPABASE_PROJECT_REF`), both built from `apps/api/Dockerfile`:

- `secrets` writes the control plane's environment as project secrets, which
  Compute injects into every compute, removes any secret the environment no
  longer names (the `SUPABASE_` ones are the platform's own), then reads back
  each secret's digest and refuses a mismatch. Values come from `.env.local`,
  apart from the deployment policy below, which is decided in review rather than
  per invocation.
- `push` refuses a dirty tree, so a deploy always names committed bytes. It
  stages the files the Dockerfile copies (`git archive` of HEAD) into
  `supabase/compute/<name>/`, appends the worker's own CMD to its copy, runs
  `supabase compute push`, and checks the API's `/health` through the project's
  public URL.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Final

from dotenv import dotenv_values

REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
ENVIRONMENT_FILE: Final[Path] = REPOSITORY_ROOT / ".env.local"
STAGING_ROOT: Final[Path] = REPOSITORY_ROOT / "supabase" / "compute"
DOCKERFILE: Final[str] = "apps/api/Dockerfile"
COMPUTES: Final[tuple[str, ...]] = ("api", "worker")
WORKER_CMD: Final[str] = 'CMD ["utrace-worker"]'
API_BASE_URL: Final[str] = "https://api.utrace.dev/compute/v1/api"
API_SOCKET_URL: Final[str] = "wss://api.utrace.dev/compute/v1/api"
#: Secrets Compute injects itself; the environment never names them.
PLATFORM_SECRET_PREFIX: Final[str] = "SUPABASE_"
HEALTH_ATTEMPTS: Final[int] = 30
HEALTH_INTERVAL_SECONDS: Final[float] = 2.0

#: What the Dockerfile copies, archived from the committed tree.
BUILD_PATHS: Final[tuple[str, ...]] = (
    "apps/api/pyproject.toml",
    "apps/api/uv.lock",
    "apps/api/app",
    "packages/utrace-contracts/python/pyproject.toml",
    "packages/utrace-contracts/python/src",
    "packages/utrace-contracts/schemas",
)

#: Runtime variables whose values `.env.local` holds.
FROM_ENVIRONMENT_FILE: Final[tuple[str, ...]] = (
    "UTRACE_SUPABASE_SECRET_KEY",
    "UTRACE_SUPABASE_PUBLISHABLE_KEY",
    "UTRACE_ATTESTATION_SIGNING_KEY",
    "UTRACE_ATTESTATION_SIGNING_KEY_ID",
    "UTRACE_SERVER_MANIFEST_TRUST_ANCHOR",
    "UTRACE_SERVER_MANIFEST_TRUST_ANCHOR_KEY_ID",
    "UTRACE_LANE_SIGNING_KEY",
    "UTRACE_FRONT_DOOR_SERVICE_TOKEN",
    "UTRACE_DISCORD_BOT_TOKEN",
    "UTRACE_DISCORD_SERVER_ID",
    "UTRACE_DISCORD_FEATURE_REQUEST_CHANNEL_ID",
    "STRIPE_SANDBOX_SECRET_KEY",
    "OPENAI_API_KEY",
    "VERCEL_AI_GATEWAY_API_KEY",
    "UTRACE_JEV_MODEL_ID",
    "UTRACE_JEV_TIMEOUT_SECONDS",
    "UTRACE_INTAKE_DEBOUNCE_QUIET_SECONDS",
    "UTRACE_INTAKE_DEBOUNCE_MAX_SECONDS",
    "UTRACE_VERCEL_SANDBOX_TOKEN",
    "UTRACE_VERCEL_TEAM_ID",
    "UTRACE_VERCEL_PROJECT_ID",
    "UTRACE_GITHUB_APP_ID",
    "UTRACE_GITHUB_INSTALLATION_ID",
    "UTRACE_GITHUB_PRIVATE_KEY",
    "UTRACE_TELEMETRY_BUCKET",
)

#: Runtime variables renamed on the way: the computes reach Postgres directly,
#: inside Supabase's network, rather than through the session pooler local tools
#: use, so they are not bound by the pooler's client limit.
RENAMED_FROM_ENVIRONMENT_FILE: Final[tuple[tuple[str, str], ...]] = (
    ("UTRACE_DATABASE_URL", "UTRACE_DATABASE_DIRECT_URL"),
)

#: Deployment policy.
POLICY: Final[tuple[tuple[str, str], ...]] = (
    ("UTRACE_DB_POOL_MAX", "6"),
    ("UTRACE_WEB_ORIGIN", "https://utrace.dev"),
    ("UTRACE_PREVIEW_ORIGIN_SUFFIX", "preview.utrace.dev"),
    ("UTRACE_INGEST_WEBSOCKET_ORIGIN", API_SOCKET_URL),
    ("UTRACE_PREVIEW_SERVER_CONTROL_URL", API_SOCKET_URL),
    ("UTRACE_PREVIEW_SERVER_INGEST_URL", API_BASE_URL),
    ("UTRACE_PREVIEW_SERVER_SERVICE_NAME", "shopalytics_web"),
    ("UTRACE_PREVIEW_ROUTE_TTL_SECONDS", "3600"),
    ("UTRACE_PREVIEW_HANDOFF_CODE_TTL_SECONDS", "120"),
    ("UTRACE_PREVIEW_SESSION_CREDENTIAL_TTL_SECONDS", "43200"),
    ("UTRACE_PREVIEW_EXECUTION_TIME_LIMIT_SECONDS", "7200"),
    ("UTRACE_PREVIEW_READINESS_TIMEOUT_SECONDS", "20"),
    ("UTRACE_PREVIEW_PUBLIC_PATH_ENABLED", "1"),
    ("UTRACE_INTAKE_CONSUMER_MAX_ATTEMPTS", "5"),
    ("UTRACE_INTAKE_CONSUMER_BACKOFF_SECONDS", "2"),
    ("UTRACE_INTAKE_CONSUMER_HEALTHY_SECONDS", "120"),
    ("UTRACE_INTAKE_CONSUMER_SHUTDOWN_SECONDS", "10"),
)


class DeployError(RuntimeError):
    """A deploy step failed; the message says which and why."""


def main(argv: list[str]) -> int:
    """Run one subcommand: `secrets` or `push`."""
    if len(argv) != 2 or argv[1] not in {"secrets", "push"}:
        raise DeployError("usage: deploy.py secrets | deploy.py push")
    values = _environment_file()
    project_ref = _required(values, "SUPABASE_PROJECT_REF")
    if argv[1] == "secrets":
        _sync_secrets(project_ref=project_ref, runtime=_runtime_environment(values))
    else:
        _push(project_ref=project_ref)
    return 0


def _environment_file() -> dict[str, str]:
    """Return `.env.local`, refusing a variable that has no value."""
    if not ENVIRONMENT_FILE.is_file():
        raise DeployError(f"{ENVIRONMENT_FILE} does not exist")
    parsed = dotenv_values(ENVIRONMENT_FILE, interpolate=False)
    return {name: value for name, value in parsed.items() if value is not None}


def _required(values: dict[str, str], name: str) -> str:
    value = values.get(name, "")
    if not value.strip():
        raise DeployError(f"{ENVIRONMENT_FILE.name} has no value for {name}")
    return value


def _runtime_environment(values: dict[str, str]) -> dict[str, str]:
    """The control plane's runtime environment, named as the computes read it."""
    missing = [name for name in FROM_ENVIRONMENT_FILE if not values.get(name, "").strip()]
    missing += [
        source for _, source in RENAMED_FROM_ENVIRONMENT_FILE if not values.get(source, "").strip()
    ]
    if missing:
        raise DeployError(f"{ENVIRONMENT_FILE.name} has no value for: {', '.join(missing)}")
    runtime = {name: values[name] for name in FROM_ENVIRONMENT_FILE}
    runtime.update({target: values[source] for target, source in RENAMED_FROM_ENVIRONMENT_FILE})
    runtime.update(POLICY)
    return runtime


def _sync_secrets(*, project_ref: str, runtime: dict[str, str]) -> None:
    """Set every runtime variable as a project secret, then verify each digest."""
    with tempfile.TemporaryDirectory() as directory:
        env_file = Path(directory) / "compute.env"
        env_file.write_text(
            "".join(f"{name}={_quoted(value)}\n" for name, value in sorted(runtime.items())),
            encoding="utf-8",
        )
        env_file.chmod(0o600)
        _supabase("secrets", "set", "--env-file", str(env_file), "--project-ref", project_ref)
    stale = sorted(
        name
        for name in _secret_digests(project_ref)
        if name not in runtime and not name.startswith(PLATFORM_SECRET_PREFIX)
    )
    if stale:
        _supabase("secrets", "unset", *stale, "--project-ref", project_ref, "--yes")
        print(f"removed stale secrets: {', '.join(stale)}")
    digests = _secret_digests(project_ref)
    wrong = [
        name
        for name, value in runtime.items()
        if digests.get(name) != hashlib.sha256(value.encode("utf-8")).hexdigest()
    ]
    if wrong:
        raise DeployError(f"these secrets did not round-trip: {', '.join(sorted(wrong))}")
    print(f"{len(runtime)} secrets set and verified on {project_ref}")


def _secret_digests(project_ref: str) -> dict[str, str]:
    """Each project secret's name and the SHA-256 digest Supabase reports for its value."""
    listed = _supabase_json("secrets", "list", "--project-ref", project_ref, "--output", "json")
    if not isinstance(listed, list):
        raise DeployError("`supabase secrets list` did not return a list")
    return {str(item["name"]): str(item["value"]) for item in listed}


def _quoted(value: str) -> str:
    """Quote one value for the CLI's env-file parser, which unescapes these in double quotes."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _push(*, project_ref: str) -> None:
    """Stage both computes from HEAD, deploy them, and check the API's health."""
    if _git("status", "--porcelain").strip():
        raise DeployError("the working tree has uncommitted changes; commit them before deploying")
    revision = _git("rev-parse", "HEAD").strip()
    for name in COMPUTES:
        _stage(name=name, revision=revision)
    deployed = _supabase_json(
        "compute", "push", *COMPUTES, "--project-ref", project_ref, "--output", "json"
    )
    states = {
        str(item["compute_name"]): str(item["build_state"])
        for item in deployed["compute"]  # type: ignore[index]
    }
    if any(states.get(name) != "active" for name in COMPUTES):
        raise DeployError(f"compute builds did not all become active: {states}")
    _await_health(f"https://{project_ref}.supabase.co/compute/v1/api/health")
    print(f"deployed {revision[:8]} to {', '.join(COMPUTES)} on {project_ref}")


def _stage(*, name: str, revision: str) -> None:
    """Write one compute's build directory: the archived files and its Dockerfile."""
    target = STAGING_ROOT / name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", revision, "--", *BUILD_PATHS],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(target, filter="data")
    dockerfile = _git("show", f"{revision}:{DOCKERFILE}")
    dockerfile += f'\nLABEL org.opencontainers.image.revision="{revision}"\n'
    if name == "worker":
        dockerfile += f"{WORKER_CMD}\n"
    (target / "Dockerfile").write_text(dockerfile, encoding="utf-8")


def _await_health(url: str) -> None:
    """Wait for the deployed API to answer its health route."""
    last_error = ""
    for _ in range(HEALTH_ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                if response.status == 200:
                    return
                last_error = f"HTTP {response.status}"
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = str(error)
        time.sleep(HEALTH_INTERVAL_SECONDS)
    raise DeployError(f"{url} never answered 200: {last_error}")


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=REPOSITORY_ROOT, check=True, capture_output=True, text=True
    ).stdout


def _supabase(*arguments: str) -> str:
    """Run the repository's pinned Supabase CLI; its output never carries secret values."""
    result = subprocess.run(
        ["npm", "run", "-s", "supabase", "--", *arguments],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise DeployError(
            f"supabase {arguments[0]} {arguments[1]} failed ({result.returncode}): "
            f"{result.stdout[-2000:]}{result.stderr[-2000:]}"
        )
    return result.stdout


def _supabase_json(*arguments: str) -> object:
    """Run the CLI and parse the JSON document it prints last, after any progress lines."""
    output = _supabase(*arguments)
    decoder = json.JSONDecoder()
    offset = 0
    for line in output.splitlines(keepends=True):
        if line.startswith(("{", "[")):
            try:
                document, _ = decoder.raw_decode(output[offset:])
            except json.JSONDecodeError:
                pass
            else:
                return document
        offset += len(line)
    raise DeployError(f"supabase {' '.join(arguments[:2])} printed no JSON: {output[-500:]}")


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except DeployError as error:
        print(f"deploy failed: {error}", file=sys.stderr)
        sys.exit(1)
