"""Candidate deployment: one Preview Version, one fresh runtime, one advance.

An accepted candidate is installed in a fresh runtime created from the same
pinned template the base allocation came from. Its files are installed and built
before the runtime first starts, so it starts once, on the candidate build. That
build may already have run while the workspace checked the same files
(`build_candidate_runtime`): the deployment adopts it only when every file digest
matches the artifact, and builds afresh otherwise. Its dataset is installed
independently with the same approved seed identity when it starts, its migrations
run only against its own database, and the base allocation is never mutated.

The session origin advances only after the candidate has passed every gating
probe. The probes run against the exact claims the next route revision will
carry, so the advance itself is one committed transaction rather than a window in
which an unproven candidate is already public.
"""

from __future__ import annotations

import asyncio
import hashlib
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from uuid import UUID

import httpx

from app.db.pool import Database
from app.errors import GuardRejectedError
from app.logging import get_logger
from app.preview.activation import ServerActivationContext, install_server_activation
from app.preview.artifacts import (
    fail_preview_version,
    load_artifact,
    publish_preview_version,
    reopen_for_rebuild,
)
from app.preview.config import PreviewSettings
from app.preview.machines import PreviewRuntimeAllocationFacts
from app.preview.pool import (
    commit_allocation_transition,
    insert_allocation,
    load_pins,
    mint_secret,
)
from app.preview.ports import ObservationReadinessPort
from app.preview.provisioning import wait_for_health
from app.preview.readiness import record_readiness, run_session_readiness
from app.preview.recipe import TemplateRecipe
from app.preview.records import (
    CandidateArtifact,
    CandidateFile,
    PoolRole,
    PreviewRouteRecord,
    ReadinessReport,
    RuntimeTemplate,
    SeedTarget,
)
from app.preview.routing import advance_route, draft_route_record, route_for_session
from app.preview.runtime import PreviewRuntimeHandle, PreviewRuntimeProvider
from app.preview.seeding import verify_dataset
from app.preview.teardown import tear_down_allocation
from app.preview.template import (
    build_runtime_env,
    candidate_build_network_policy,
    mint_runtime_ref,
)

__all__ = [
    "BuiltCandidateRuntime",
    "DeployedCandidate",
    "build_candidate_runtime",
    "deploy_candidate",
    "expire_idle_candidates",
    "rebuild_candidate",
]

_log = get_logger(__name__)

_BUILD_TIMEOUT_SECONDS = 1800
_BUILD_WINDOW_SECONDS = 1200
"""A built runtime's execution limit until a deployment adopts it and extends it.

A runtime built ahead of its checks has no allocation row yet, so a worker that
dies before adopting it leaves it to stop at this limit."""
_MIGRATION_TIMEOUT_SECONDS = 600
_DIRECTORY_TIMEOUT_SECONDS = 60


@dataclass(frozen=True, slots=True)
class BuiltCandidateRuntime:
    """A candidate runtime with its files installed and built, never started or routed."""

    handle: PreviewRuntimeHandle
    handoff_secret: str
    route_secret: str
    file_digests: Mapping[str, str]
    build_commands: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeployedCandidate:
    """What one candidate deployment produced."""

    preview_version_id: UUID
    preview_runtime_allocation_id: UUID
    route: PreviewRouteRecord
    report: ReadinessReport


async def _install_files(
    *,
    provider: PreviewRuntimeProvider,
    handle: PreviewRuntimeHandle,
    recipe: TemplateRecipe,
    files: Sequence[CandidateFile],
) -> None:
    """Write the candidate's files and prove the installed bytes match their digests."""
    directories = sorted(
        {
            str(PurePosixPath(candidate.path).parent)
            for candidate in files
            if str(PurePosixPath(candidate.path).parent) != "."
        }
    )
    if directories:
        created = await provider.execute(
            handle=handle,
            script="mkdir -p " + " ".join(shlex.quote(name) for name in directories),
            working_directory=recipe.application_directory,
            timeout_seconds=_DIRECTORY_TIMEOUT_SECONDS,
        )
        if created.exit_code != 0:
            raise GuardRejectedError(
                machine="preview_version",
                trigger="install_files",
                reason=(
                    "the candidate runtime refused to create the patch's directories: "
                    f"{created.stdout[-500:]}"
                ),
            )
    # Each file is its own provider call, so the writes go out together and the
    # read-backs after them; the digest check is the same for every file.
    targets = [
        f"{recipe.application_directory.rstrip('/')}/{candidate.path}" for candidate in files
    ]
    await asyncio.gather(
        *(
            provider.write_file(handle=handle, path=target, content=candidate.content.encode("utf-8"))
            for candidate, target in zip(files, targets, strict=True)
        )
    )
    installed_files = await asyncio.gather(
        *(provider.read_file(handle=handle, path=target) for target in targets)
    )
    for candidate, installed in zip(files, installed_files, strict=True):
        digest = f"sha256:{hashlib.sha256(installed).hexdigest()}"
        if digest != candidate.digest:
            raise GuardRejectedError(
                machine="preview_version",
                trigger="install_files",
                reason=(
                    f"{candidate.path} installed as {digest}, not the approved "
                    f"{candidate.digest}"
                ),
            )


async def _run_build_recipe(
    *,
    provider: PreviewRuntimeProvider,
    handle: PreviewRuntimeHandle,
    recipe: TemplateRecipe,
    commands: Sequence[str],
) -> None:
    """Run the candidate's build commands inside its runtime."""
    for command in commands:
        result = await provider.execute(
            handle=handle,
            script=command,
            working_directory=recipe.application_directory,
            timeout_seconds=_BUILD_TIMEOUT_SECONDS,
        )
        if result.exit_code != 0:
            raise GuardRejectedError(
                machine="preview_version",
                trigger="build_candidate",
                reason=(
                    f"build command {command!r} failed with exit {result.exit_code}: "
                    f"{result.stdout[-2000:]}"
                ),
            )


async def _run_migrations(
    *,
    provider: PreviewRuntimeProvider,
    handle: PreviewRuntimeHandle,
    recipe: TemplateRecipe,
    artifact: CandidateArtifact,
) -> None:
    """Apply candidate-only migrations to the candidate's own database.

    The candidate's Postgres runs inside its own sandbox, so "only against the
    candidate database" is a property of the topology, not of a connection string
    somebody remembered to change.
    """
    for migration in artifact.migrations:
        result = await provider.execute(
            handle=handle,
            script=(
                'psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$POSTGRES_USER" '
                '-d "$POSTGRES_DB" '
                '-c "set search_path to \\"$SHOPALYTICS_DATABASE_SCHEMA\\"" '
                f"-f {shlex.quote(migration)}"
            ),
            working_directory=recipe.application_directory,
            timeout_seconds=_MIGRATION_TIMEOUT_SECONDS,
        )
        if result.exit_code != 0:
            raise GuardRejectedError(
                machine="preview_version",
                trigger="run_migrations",
                reason=(
                    f"migration {migration!r} failed with exit {result.exit_code}: "
                    f"{result.stdout[-2000:]}"
                ),
            )


async def build_candidate_runtime(
    *,
    provider: PreviewRuntimeProvider,
    template: RuntimeTemplate,
    recipe: TemplateRecipe,
    files: Sequence[CandidateFile],
    build_commands: Sequence[str],
    activation: ServerActivationContext,
) -> BuiltCandidateRuntime:
    """Create a candidate runtime from the pinned template, install the files, and build.

    Nothing is started or routed, so this may run before the workspace's checks
    have passed. A runtime whose build fails is destroyed here.
    """
    handoff_secret = mint_secret()
    route_secret = mint_secret()
    handle = await provider.create_idle_runtime(
        runtime_ref=mint_runtime_ref(purpose="candidate"),
        snapshot_id=template.snapshot_id,
        application_port=template.application_port,
        env_vars=build_runtime_env(
            seed_target=SeedTarget.CANDIDATE,
            handoff_secret=handoff_secret,
            route_context_signing_secret=route_secret,
        ),
        egress=candidate_build_network_policy(observation_hosts=activation.endpoints.hosts()),
        execution_time_limit_seconds=_BUILD_WINDOW_SECONDS,
        vcpus=recipe.vcpus,
    )
    try:
        await _install_files(provider=provider, handle=handle, recipe=recipe, files=files)
        await _run_build_recipe(
            provider=provider, handle=handle, recipe=recipe, commands=build_commands
        )
    except BaseException:
        await provider.delete_runtime(handle=handle)
        raise
    return BuiltCandidateRuntime(
        handle=handle,
        handoff_secret=handoff_secret,
        route_secret=route_secret,
        file_digests={candidate.path: candidate.digest for candidate in files},
        build_commands=tuple(build_commands),
    )


async def deploy_candidate(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    client: httpx.AsyncClient,
    settings: PreviewSettings,
    template: RuntimeTemplate,
    recipe: TemplateRecipe,
    artifact: CandidateArtifact,
    interview_session_id: UUID,
    activation: ServerActivationContext,
    observation_port: ObservationReadinessPort,
    built: BuiltCandidateRuntime | None,
) -> DeployedCandidate:
    """Start one candidate runtime built from a frozen artifact and advance the route.

    A candidate is a separate allocation serving the same session origin, so it
    needs its own activation: the server streams bind the exact allocation, and
    the base runtime's credential can never speak for it. A runtime built ahead of
    the checks is adopted only when its files and build match the artifact exactly.
    """
    current = await route_for_session(
        database, interview_session_id=interview_session_id
    )
    if current is None:
        raise GuardRejectedError(
            machine="preview_route",
            trigger="advance",
            reason="the Interview Session has no committed route to advance",
        )
    if artifact.seed_manifest_digest != template.seed.manifest_digest:
        raise GuardRejectedError(
            machine="preview_version",
            trigger="deploy_candidate",
            reason=(
                "the artifact's seed identity does not match the pinned template's "
                "approved dataset, so the candidate would not use the agreed data "
                "conditions"
            ),
        )

    expected_digests = {candidate.path: candidate.digest for candidate in artifact.files}
    if built is not None and (
        built.file_digests != expected_digests or built.build_commands != artifact.build_recipe
    ):
        _log.warning(
            "preview_candidate_prebuild_mismatch",
            preview_version_id=str(artifact.preview_version_id),
            runtime_ref=built.handle.runtime_ref,
        )
        await provider.delete_runtime(handle=built.handle)
        built = None
    if built is None:
        built = await build_candidate_runtime(
            provider=provider,
            template=template,
            recipe=recipe,
            files=artifact.files,
            build_commands=artifact.build_recipe,
            activation=activation,
        )
    handle = built.handle
    handoff_secret = built.handoff_secret
    route_secret = built.route_secret
    await provider.extend_runtime(handle=handle, seconds=settings.execution_time_limit_seconds)
    runtime_expires_at = datetime.now(tz=UTC) + timedelta(
        seconds=settings.execution_time_limit_seconds
    )
    allocation_id = await insert_allocation(
        database,
        template=template,
        pool_role=PoolRole.CANDIDATE,
        allocation_kind="candidate",
        product_signal_id=artifact.product_signal_id,
        preview_version_id=artifact.preview_version_id,
        runtime_ref=handle.runtime_ref,
        origin_host=None,
        handoff_secret=handoff_secret,
        runtime_expires_at=runtime_expires_at,
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_runtime_allocations
            set route_context_signing_secret = %s, interview_session_id = %s,
                updated_at = now()
            where id = %s
            """,
            (route_secret, interview_session_id, allocation_id),
        )

    try:
        pins = await load_pins(database, allocation_id=allocation_id)
        async with database.transaction() as conn:
            await commit_allocation_transition(
                conn,
                client_id=template.client_id,
                allocation_id=allocation_id,
                trigger="begin_seeding",
                facts=PreviewRuntimeAllocationFacts(
                    runtime_reachable=True,
                    seed_manifest_matches=False,
                    readiness_admits_traffic=False,
                    routes_revoked=False,
                    evidence_extracted=False,
                ),
                mutation={},
                event_type="preview_candidate_seeding",
                payload={
                    "preview_version_id": str(artifact.preview_version_id),
                    "runtime_ref": handle.runtime_ref,
                },
                reason_code="candidate_deployment",
                correlation_id=artifact.product_signal_id,
                additional_locks=(),
            )

        upstream_url = provider.upstream_url(handle=handle)

        # The template's supervisor serves whatever build is on disk and installs the
        # candidate dataset as it starts, so its first start serves the candidate
        # build on the candidate's data. The activation is installed first, so that
        # one start also activates the Server SDK bound to this allocation.
        await install_server_activation(
            provider=provider,
            handle=handle,
            endpoints=activation.endpoints,
            application_directory=recipe.application_directory,
            lane_signing_key=activation.lane_signing_key,
            client_id=template.client_id,
            interview_session_id=interview_session_id,
            preview_runtime_allocation_id=allocation_id,
            public_preview_host=current.public_preview_host,
        )
        await provider.start_runtime(
            handle,
            supervisor_command=recipe.supervisor_command,
            application_directory=recipe.application_directory,
        )
        await wait_for_health(
            client=client,
            upstream_url=upstream_url,
            recipe=recipe,
            public_preview_host=current.public_preview_host,
        )
        if artifact.migrations:
            # Migrations need the candidate's own Postgres, which the supervisor
            # starts, so the product restarts afterwards to serve the migrated schema.
            await _run_migrations(
                provider=provider, handle=handle, recipe=recipe, artifact=artifact
            )
            await provider.restart_runtime(
                handle=handle,
                supervisor_command=recipe.supervisor_command,
                application_directory=recipe.application_directory,
            )
            await wait_for_health(
                client=client,
                upstream_url=upstream_url,
                recipe=recipe,
                public_preview_host=current.public_preview_host,
            )
        await verify_dataset(
            provider=provider,
            handle=handle,
            recipe=recipe,
            seed=template.seed,
            expected_target=SeedTarget.CANDIDATE,
        )

        draft = draft_route_record(
            current=current,
            preview_runtime_allocation_id=allocation_id,
            preview_version_id=artifact.preview_version_id,
            upstream_port_url=upstream_url,
            application_port=pins.application_port,
            route_context_signing_secret=route_secret,
            preview_entry_token=None,
            expires_at=datetime.now(tz=UTC)
            + timedelta(seconds=settings.route_ttl_seconds),
        )
        report = await run_session_readiness(
            client=client,
            record=draft,
            recipe=recipe,
            pins=pins,
            seed_target=SeedTarget.CANDIDATE,
            handoff_secret=handoff_secret,
            gating_probes=template.gating_probes,
            observation_port=observation_port,
            door_claim=None,
        )
        await record_readiness(
            database,
            client_id=template.client_id,
            report=report,
            preview_route_id=None,
        )
        if not report.ready:
            raise GuardRejectedError(
                machine="preview_version",
                trigger="publish",
                reason="; ".join(
                    f"{result.probe.value} is {result.outcome.value}"
                    for result in report.withheld()
                ),
            )

        async with database.transaction() as conn:
            await commit_allocation_transition(
                conn,
                client_id=template.client_id,
                allocation_id=allocation_id,
                trigger="bind_candidate",
                facts=PreviewRuntimeAllocationFacts(
                    runtime_reachable=True,
                    seed_manifest_matches=True,
                    readiness_admits_traffic=True,
                    routes_revoked=False,
                    evidence_extracted=False,
                ),
                mutation={"claimed_at": datetime.now(tz=UTC)},
                event_type="preview_candidate_bound",
                payload={"preview_version_id": str(artifact.preview_version_id)},
                reason_code="candidate_ready",
                correlation_id=artifact.product_signal_id,
                additional_locks=(),
            )
        route = await advance_route(
            database,
            interview_session_id=interview_session_id,
            preview_runtime_allocation_id=allocation_id,
            preview_version_id=artifact.preview_version_id,
            upstream_port_url=upstream_url,
            application_port=pins.application_port,
            route_context_signing_secret=route_secret,
            ttl_seconds=settings.route_ttl_seconds,
        )
        await publish_preview_version(
            database, preview_version_id=artifact.preview_version_id
        )
    except BaseException:
        await tear_down_allocation(
            database,
            provider=provider,
            activation=activation,
            allocation_id=allocation_id,
            reason="candidate_deployment_failed",
        )
        await fail_preview_version(
            database,
            preview_version_id=artifact.preview_version_id,
            reason="candidate_deployment_failed",
        )
        raise

    _log.info(
        "preview_candidate_deployed",
        client_id=str(template.client_id),
        interview_session_id=str(interview_session_id),
        preview_version_id=str(artifact.preview_version_id),
        route_revision=route.route_revision,
    )
    return DeployedCandidate(
        preview_version_id=artifact.preview_version_id,
        preview_runtime_allocation_id=allocation_id,
        route=route,
        report=report,
    )


async def rebuild_candidate(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    client: httpx.AsyncClient,
    settings: PreviewSettings,
    template: RuntimeTemplate,
    recipe: TemplateRecipe,
    preview_version_id: UUID,
    interview_session_id: UUID,
    activation: ServerActivationContext,
    observation_port: ObservationReadinessPort,
) -> DeployedCandidate:
    """Rebuild an expired candidate from its frozen artifact and seed manifest.

    The runtime bytes expire under retention policy; the artifact and the seed
    manifest do not. A rebuild therefore reproduces the exact same candidate
    rather than creating a new Preview Version, so validation evidence that cites
    it stays admissible.
    """
    artifact = await load_artifact(database, preview_version_id=preview_version_id)
    await reopen_for_rebuild(database, preview_version_id=preview_version_id)
    return await deploy_candidate(
        database,
        provider=provider,
        client=client,
        settings=settings,
        template=template,
        recipe=recipe,
        artifact=artifact,
        interview_session_id=interview_session_id,
        activation=activation,
        observation_port=observation_port,
        built=None,
    )


async def expire_idle_candidates(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    activation: ServerActivationContext,
    client_id: UUID,
    idle_seconds: int,
) -> tuple[UUID, ...]:
    """Destroy candidate runtimes whose route revision is no longer active.

    A superseded candidate is derived runtime bytes, not evidence: its artifact
    and dataset manifest remain durable, so it can be rebuilt exactly if it is
    needed again.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select allocations.id
            from public.preview_runtime_allocations as allocations
            where allocations.client_id = %s
              and allocations.pool_role = 'candidate'
              and allocations.state = 'bound'
              and allocations.updated_at < now() - make_interval(secs => %s)
              and not exists (
                select 1 from public.preview_routes routes
                where routes.preview_runtime_allocation_id = allocations.id
                  and routes.state = 'active'
              )
            """,
            (client_id, idle_seconds),
        )
        rows = await cur.fetchall()
    expired: list[UUID] = []
    for row in rows:
        allocation_id = row["id"]
        if not isinstance(allocation_id, UUID):
            raise TypeError("preview_runtime_allocations.id is not a UUID")
        await tear_down_allocation(
            database,
            provider=provider,
            activation=activation,
            allocation_id=allocation_id,
            reason="idle_candidate_expired",
        )
        expired.append(allocation_id)
    return tuple(expired)
