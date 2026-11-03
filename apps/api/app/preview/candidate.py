"""Candidate deployment: one Preview Version, one spare runtime, one advance.

Contract: docs/arch-components/preview-build.md#spare-candidate-runtimes

An accepted candidate runs in the session's spare candidate runtime
(`app.preview.spare`): a runtime started from the same pinned template the base
allocation came from, with its own dataset and its Server SDK already activated for
it, serving a production build of the candidate's files. The files are usually
staged and built there already, while its checks ran or before the participant
answered; the deployment proves the staged bytes are the artifact's and stages and
builds them itself otherwise. Migrations run only against the spare's own
database, and the base allocation is never mutated.

The session origin advances only after the candidate has passed every gating
probe. The probes run against the exact claims the next route revision will
carry, so the advance itself is one committed transaction rather than a window in
which an unproven candidate is already public. Once a spare serves a version, the
session is given its next spare.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from psycopg.types.json import Jsonb

from app.db.pool import Database
from app.errors import GuardRejectedError
from app.logging import get_logger
from app.preview.activation import ServerActivationContext, restart_observed_runtime
from app.preview.artifacts import (
    fail_preview_version,
    publish_preview_version,
)
from app.preview.config import PreviewSettings
from app.preview.machines import PreviewRuntimeAllocationFacts
from app.preview.pool import acquire_allocation, commit_allocation_transition, load_pins
from app.preview.ports import ObservationReadinessPort
from app.preview.provisioning import wait_for_health
from app.preview.readiness import record_readiness, run_session_readiness
from app.preview.recipe import TemplateRecipe
from app.preview.records import (
    CandidateArtifact,
    PreviewRouteRecord,
    ReadinessReport,
    RuntimeTemplate,
    SeedTarget,
)
from app.preview.routing import advance_route, draft_route_record, route_for_session
from app.preview.runtime import PreviewRuntimeHandle, PreviewRuntimeProvider
from app.preview.seeding import verify_dataset
from app.preview.spare import (
    SpareRuntime,
    StagedFiles,
    decoded_original,
    request_spare_candidate,
    serve_staged,
    settle_standby_spares,
    stage_files,
    start_spare_candidate,
    take_spare_candidate,
)
from app.preview.teardown import tear_down_allocation

__all__ = [
    "PREPARED_READINESS_SECONDS",
    "DeployedCandidate",
    "deploy_candidate",
    "prepared_readiness",
    "prove_candidate",
    "session_spare",
]

_log = get_logger(__name__)

_MIGRATION_TIMEOUT_SECONDS = 600
PREPARED_READINESS_SECONDS = 90
"""How long a spare's readiness, proven before the gate decided, stands for its swap."""


@dataclass(frozen=True, slots=True)
class DeployedCandidate:
    """What one candidate deployment produced."""

    preview_version_id: UUID
    preview_runtime_allocation_id: UUID
    route: PreviewRouteRecord
    report: ReadinessReport


async def session_spare(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    client: httpx.AsyncClient,
    settings: PreviewSettings,
    template: RuntimeTemplate,
    recipe: TemplateRecipe,
    interview_session_id: UUID,
    activation: ServerActivationContext,
    runtime_ref: str | None,
) -> SpareRuntime:
    """The session's ready spare, waiting for one still starting, else a new one.

    With `runtime_ref`, the spare that ref names, when it is still unbound.
    """
    spare = await take_spare_candidate(
        database,
        provider=provider,
        interview_session_id=interview_session_id,
        runtime_ref=runtime_ref,
    )
    if spare is not None:
        return spare
    if runtime_ref is not None:
        spare = await take_spare_candidate(
            database, provider=provider, interview_session_id=interview_session_id
        )
        if spare is not None:
            return spare
    _log.warning("preview_spare_candidate_missing", interview_session_id=str(interview_session_id))
    return await start_spare_candidate(
        database,
        provider=provider,
        client=client,
        settings=settings,
        template=template,
        recipe=recipe,
        interview_session_id=interview_session_id,
        activation=activation,
    )


async def prove_candidate(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    client: httpx.AsyncClient,
    settings: PreviewSettings,
    template: RuntimeTemplate,
    recipe: TemplateRecipe,
    spare: SpareRuntime,
    interview_session_id: UUID,
    preview_version_id: UUID,
    observation_port: ObservationReadinessPort,
) -> ReadinessReport:
    """Prove a staged spare ready for the version and route revision it would serve.

    The dataset and the gating probes are checked against the exact claims the
    session's next route revision would carry for that version, and a passing proof
    is recorded on the spare. A swap within `PREPARED_READINESS_SECONDS`, for the same
    version and the same next revision, uses it instead of proving again, and records
    its results then: the version does not exist until the swap publishes it.
    """
    current = await route_for_session(database, interview_session_id=interview_session_id)
    if current is None:
        raise GuardRejectedError(
            machine="preview_route",
            trigger="prove_candidate",
            reason="the Interview Session has no committed route",
        )
    pins = await load_pins(database, allocation_id=spare.preview_runtime_allocation_id)
    await verify_dataset(
        provider=provider,
        handle=spare.handle,
        recipe=recipe,
        seed=template.seed,
        expected_target=SeedTarget.CANDIDATE,
    )
    draft = draft_route_record(
        current=current,
        preview_runtime_allocation_id=spare.preview_runtime_allocation_id,
        preview_version_id=preview_version_id,
        upstream_port_url=spare.upstream_url,
        application_port=pins.application_port,
        route_context_signing_secret=spare.route_secret,
        preview_entry_token=None,
        expires_at=datetime.now(tz=UTC) + timedelta(seconds=settings.route_ttl_seconds),
    )
    report = await run_session_readiness(
        client=client,
        record=draft,
        recipe=recipe,
        pins=pins,
        seed_target=SeedTarget.CANDIDATE,
        handoff_secret=spare.handoff_secret,
        gating_probes=template.gating_probes,
        observation_port=observation_port,
        door_claim=None,
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_runtime_allocations
            set prepared_route = %s, prepared_readiness = %s, prepared_at = %s,
                updated_at = now()
            where id = %s and preview_version_id is null
            """,
            (
                Jsonb(
                    {
                        "preview_version_id": str(preview_version_id),
                        "route_revision": draft.route_revision,
                    }
                ),
                Jsonb(report.model_dump(mode="json")) if report.ready else None,
                datetime.now(tz=UTC) if report.ready else None,
                spare.preview_runtime_allocation_id,
            ),
        )
    _log.info(
        "preview_candidate_proven",
        preview_runtime_allocation_id=str(spare.preview_runtime_allocation_id),
        preview_version_id=str(preview_version_id),
        route_revision=draft.route_revision,
        ready=report.ready,
    )
    return report


async def prepared_readiness(
    database: Database, *, allocation_id: UUID, preview_version_id: UUID, route_revision: int
) -> ReadinessReport | None:
    """The spare's recorded proof, when it is fresh and for exactly this swap."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select prepared_route, prepared_readiness from public.preview_runtime_allocations
            where id = %s and prepared_at > now() - make_interval(secs => %s)
            """,
            (allocation_id, PREPARED_READINESS_SECONDS),
        )
        row = await cur.fetchone()
    if row is None or not isinstance(row["prepared_route"], dict):
        return None
    route = row["prepared_route"]
    if (
        route.get("preview_version_id") != str(preview_version_id)
        or route.get("route_revision") != route_revision
        or not isinstance(row["prepared_readiness"], dict)
    ):
        return None
    report = ReadinessReport.model_validate(row["prepared_readiness"])
    return report if report.ready else None


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
    spare: SpareRuntime,
) -> DeployedCandidate:
    """Serve one frozen artifact from a spare and advance the session's route to it.

    A candidate is a separate allocation serving the same session origin, so it
    carries its own activation: the server streams bind the exact allocation, and
    the base runtime's credential can never speak for it. Files staged into the
    spare are kept only when their digests are the artifact's, and the spare serves
    a production build of exactly them, with its database following the artifact's
    schema file, before anything is proven, so the dataset the version binds is the
    approved seed with exactly that schema change. A spare a draft already built and
    proved serves as it is.
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
    allocation_id = spare.preview_runtime_allocation_id
    handle = spare.handle
    try:
        expected = {candidate.path: candidate.digest for candidate in artifact.files}
        staged = await _staged(database, allocation_id=allocation_id)
        if staged.file_digests != expected:
            _log.info(
                "preview_candidate_restaged",
                preview_version_id=str(artifact.preview_version_id),
                runtime_ref=handle.runtime_ref,
            )
            staged = await stage_files(
                database, provider=provider, recipe=recipe, spare=spare, files=artifact.files
            )
        schema, build = await serve_staged(
            database,
            provider=provider,
            client=client,
            recipe=recipe,
            spare=spare,
            staged=staged,
            files=artifact.files,
            activation=activation,
            observation_port=observation_port,
            client_id=template.client_id,
            interview_session_id=interview_session_id,
            public_preview_host=current.public_preview_host,
        )
        if not build.passed:
            raise GuardRejectedError(
                machine="preview_version",
                trigger="build_candidate",
                reason=f"the candidate's production build failed: {build.output_tail[-1000:]}",
            )
        pins, fencing_token = await acquire_allocation(database, allocation_id=allocation_id)
        runtime_expires_at = datetime.now(tz=UTC) + timedelta(
            seconds=settings.execution_time_limit_seconds
        )
        await provider.keep_alive(handle=handle, planned_end=runtime_expires_at)
        proven = (
            None
            if artifact.migrations
            else await prepared_readiness(
                database,
                allocation_id=allocation_id,
                preview_version_id=artifact.preview_version_id,
                route_revision=current.route_revision + 1,
            )
        )
        if artifact.migrations:
            # Migrations need the spare's own Postgres, which its supervisor runs, so
            # the product restarts afterwards to serve the migrated schema.
            await _run_migrations(
                provider=provider, handle=handle, recipe=recipe, artifact=artifact
            )
            await restart_observed_runtime(
                database,
                provider=provider,
                commands=activation.commands,
                client_id=template.client_id,
                preview_runtime_allocation_id=allocation_id,
                handle=handle,
                supervisor_command=recipe.supervisor_command,
                application_directory=recipe.application_directory,
            )
            await wait_for_health(
                client=client,
                upstream_url=spare.upstream_url,
                recipe=recipe,
                public_preview_host=current.public_preview_host,
            )
        if proven is not None:
            # Proven before the gate decided, for this version and this revision,
            # and recorded now that the version exists.
            report = proven
            await record_readiness(
                database, client_id=template.client_id, report=report, preview_route_id=None
            )
        else:
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
                upstream_port_url=spare.upstream_url,
                application_port=pins.application_port,
                route_context_signing_secret=spare.route_secret,
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
                handoff_secret=spare.handoff_secret,
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
                fencing_token=fencing_token,
                trigger="bind_candidate",
                facts=PreviewRuntimeAllocationFacts(
                    runtime_reachable=True,
                    seed_manifest_matches=True,
                    readiness_admits_traffic=True,
                    routes_revoked=False,
                    evidence_extracted=False,
                ),
                mutation={
                    "claimed_at": datetime.now(tz=UTC),
                    "preview_version_id": artifact.preview_version_id,
                    "runtime_expires_at": runtime_expires_at,
                },
                event_type="preview_candidate_bound",
                payload={
                    "preview_version_id": str(artifact.preview_version_id),
                    "readiness_proven_before_the_gate": proven is not None,
                    # The dataset the version binds: the approved seed, with the
                    # schema change its spare's database followed.
                    "seed_manifest_digest": template.seed.manifest_digest,
                    "schema_mode": schema.mode.value,
                    "schema_digest": schema.digest,
                },
                reason_code="candidate_ready",
                correlation_id=artifact.product_signal_id,
                additional_locks=(),
            )
        route = await advance_route(
            database,
            interview_session_id=interview_session_id,
            preview_runtime_allocation_id=allocation_id,
            preview_version_id=artifact.preview_version_id,
            upstream_port_url=spare.upstream_url,
            application_port=pins.application_port,
            route_context_signing_secret=spare.route_secret,
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

    # A standby that already built this version becomes the session's next spare;
    # without one, a new spare starts and builds it.
    if not await settle_standby_spares(
        database,
        provider=provider,
        activation=activation,
        client_id=template.client_id,
        interview_session_id=interview_session_id,
        served_files={candidate.path: candidate.digest for candidate in artifact.files},
    ):
        async with database.transaction() as conn:
            await request_spare_candidate(
                conn,
                client_id=template.client_id,
                product_signal_id=artifact.product_signal_id,
                interview_session_id=interview_session_id,
            )
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


async def _staged(database: Database, *, allocation_id: UUID) -> StagedFiles:
    """The files last staged into a spare, with the base files they replaced."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select staged_files, staged_originals from public.preview_runtime_allocations
            where id = %s
            """,
            (allocation_id,),
        )
        row = await cur.fetchone()
    digests = None if row is None else row["staged_files"]
    originals = None if row is None else row["staged_originals"]
    return StagedFiles(
        file_digests=(
            {str(path): str(digest) for path, digest in digests.items()}
            if isinstance(digests, dict)
            else {}
        ),
        originals=(
            {str(path): decoded_original(value) for path, value in originals.items()}
            if isinstance(originals, dict)
            else {}
        ),
    )
