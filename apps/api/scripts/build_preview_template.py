"""Build the Shopalytics preview runtime template and pin it for a client.

The template is a Vercel Container Registry image built from the fixture
repository's own Dockerfile at the recipe's pinned revision, and a Vercel Sandbox
snapshot taken from a fresh sandbox of that image. This is a workstation job: it
builds the image with Apple's ``container`` CLI for ``linux/amd64``, because the
Vercel Sandbox runs amd64 microVMs, and pushes it with the grant's project token.

Apple ``container``'s builder reads only the home directory, so the context is
staged under ``~/.cache/utrace/preview-template`` and removed afterwards.

Run it from the repository root::

    set -a; source .env.local; set +a
    uv run --project apps/api python apps/api/scripts/build_preview_template.py \\
      --client-id <uuid> --configuration-release-id <uuid> --warm-pool-target 1

Without ``--client-id`` it builds, snapshots, and verifies the template and prints
its identity without pinning anything. ``--image-reference`` with ``--snapshot-id``
reuses a template this script already built from the same recipe and fixture
commit: nothing is rebuilt, the snapshot is verified again, and it is pinned.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from collections.abc import Sequence
from logging import INFO
from pathlib import Path
from uuid import UUID

from app.connectors.vercel_sandboxes import SandboxGrant, sandbox_grant_from_environment
from app.db.pool import Database
from app.logging import configure_logging, get_logger
from app.preview.activation import observation_hosts
from app.preview.config import get_preview_settings, get_template_build_settings
from app.preview.recipe import StagedBuildContext, TemplateRecipe, stage_build_context
from app.preview.runtime import PreviewRuntimeProvider
from app.preview.template import pin_template, snapshot_template_image, verify_template_health
from app.settings import get_settings

_log = get_logger(__name__)

_REGISTRY = "vcr.vercel.com"
_PLATFORM = "linux/amd64"
_TAG_LENGTH = 16
_PUSH_ATTEMPTS = 4
_PUSH_BACKOFF_SECONDS = 15


def _container(arguments: Sequence[str], *, stdin: str | None) -> str:
    """Run one ``container`` command, raising with its output when it fails."""
    completed = subprocess.run(
        ["container", *arguments],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"container {arguments[0]} failed with exit code {completed.returncode}: "
            f"{(completed.stderr or completed.stdout).strip()[-4000:]}"
        )
    return completed.stdout


def _push(tagged: str) -> None:
    """Push the image, retrying a transient registry failure with a warning.

    A multi-hundred-megabyte push can lose one blob upload to a registry timeout
    (HTTP 408); a repeat push skips the blobs the registry already holds, so it
    resumes rather than restarts. The last failure is raised.
    """
    for attempt in range(1, _PUSH_ATTEMPTS + 1):
        try:
            _container(["image", "push", "--platform", _PLATFORM, tagged], stdin=None)
            return
        except RuntimeError as error:
            if attempt == _PUSH_ATTEMPTS:
                raise
            _log.warning(
                "preview_template_image_push_retrying",
                image=tagged,
                attempt=attempt,
                attempts=_PUSH_ATTEMPTS,
                error=str(error)[-500:],
            )
            time.sleep(_PUSH_BACKOFF_SECONDS * attempt)


def _build_and_push(
    *, grant: SandboxGrant, recipe: TemplateRecipe, staged: StagedBuildContext, namespace: str
) -> str:
    """Build and push the template image, and return its digest-pinned reference."""
    tag = staged.recipe_digest.removeprefix("sha256:")[:_TAG_LENGTH]
    repository = f"{namespace}/{recipe.image_repository}"
    tagged = f"{repository}:{tag}"
    _container(
        ["registry", "login", "--username", "oidc", "--password-stdin", _REGISTRY],
        stdin=grant.token,
    )
    build_arguments = [
        argument
        for name, value in sorted(recipe.dockerfile_build_args.items())
        for argument in ("--build-arg", f"{name}={value}")
    ]
    _log.info(
        "preview_template_image_building", image=tagged, fixture_commit=staged.fixture_commit
    )
    _container(
        [
            "build",
            "--platform",
            _PLATFORM,
            "--file",
            str(staged.dockerfile),
            *build_arguments,
            "--tag",
            tagged,
            str(staged.root),
        ],
        stdin=None,
    )
    _push(tagged)
    inspected = json.loads(_container(["image", "inspect", tagged], stdin=None))
    digest = str(inspected[0]["configuration"]["descriptor"]["digest"])
    _log.info("preview_template_image_pushed", image=tagged, digest=digest)
    return f"{repository}@{digest}"


def _parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", type=UUID)
    parser.add_argument("--configuration-release-id", type=UUID)
    parser.add_argument("--warm-pool-target", type=int)
    parser.add_argument(
        "--image-reference",
        help="Reuse this already-pushed template image (requires --snapshot-id).",
    )
    parser.add_argument(
        "--snapshot-id",
        help="Reuse this already-taken template snapshot (requires --image-reference).",
    )
    parser.add_argument(
        "--skip-verification",
        action="store_true",
        help="Do not create a runtime to prove the template reaches health.",
    )
    return parser.parse_args(argv)


async def _run(arguments: argparse.Namespace) -> int:
    preview = get_preview_settings()
    build = get_template_build_settings()
    grant = sandbox_grant_from_environment()
    recipe = TemplateRecipe.load(build.template_recipe_path)
    staging_root = Path.home() / ".cache" / "utrace" / "preview-template"

    with stage_build_context(
        recipe=recipe,
        fixture_repository=build.fixture_repository_path,
        staging_root=staging_root,
    ) as staged:
        if (arguments.image_reference is None) != (arguments.snapshot_id is None):
            raise ValueError("--image-reference and --snapshot-id are given together or not at all")
        async with PreviewRuntimeProvider(grant=grant) as provider:
            if arguments.image_reference is not None:
                image_reference = str(arguments.image_reference)
                snapshot_id = str(arguments.snapshot_id)
                if not await provider.snapshot_exists(snapshot_id=snapshot_id):
                    raise ValueError(f"template snapshot {snapshot_id} no longer exists")
            else:
                image_reference = _build_and_push(
                    grant=grant, recipe=recipe, staged=staged, namespace=build.image_namespace
                )
                snapshot_id = await snapshot_template_image(
                    provider=provider,
                    recipe=recipe,
                    image_reference=image_reference,
                    execution_time_limit_seconds=preview.execution_time_limit_seconds,
                )
            health: dict[str, str] = {}
            if not arguments.skip_verification:
                health = dict(
                    await verify_template_health(
                        provider=provider,
                        recipe=recipe,
                        snapshot_id=snapshot_id,
                        observation_hosts=observation_hosts(
                            control_url=preview.server_control_url,
                            ingest_url=preview.server_ingest_url,
                        ),
                        execution_time_limit_seconds=preview.execution_time_limit_seconds,
                    )
                )

        pinned: str | None = None
        if arguments.client_id is not None:
            if arguments.configuration_release_id is None or arguments.warm_pool_target is None:
                raise ValueError(
                    "--client-id requires --configuration-release-id and --warm-pool-target"
                )
            database = Database(dsn=get_settings().database_url, min_size=1, max_size=2)
            await database.open()
            try:
                template = await pin_template(
                    database,
                    client_id=arguments.client_id,
                    pinned_configuration_release_id=arguments.configuration_release_id,
                    recipe=recipe,
                    staged=staged,
                    image_reference=image_reference,
                    snapshot_id=snapshot_id,
                    warm_pool_target=arguments.warm_pool_target,
                )
                pinned = str(template.id)
            finally:
                await database.close()

        print(
            json.dumps(
                {
                    "image_reference": image_reference,
                    "snapshot_id": snapshot_id,
                    "recipe_digest": staged.recipe_digest,
                    "fixture_commit": staged.fixture_commit,
                    "seed_manifest_digest": staged.seed.manifest_digest,
                    "sdk_versions": dict(staged.sdk_versions),
                    "preview_runtime_template_id": pinned,
                    "verification": health,
                },
                indent=2,
            )
        )
    return 0


def main() -> int:
    """Entry point."""
    configure_logging(INFO)
    return asyncio.run(_run(_parse_arguments(sys.argv[1:])))


if __name__ == "__main__":
    raise SystemExit(main())
