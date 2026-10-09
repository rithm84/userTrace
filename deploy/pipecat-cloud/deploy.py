#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["httpx>=0.27", "python-dotenv>=1.1"]
# ///
"""Deploy the voice agent to Pipecat Cloud.

    uv run deploy/pipecat-cloud/deploy.py secrets   # sync the agent's secret set
    uv run deploy/pipecat-cloud/deploy.py push      # build and deploy the agent

Both authenticate with `PIPECAT_CLOUD_PRIVATE_API_KEY` from `.env.local` against
the private API (https://docs.pipecat.ai/api-reference/pipecat-cloud/rest-reference/overview).

- `secrets` writes the agent's runtime environment as the secret set its sessions
  read: the voice providers' keys, the control plane settings the agent's code
  reads, and the database reached through the session pooler, since Pipecat Cloud
  is outside Supabase's network. Values come from `.env.local`, apart from the
  deployment policy below.
- `push` refuses a dirty tree, so a deploy always names committed bytes. It
  archives the files the Dockerfile copies from HEAD, has Pipecat Cloud build the
  image, and points the agent at the build with one warm instance, so no session
  waits for a cold start. It returns once the agent's new deployment is ready.
"""

from __future__ import annotations

import gzip
import io
import subprocess
import sys
import time
from pathlib import Path
from typing import Final

import httpx
from dotenv import dotenv_values

REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
ENVIRONMENT_FILE: Final[Path] = REPOSITORY_ROOT / ".env.local"
API_URL: Final[str] = "https://api.pipecat.daily.co/v1"
AGENT_NAME: Final[str] = "utrace-voice-agent"
SECRET_SET: Final[str] = "utrace-voice-agent-secrets"
#: Beside the Supabase project (us-west-2) and the Daily rooms' media servers.
REGION: Final[str] = "us-west"
DOCKERFILE: Final[str] = "deploy/pipecat-cloud/Dockerfile"
#: One vCPU and 2 GB, so a call's audio never waits on the processor.
AGENT_PROFILE: Final[str] = "agent-2x"
#: A warm agent answers a call at once; a cold start takes ten seconds or more.
MIN_AGENTS: Final[int] = 1
MAX_AGENTS: Final[int] = 10
#: The voice agent Job's own timeout and the Daily room's lifetime.
MAX_SESSION_SECONDS: Final[int] = 7200
BUILD_POLL_SECONDS: Final[float] = 5.0
BUILD_TIMEOUT_SECONDS: Final[float] = 1800.0
READY_POLL_SECONDS: Final[float] = 5.0
READY_TIMEOUT_SECONDS: Final[float] = 900.0

#: What the Dockerfile copies, archived from the committed tree.
BUILD_PATHS: Final[tuple[str, ...]] = (
    "apps/api/pyproject.toml",
    "apps/api/uv.lock",
    "apps/api/app",
    "packages/utrace-contracts/python/pyproject.toml",
    "packages/utrace-contracts/python/src",
    "packages/utrace-contracts/schemas",
    "deploy/pipecat-cloud/Dockerfile",
    "deploy/pipecat-cloud/bot.py",
)

#: Runtime variables whose values `.env.local` holds.
FROM_ENVIRONMENT_FILE: Final[tuple[str, ...]] = (
    "UTRACE_DATABASE_URL",
    "SUPABASE_URL",
    "UTRACE_SUPABASE_SECRET_KEY",
    "UTRACE_SUPABASE_PUBLISHABLE_KEY",
    "UTRACE_LANE_SIGNING_KEY",
    "UTRACE_FRONT_DOOR_SERVICE_TOKEN",
    "DAILY_API_KEY",
    "DEEPGRAM_API_KEY",
    "OPENAI_API_KEY",
    "ELEVENLABS_API_KEY",
    "VERCEL_AI_GATEWAY_API_KEY",
    "UTRACE_JEV_MODEL_ID",
    "UTRACE_JEV_TIMEOUT_SECONDS",
)

#: Deployment policy.
POLICY: Final[tuple[tuple[str, str], ...]] = (
    ("UTRACE_VOICE_AGENT_RUNTIME", "pipecat_cloud"),
    ("UTRACE_PREVIEW_ORIGIN_SUFFIX", "preview.utrace.dev"),
    ("UTRACE_DB_POOL_MAX", "4"),
)


class DeployError(RuntimeError):
    """A deploy step failed; the message names the step and Pipecat Cloud's answer."""


def main(argv: list[str]) -> int:
    """Run one subcommand: `secrets` or `push`."""
    if len(argv) != 2 or argv[1] not in {"secrets", "push"}:
        raise DeployError("usage: deploy.py secrets | deploy.py push")
    values = _environment_file()
    with httpx.Client(
        base_url=API_URL,
        headers={"Authorization": f"Bearer {_required(values, 'PIPECAT_CLOUD_PRIVATE_API_KEY')}"},
        timeout=60.0,
    ) as client:
        if argv[1] == "secrets":
            _sync_secrets(client, runtime=_runtime_environment(values))
        else:
            _push(client)
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
    """The agent's runtime environment."""
    runtime = {name: _required(values, name) for name in FROM_ENVIRONMENT_FILE}
    runtime.update(POLICY)
    return runtime


def _checked(response: httpx.Response, step: str) -> httpx.Response:
    if response.status_code >= 400:
        raise DeployError(
            f"{step} failed: {response.request.method} {response.request.url} "
            f"answered {response.status_code}: {response.text[:2000]}"
        )
    return response


def _sync_secrets(client: httpx.Client, *, runtime: dict[str, str]) -> None:
    """Write every runtime variable into the agent's secret set."""
    _checked(
        client.put(
            f"/secrets/{SECRET_SET}",
            json={
                "secrets": [{"secretKey": key, "secretValue": value} for key, value in runtime.items()],
                "region": REGION,
            },
        ),
        f"writing {SECRET_SET}",
    )
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while True:
        sets = _checked(client.get("/secrets"), "listing secret sets").json()["sets"]
        status = next((s["status"] for s in sets if s["name"] == SECRET_SET), None)
        if status == "ready":
            break
        if time.monotonic() > deadline:
            raise DeployError(f"{SECRET_SET} is {status}, not ready, after {READY_TIMEOUT_SECONDS}s")
        time.sleep(READY_POLL_SECONDS)
    print(f"{len(runtime)} secrets set in {SECRET_SET} ({REGION})")


def _push(client: httpx.Client) -> None:
    """Build the committed tree in the cloud and deploy the agent onto the build."""
    revision = _clean_revision()
    build_id = _build(client, context=_context(revision))
    current = client.get(f"/agents/{AGENT_NAME}")
    settings = {
        "buildId": build_id,
        "secretSet": SECRET_SET,
        "autoScaling": {"minAgents": MIN_AGENTS, "maxAgents": MAX_AGENTS},
        "agentProfile": AGENT_PROFILE,
        "maxSessionDuration": MAX_SESSION_SECONDS,
    }
    if current.status_code == 404:
        deployed = client.post(
            "/agents", json={"serviceName": AGENT_NAME, "region": REGION, **settings}
        )
    else:
        _checked(current, f"reading agent {AGENT_NAME}")
        deployed = client.post(f"/agents/{AGENT_NAME}", json=settings)
    desired = _checked(deployed, f"deploying {AGENT_NAME}").json()["desiredDeploymentId"]
    _wait_ready(client, desired_deployment_id=desired)
    print(f"deployed {revision[:8]} to {AGENT_NAME} ({REGION}, build {build_id})")


def _clean_revision() -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPOSITORY_ROOT, check=True, capture_output=True, text=True,
    ).stdout
    if status.strip():
        raise DeployError("refusing to deploy a dirty tree; commit or stash first")
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _context(revision: str) -> bytes:
    """The build context: the Dockerfile's inputs at `revision`, as a gzipped tar."""
    tar = subprocess.run(
        ["git", "archive", "--format=tar", revision, "--", *BUILD_PATHS],
        cwd=REPOSITORY_ROOT, check=True, capture_output=True,
    ).stdout
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as archive:
        archive.write(tar)
    return buffer.getvalue()


def _build(client: httpx.Client, *, context: bytes) -> str:
    """Upload the context, build it, and return the successful build's id."""
    upload = _checked(
        client.post("/builds/upload-url", json={"region": REGION}), "requesting a build upload URL"
    ).json()
    fields = {**upload["uploadFields"]}
    fields.setdefault("Content-Type", "application/gzip")
    _checked(
        httpx.post(
            upload["uploadUrl"],
            data=fields,
            files={"file": ("context.tar.gz", context, "application/gzip")},
            timeout=300.0,
        ),
        "uploading the build context",
    )
    build = _checked(
        client.post(
            "/builds",
            json={"uploadId": upload["uploadId"], "region": REGION, "dockerfilePath": DOCKERFILE},
        ),
        "starting the build",
    ).json()
    build_id = str(build.get("id") or build["build"]["id"])
    deadline = time.monotonic() + BUILD_TIMEOUT_SECONDS
    while True:
        state = _checked(client.get(f"/builds/{build_id}"), "reading the build").json()["build"]
        if state["status"] == "success":
            print(f"built {build_id} in {state.get('buildDurationSeconds')}s")
            return build_id
        if state["status"] in {"failed", "timeout"}:
            raise DeployError(
                f"build {build_id} ended {state['status']}: {state.get('errorMessage')}; "
                f"logs: {state.get('logsUrl')}"
            )
        if time.monotonic() > deadline:
            raise DeployError(f"build {build_id} is still {state['status']} after {BUILD_TIMEOUT_SECONDS}s")
        time.sleep(BUILD_POLL_SECONDS)


def _wait_ready(client: httpx.Client, *, desired_deployment_id: str) -> None:
    """Wait for the agent's new deployment to be reconciled and ready for sessions."""
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while True:
        agent = _checked(client.get(f"/agents/{AGENT_NAME}"), "reading the agent").json()
        if agent.get("reconciledDeploymentId") == desired_deployment_id and agent.get("ready"):
            return
        if time.monotonic() > deadline:
            raise DeployError(
                f"{AGENT_NAME} is not ready on deployment {desired_deployment_id} after "
                f"{READY_TIMEOUT_SECONDS}s: {agent.get('currentRevision')}"
            )
        time.sleep(READY_POLL_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except DeployError as error:
        print(f"deploy failed: {error}", file=sys.stderr)
        sys.exit(1)
