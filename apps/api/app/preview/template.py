"""Pinning one client's preview runtime template, and the policy runtimes run under.

A client configuration pins exactly one template: the Vercel Container Registry
image built from the fixture repository, pinned by digest; the Vercel Sandbox
snapshot taken from a sandbox of that image, created without an expiry; the seed
manifest it carries; the fixture clock; the application port; and the SDK versions
installed in it. Every allocation copies those pins onto itself, so a Preview
Version's evidence can name the exact runtime it ran on.

Building the image is a workstation job owned by
``scripts/build_preview_template.py``; this module owns what happens once the image
is in the registry.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Mapping, Sequence
from uuid import UUID

from psycopg.types.json import Jsonb

from app.connectors.failures import ConnectorFailure
from app.connectors.vercel_sandboxes import EgressPolicy
from app.db.pool import Database
from app.db.rows import as_int, as_str, as_uuid
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.ids import uuid7
from app.logging import get_logger
from app.preview.recipe import StagedBuildContext, TemplateRecipe
from app.preview.records import RuntimeTemplate, SeedIdentity, SeedTarget
from app.preview.runtime import PreviewRuntimeProvider

__all__ = [
    "build_runtime_env",
    "candidate_build_network_policy",
    "load_current_template",
    "load_recipe",
    "mint_runtime_ref",
    "pin_template",
    "runtime_network_policy",
    "snapshot_template_image",
    "verify_template_health",
]

_log = get_logger(__name__)

_HEALTH_ATTEMPTS = 60
_IMAGE_NOT_READY = "image_not_ready"
_IMAGE_READY_ATTEMPTS = 20
_IMAGE_READY_WAIT_SECONDS = 30

#: The package registry a candidate runtime resolves its build recipe against.
_BUILD_REGISTRY_DOMAINS = ("registry.npmjs.org", "*.npmjs.org")


def runtime_network_policy(*, observation_hosts: Sequence[str]) -> EgressPolicy:
    """Return the outbound wall a prepared Shopalytics runtime runs behind.

    The product serves itself and its own local Postgres and has no outbound
    work of its own, so the only destinations on the list are the control plane
    the Server SDK reports to and the ingestion edge it exports to. An
    application adapter that tried to reach a real external effect still cannot.
    """
    return EgressPolicy(allowed_domains=tuple(observation_hosts), injections=())


def candidate_build_network_policy(*, observation_hosts: Sequence[str]) -> EgressPolicy:
    """Return the wall a candidate runs behind: its registry, and nothing else new.

    A candidate builds the approved patch inside itself, so it additionally
    needs the package registry the build recipe resolves against.
    """
    return EgressPolicy(
        allowed_domains=(*observation_hosts, *_BUILD_REGISTRY_DOMAINS), injections=()
    )


def mint_runtime_ref(*, purpose: str) -> str:
    """Return a fresh sandbox name for one runtime.

    The name is the runtime's durable reference and its reconciliation key: a
    create whose response was lost is resolved by looking the name up.
    """
    return f"utrace-{purpose}-{uuid7().hex}"


def build_runtime_env(
    *,
    seed_target: SeedTarget,
    handoff_secret: str,
    route_context_signing_secret: str,
) -> dict[str, str]:
    """Build the environment one preview sandbox is created with.

    The Server SDK's activation is not here. Its credential binds an Interview
    Session and a public preview origin, and a sandbox is created before either
    exists, so ``app.preview.activation`` installs it when the allocation is
    claimed. Until then the packaged SDK is dormant by its own contract and the
    ``server_sdk_activation`` probe reports that rather than inventing one.
    """
    return {
        "SHOPALYTICS_SEED_TARGET": seed_target.value,
        "UTRACE_PROFILE": "preview",
        "UTRACE_PREVIEW_HANDOFF_SECRET": handoff_secret,
        "UTRACE_PREVIEW_ROUTE_SIGNING_SECRET": route_context_signing_secret,
    }


async def snapshot_template_image(
    *,
    provider: PreviewRuntimeProvider,
    recipe: TemplateRecipe,
    image_reference: str,
    execution_time_limit_seconds: int,
) -> str:
    """Take the template snapshot from a fresh sandbox of the pinned image.

    The image already carries the built product and the seeded dataset, so the
    snapshot is taken before anything runs in it. The provider stops the sandbox
    once the snapshot completes, and the sandbox is then deleted.

    The registry processes a freshly pushed image before a sandbox can use it,
    and answers `image_not_ready` until then; that one answer is waited out with
    a warning, and it is raised once the wait is exhausted.
    """
    for attempt in range(1, _IMAGE_READY_ATTEMPTS + 1):
        try:
            snapshot_id = await provider.snapshot_image(
                runtime_ref=mint_runtime_ref(purpose="template"),
                image_reference=image_reference,
                application_port=recipe.application_port,
                egress=EgressPolicy(allowed_domains=(), injections=()),
                execution_time_limit_seconds=execution_time_limit_seconds,
                vcpus=recipe.vcpus,
            )
            break
        except ConnectorFailure as failure:
            if (
                failure.detail.provider_error_code != _IMAGE_NOT_READY
                or attempt == _IMAGE_READY_ATTEMPTS
            ):
                raise
            _log.warning(
                "preview_template_image_not_ready",
                image_reference=image_reference,
                attempt=attempt,
                attempts=_IMAGE_READY_ATTEMPTS,
            )
            await asyncio.sleep(_IMAGE_READY_WAIT_SECONDS)
    _log.info(
        "preview_template_snapshotted",
        image_reference=image_reference,
        snapshot_id=snapshot_id,
    )
    return snapshot_id


async def verify_template_health(
    *,
    provider: PreviewRuntimeProvider,
    recipe: TemplateRecipe,
    snapshot_id: str,
    observation_hosts: Sequence[str],
    execution_time_limit_seconds: int,
) -> Mapping[str, str]:
    """Create one runtime from the snapshot, prove health, and delete it.

    This is the build's own acceptance check: a template that cannot start the
    product and report its dataset identity is not a template, and the runtime is
    destroyed either way so the build leaves nothing behind.
    """
    handle = await provider.create_runtime(
        runtime_ref=mint_runtime_ref(purpose="template-check"),
        snapshot_id=snapshot_id,
        application_port=recipe.application_port,
        env_vars=build_runtime_env(
            seed_target=SeedTarget.BASE,
            handoff_secret=secrets.token_urlsafe(32),
            route_context_signing_secret=secrets.token_urlsafe(32),
        ),
        egress=runtime_network_policy(observation_hosts=observation_hosts),
        execution_time_limit_seconds=execution_time_limit_seconds,
        vcpus=recipe.vcpus,
        supervisor_command=recipe.supervisor_command,
        application_directory=recipe.application_directory,
    )
    try:
        probe = (
            f"for attempt in $(seq 1 {_HEALTH_ATTEMPTS}); do "
            f"curl -fs http://127.0.0.1:{recipe.application_port}{recipe.health_path} "
            "&& exit 0; sleep 2; done; exit 1"
        )
        result = await provider.execute(
            handle=handle,
            script=probe,
            working_directory=recipe.application_directory,
            timeout_seconds=_HEALTH_ATTEMPTS * 3,
        )
        if result.exit_code != 0:
            raise GuardRejectedError(
                machine="preview_runtime_template",
                trigger="verify_template_health",
                reason=(
                    f"a runtime from {snapshot_id} did not answer "
                    f"{recipe.health_path} with 200 within "
                    f"{_HEALTH_ATTEMPTS * 2} seconds: {result.stdout[-2000:]}"
                ),
            )
        return {"health_body": result.stdout.strip()[-4000:]}
    finally:
        await provider.delete_runtime(handle=handle)


async def pin_template(
    database: Database,
    *,
    client_id: UUID,
    pinned_configuration_release_id: UUID,
    recipe: TemplateRecipe,
    staged: StagedBuildContext,
    image_reference: str,
    snapshot_id: str,
    warm_pool_target: int,
) -> RuntimeTemplate:
    """Record the pinned image and snapshot as this client's current runtime template.

    Pinning retires the previous template in the same transaction. Allocations
    already running keep their own copied pins, so a retired template never
    rewrites the identity of a runtime that is carrying a session.
    """
    template_id = uuid7()
    sdk_versions = dict(staged.sdk_versions)
    environment = staged.seed_manifest.get("environment_template")
    if isinstance(environment, dict):
        sdk_versions["node"] = str(environment.get("node_major_version"))
        sdk_versions["postgres"] = str(environment.get("postgres_major_version"))

    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_runtime_templates
            set retired_at = now(), updated_at = now()
            where client_id = %s and retired_at is null
            """,
            (client_id,),
        )
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
                pinned_configuration_release_id,
                recipe.template_id,
                recipe.template_revision,
                snapshot_id,
                image_reference,
                staged.recipe_digest,
                staged.seed.manifest_digest,
                Jsonb(
                    {
                        "seed": staged.seed.model_dump(mode="json"),
                        "recipe": recipe.model_dump(mode="json"),
                        "fixture_commit": staged.fixture_commit,
                    }
                ),
                staged.seed.fixture_clock,
                recipe.application_port,
                staged.seed.authenticated_identity,
                Jsonb(sdk_versions),
                warm_pool_target,
            ),
        )
    _log.info(
        "preview_template_pinned",
        client_id=str(client_id),
        template_id=str(template_id),
        snapshot_id=snapshot_id,
        image_reference=image_reference,
        recipe_digest=staged.recipe_digest,
    )
    return await load_current_template(database, client_id=client_id)


async def load_current_template(
    database: Database, *, client_id: UUID
) -> RuntimeTemplate:
    """Return the client's current pinned runtime template."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, client_id, pinned_configuration_release_id, template_id,
                   template_revision, snapshot_id, image_digest, recipe_digest,
                   seed_manifest_digest, seed_manifest, fixture_clock,
                   application_port, authenticated_identity, sdk_versions,
                   warm_pool_target
            from public.preview_runtime_templates
            where client_id = %s and retired_at is null
            """,
            (client_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="preview_runtime_templates", row_id=client_id
        )
    manifest = row["seed_manifest"]
    if not isinstance(manifest, dict):
        raise TypeError("preview_runtime_templates.seed_manifest is not an object")
    recipe = TemplateRecipe.model_validate(manifest["recipe"])
    return RuntimeTemplate(
        id=as_uuid(row, "id"),
        client_id=as_uuid(row, "client_id"),
        pinned_configuration_release_id=as_uuid(
            row, "pinned_configuration_release_id"
        ),
        template_id=as_str(row, "template_id"),
        template_revision=as_int(row, "template_revision"),
        snapshot_id=as_str(row, "snapshot_id"),
        image_digest=as_str(row, "image_digest"),
        recipe_digest=as_str(row, "recipe_digest"),
        seed=SeedIdentity.model_validate(manifest["seed"]),
        application_port=as_int(row, "application_port"),
        sdk_versions=_string_map(row["sdk_versions"]),
        warm_pool_target=as_int(row, "warm_pool_target"),
        gating_probes=recipe.gating_probes,
    )


async def load_recipe(database: Database, *, template_id: UUID) -> TemplateRecipe:
    """Return the recipe the named template was pinned with."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select seed_manifest from public.preview_runtime_templates where id = %s",
            (template_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="preview_runtime_templates", row_id=template_id
        )
    manifest = row["seed_manifest"]
    if not isinstance(manifest, dict):
        raise TypeError("preview_runtime_templates.seed_manifest is not an object")
    return TemplateRecipe.model_validate(manifest["recipe"])


def _string_map(value: object) -> dict[str, str]:
    """Narrow a jsonb column into a string map, refusing anything else."""
    if not isinstance(value, dict):
        raise TypeError("expected a JSON object of strings")
    return {str(key): str(item) for key, item in value.items()}
