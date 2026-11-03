"""Spare candidate runtimes: running before a candidate exists, swapped in once it passes.

Contract: docs/arch-components/preview-build.md#spare-candidate-runtimes

A spare is a `candidate` allocation started for one Interview Session before any
Preview Version exists for it. It boots from the pinned template with its own copy
of the dataset, has its Server SDK activation written before its first start so it
never restarts to activate, and serves the image's production build until it is
given files. Its landing page is rendered once, so a spare that cannot serve is
known before any candidate needs it.

Nothing routes to a spare. A candidate's files, or a prepared draft's, are written
into it (`stage_files`); staging a different set first restores the files the
earlier set replaced, from the copies taken when they were first replaced. The
spare then serves them (`serve_staged`): it follows their schema and builds them
for production at once, with the build cache the image's own build left, and
restarts into the new build. Once a spare serves a version it is bound and never
staged again, and the session gets a new spare (`request_spare_candidate`).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import shlex
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Final
from uuid import UUID

import httpx
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import ActorKind, AggregateKind

from app.connectors.ports import WorkspaceExecution
from app.db.kernel import append_fact
from app.db.pool import Database
from app.db.rows import as_str, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, JobIntent, StreamRef
from app.errors import GuardRejectedError
from app.ids import uuid7
from app.logging import get_logger
from app.preview.activation import (
    ServerActivationContext,
    end_server_stream,
    install_server_activation,
    restart_application,
    restart_observed_runtime,
)
from app.preview.artifacts import load_artifact
from app.preview.config import PreviewSettings
from app.preview.machines import PreviewRuntimeAllocationFacts
from app.preview.pool import (
    acquire_allocation,
    commit_allocation_transition,
    insert_allocation,
    mint_secret,
    take_over_allocation,
)
from app.preview.ports import ObservationReadinessPort
from app.preview.provisioning import wait_for_health
from app.preview.readiness import ProbeTarget, upstream_target
from app.preview.recipe import TemplateRecipe
from app.preview.records import (
    CandidateFile,
    PoolRole,
    PreviewRouteRecord,
    RuntimeTemplate,
    SeedTarget,
)
from app.preview.render_check import RenderCheck, run_render_check
from app.preview.routing import BASE_PREVIEW_VERSION_ID, draft_route_record, route_for_session
from app.preview.runtime import PreviewRuntimeHandle, PreviewRuntimeProvider
from app.preview.schema_delta import SchemaDelta, SchemaMode, schema_delta
from app.preview.seeding import verify_dataset
from app.preview.teardown import tear_down_allocation
from app.preview.template import (
    build_runtime_env,
    mint_runtime_ref,
    runtime_network_policy,
)

__all__ = [
    "PREPARE_SPARE_CANDIDATE",
    "ServedBuild",
    "SpareRuntime",
    "StagedFiles",
    "StandbyDraft",
    "decoded_original",
    "prepare_standby_spare",
    "render_spare",
    "request_spare_candidate",
    "request_standby_spare",
    "serve_staged",
    "session_needs_spare",
    "settle_standby_spares",
    "spare_probe_target",
    "stage_files",
    "standby_disposition",
    "start_spare_candidate",
    "take_spare_candidate",
]

_log = get_logger(__name__)

PREPARE_SPARE_CANDIDATE: Final[str] = "preview.prepare_spare_candidate"
_ACTOR: Final[Actor] = Actor(kind=ActorKind.SERVICE, ref="preview.spare_candidates")
_START_WINDOW_SECONDS: Final[int] = 1200
"""A spare's first sandbox limit, until its allocation row exists and keeps it alive."""
_TAKE_WAIT_SECONDS: Final[float] = 90.0
"""How long a candidate waits for its session's spare still starting."""
_TAKE_POLL_SECONDS: Final[float] = 1.0
_DIRECTORY_TIMEOUT_SECONDS: Final[int] = 60
_SPARE_JOB_TIMEOUT_SECONDS: Final[int] = 600
_BUILD_TIMEOUT_SECONDS: Final[int] = 900
_ACTIVATION_WAIT_SECONDS: Final[float] = 30.0
"""How long a restarted spare's Server SDK has to activate again."""
_ACTIVATION_POLL_SECONDS: Final[float] = 0.25
_OUTPUT_TAIL_CHARACTERS: Final[int] = 4000


@dataclass(frozen=True, slots=True)
class SpareRuntime:
    """A started, unrouted candidate runtime and what reaching it takes."""

    preview_runtime_allocation_id: UUID
    handle: PreviewRuntimeHandle
    upstream_url: str
    handoff_secret: str
    route_secret: str
    authenticated_identity: str


@dataclass(frozen=True, slots=True)
class ServedBuild:
    """The production build a spare serves its staged files from."""

    exit_code: int
    seconds: float
    reused: bool
    """Whether the spare already served a build of exactly these files."""
    restart: str
    """What was restarted to serve it: `application`, `runtime`, or `none`."""
    restart_seconds: float
    output_tail: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0

    def as_column(self) -> dict[str, object]:
        return {
            "exit_code": self.exit_code,
            "seconds": round(self.seconds, 2),
            "reused": self.reused,
            "restart": self.restart,
            "restart_seconds": round(self.restart_seconds, 2),
            "output_tail": self.output_tail,
        }


@dataclass(frozen=True, slots=True)
class StandbyDraft:
    """The draft whose files a session's standby spare builds."""

    prepared_draft_id: UUID
    files: tuple[CandidateFile, ...]


@dataclass(frozen=True, slots=True)
class StagedFiles:
    """One set of files written into a spare, and the base files they replaced."""

    file_digests: Mapping[str, str]
    originals: Mapping[str, str | None]
    """Each staged path's base content, or `None` for a path the base did not have."""


async def start_spare_candidate(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    client: httpx.AsyncClient,
    settings: PreviewSettings,
    template: RuntimeTemplate,
    recipe: TemplateRecipe,
    interview_session_id: UUID,
    activation: ServerActivationContext,
    observation_port: ObservationReadinessPort | None = None,
    standby: StandbyDraft | None = None,
) -> SpareRuntime:
    """Start one spare candidate runtime for a session and render its landing page.

    With `observation_port`, a spare started while the session is served a Preview
    Version is given that version's files and builds them before it is ready, so a
    change asked for while the participant tries the version rebuilds only what the
    change touches. With `standby` as well, it is the session's standby spare and
    builds that draft's files instead (`prepare_standby_spare`).
    """
    current = await _current_route(database, interview_session_id=interview_session_id)
    handoff_secret = mint_secret()
    route_secret = mint_secret()
    handle = await provider.create_idle_runtime(
        runtime_ref=mint_runtime_ref(purpose="candidate"),
        snapshot_id=template.snapshot_id,
        application_port=template.application_port,
        env_vars={
            **build_runtime_env(
                seed_target=SeedTarget.CANDIDATE,
                handoff_secret=handoff_secret,
                route_context_signing_secret=route_secret,
            ),
        },
        egress=runtime_network_policy(observation_hosts=activation.endpoints.hosts()),
        execution_time_limit_seconds=_START_WINDOW_SECONDS,
        vcpus=recipe.candidate_vcpus,
    )
    runtime_expires_at = datetime.now(tz=UTC) + timedelta(
        seconds=settings.execution_time_limit_seconds
    )
    try:
        await provider.keep_alive(handle=handle, planned_end=runtime_expires_at)
        allocation_id = await insert_allocation(
            database,
            template=template,
            pool_role=PoolRole.CANDIDATE,
            allocation_kind="candidate",
            product_signal_id=current.product_signal_id,
            preview_version_id=None,
            runtime_ref=handle.runtime_ref,
            origin_host=None,
            handoff_secret=handoff_secret,
            runtime_expires_at=runtime_expires_at,
        )
    except BaseException:
        await provider.delete_runtime(handle=handle)
        raise
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_runtime_allocations
            set route_context_signing_secret = %s, interview_session_id = %s,
                standby_draft_id = %s, updated_at = now()
            where id = %s
            """,
            (
                route_secret,
                interview_session_id,
                None if standby is None else standby.prepared_draft_id,
                allocation_id,
            ),
        )
    try:
        pins, fencing_token = await acquire_allocation(database, allocation_id=allocation_id)
        async with database.transaction() as conn:
            await commit_allocation_transition(
                conn,
                client_id=template.client_id,
                allocation_id=allocation_id,
                fencing_token=fencing_token,
                trigger="begin_seeding",
                facts=_facts(seed_manifest_matches=False),
                mutation={},
                event_type="preview_spare_candidate_starting",
                payload={"runtime_ref": handle.runtime_ref},
                reason_code="spare_candidate",
                correlation_id=current.product_signal_id,
                additional_locks=(),
            )
        # The activation is written before the first start, so the one start
        # activates the Server SDK for this allocation and nothing restarts.
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
        upstream_url = provider.upstream_url(handle=handle)
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
        spare = SpareRuntime(
            preview_runtime_allocation_id=allocation_id,
            handle=handle,
            upstream_url=upstream_url,
            handoff_secret=handoff_secret,
            route_secret=route_secret,
            authenticated_identity=pins.authenticated_identity,
        )
        warmed = await render_spare(
            client=client,
            recipe=recipe,
            spare=spare,
            current=current,
            pages=None,
            expected_targets=frozenset(),
        )
        if not warmed.passed:
            raise GuardRejectedError(
                machine="preview_runtime_allocation",
                trigger="prepare_spare",
                reason="the spare's landing page did not render: " + "; ".join(warmed.failures),
            )
        prebuilt: tuple[str, Sequence[CandidateFile]] | None = None
        if standby is not None:
            prebuilt = (f"draft {standby.prepared_draft_id}", standby.files)
        elif _serves_version(current) and current.preview_version_id is not None:
            prebuilt = (
                f"version {current.preview_version_id}",
                (await load_artifact(database, preview_version_id=current.preview_version_id)).files,
            )
        if prebuilt is not None and observation_port is not None:
            await _prebuild(
                database,
                provider=provider,
                client=client,
                recipe=recipe,
                spare=spare,
                source=prebuilt[0],
                files=prebuilt[1],
                activation=activation,
                observation_port=observation_port,
                client_id=template.client_id,
                interview_session_id=interview_session_id,
                public_preview_host=current.public_preview_host,
            )
            # Staging took the spare over, so it is held again to record it ready.
            fencing_token = await take_over_allocation(database, allocation_id=allocation_id)
        async with database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                update public.preview_runtime_allocations
                set spare_ready_at = now(), standby_draft_id = %s, updated_at = now()
                where id = %s and fencing_token = %s
                """,
                (
                    None if standby is None else standby.prepared_draft_id,
                    allocation_id,
                    fencing_token,
                ),
            )
            if cur.rowcount != 1:
                raise GuardRejectedError(
                    machine="preview_runtime_allocation",
                    trigger="prepare_spare",
                    reason="another holder took the spare over while it started",
                )
    except BaseException:
        await tear_down_allocation(
            database,
            provider=provider,
            activation=activation,
            allocation_id=allocation_id,
            reason="spare_candidate_failed",
        )
        raise
    _log.info(
        "preview_spare_candidate_ready",
        interview_session_id=str(interview_session_id),
        preview_runtime_allocation_id=str(allocation_id),
        runtime_ref=handle.runtime_ref,
    )
    return spare


async def _prebuild(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    client: httpx.AsyncClient,
    recipe: TemplateRecipe,
    spare: SpareRuntime,
    source: str,
    files: Sequence[CandidateFile],
    activation: ServerActivationContext,
    observation_port: ObservationReadinessPort,
    client_id: UUID,
    interview_session_id: UUID,
    public_preview_host: str,
) -> None:
    """Build files a spare will be needed for, before it is recorded ready.

    A build that fails leaves the spare serving what it served, and the files staged
    there later build everything themselves.
    """
    started = time.monotonic()
    staged = await stage_files(database, provider=provider, recipe=recipe, spare=spare, files=files)
    _, build = await serve_staged(
        database,
        provider=provider,
        client=client,
        recipe=recipe,
        spare=spare,
        staged=staged,
        files=files,
        activation=activation,
        observation_port=observation_port,
        client_id=client_id,
        interview_session_id=interview_session_id,
        public_preview_host=public_preview_host,
    )
    _log.info(
        "preview_spare_prebuilt",
        preview_runtime_allocation_id=str(spare.preview_runtime_allocation_id),
        source=source,
        files=len(files),
        build_passed=build.passed,
        build_reused=build.reused,
        seconds=round(time.monotonic() - started, 1),
    )


async def take_spare_candidate(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    interview_session_id: UUID,
    runtime_ref: str | None = None,
) -> SpareRuntime | None:
    """Return the session's ready spare, waiting for one still starting.

    With `runtime_ref`, only that spare is returned. Without a spare starting or
    ready, `None` is returned at once. The session's standby spare is never
    returned: it holds the files of a version not yet served.
    """
    deadline = asyncio.get_running_loop().time() + _TAKE_WAIT_SECONDS
    while True:
        async with database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select id, runtime_ref, application_port, handoff_secret,
                       route_context_signing_secret, authenticated_identity,
                       spare_ready_at is not null as ready
                from public.preview_runtime_allocations
                where interview_session_id = %s and allocation_kind = 'candidate'
                  and preview_version_id is null and state in ('pending', 'seeding')
                  and standby_draft_id is null
                  and (%s::text is null or runtime_ref = %s)
                order by spare_ready_at nulls last, created_at
                limit 1
                """,
                (interview_session_id, runtime_ref, runtime_ref),
            )
            row = await cur.fetchone()
        if row is not None and row["ready"]:
            handle = await provider.attach(
                runtime_ref=as_str(row, "runtime_ref"),
                application_port=int(str(row["application_port"])),
            )
            return SpareRuntime(
                preview_runtime_allocation_id=as_uuid(row, "id"),
                handle=handle,
                upstream_url=provider.upstream_url(handle=handle),
                handoff_secret=as_str(row, "handoff_secret"),
                route_secret=as_str(row, "route_context_signing_secret"),
                authenticated_identity=as_str(row, "authenticated_identity"),
            )
        if row is None or asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(_TAKE_POLL_SECONDS)


async def stage_files(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    recipe: TemplateRecipe,
    spare: SpareRuntime,
    files: Sequence[CandidateFile],
) -> StagedFiles:
    """Write one set of files into a spare, restoring what an earlier set replaced.

    The spare is taken over first, so a holder that staged before this one is
    refused when it records its own set. Only files whose bytes differ from what is
    staged are written.
    """
    allocation_id = spare.preview_runtime_allocation_id
    fencing_token = await take_over_allocation(database, allocation_id=allocation_id)
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select staged_files, staged_originals from public.preview_runtime_allocations
            where id = %s and preview_version_id is null and state in ('pending', 'seeding')
            """,
            (allocation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise GuardRejectedError(
            machine="preview_runtime_allocation",
            trigger="stage_files",
            reason="a spare that has served a version is never staged again",
        )
    staged = row["staged_files"] if isinstance(row["staged_files"], Mapping) else {}
    originals: dict[str, str | None] = {
        str(path): decoded_original(value)
        for path, value in (
            row["staged_originals"] if isinstance(row["staged_originals"], Mapping) else {}
        ).items()
    }
    wanted = {candidate.path for candidate in files}
    root = recipe.application_directory.rstrip("/")
    unseen = [candidate.path for candidate in files if candidate.path not in originals]
    contents = await asyncio.gather(
        *(_read_optional(provider, spare.handle, f"{root}/{path}") for path in unseen)
    )
    originals.update(zip(unseen, contents, strict=True))
    await _restore(
        provider,
        spare.handle,
        root=root,
        paths=[path for path in staged if path not in wanted],
        originals=originals,
    )
    # A file already staged with these bytes, as most of a revised draft's are, was
    # verified when it was written and is not written again.
    await _write_files(
        provider,
        spare.handle,
        recipe=recipe,
        files=[candidate for candidate in files if staged.get(candidate.path) != candidate.digest],
    )
    digests = {candidate.path: candidate.digest for candidate in files}
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_runtime_allocations
            set staged_files = %s, staged_originals = %s,
                prepared_route = null, prepared_readiness = null, prepared_at = null,
                updated_at = now()
            where id = %s and fencing_token = %s and preview_version_id is null
              and state in ('pending', 'seeding')
            """,
            (Jsonb(digests), Jsonb(_encoded(originals)), allocation_id, fencing_token),
        )
        if cur.rowcount != 1:
            raise GuardRejectedError(
                machine="preview_runtime_allocation",
                trigger="stage_files",
                reason="another holder staged files into the spare meanwhile",
            )
    _log.info(
        "preview_spare_candidate_staged",
        preview_runtime_allocation_id=str(allocation_id),
        files=len(files),
        restored=len([path for path in staged if path not in wanted]),
    )
    return StagedFiles(
        file_digests=digests,
        originals={path: originals.get(path) for path in wanted},
    )


async def serve_staged(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    client: httpx.AsyncClient,
    recipe: TemplateRecipe,
    spare: SpareRuntime,
    staged: StagedFiles,
    files: Sequence[CandidateFile],
    activation: ServerActivationContext,
    observation_port: ObservationReadinessPort,
    client_id: UUID,
    interview_session_id: UUID,
    public_preview_host: str,
) -> tuple[SchemaDelta, ServedBuild]:
    """Make the spare serve its staged files, before anything is shown.

    The production build of the files starts at once, and an additive schema change
    runs in the spare's own database beside it; a spare that already serves a build
    of exactly these files is not built again. A passing build is served by
    restarting the application alone, or the whole runtime when the schema change
    cannot apply in place, which installs the dataset afresh from the staged schema
    file, once per schema. Either restart waits for the new process's Server SDK to
    activate again and withdraws any readiness proven for the spare. A failed build
    is not served.
    """
    allocation_id = spare.preview_runtime_allocation_id
    candidate = next((item.content for item in files if item.path == recipe.schema_path), None)
    recorded = await _recorded_schema(database, allocation_id=allocation_id)
    applied = recorded.statements if recorded.mode is SchemaMode.IN_PLACE else ()
    if candidate is None:
        delta = SchemaDelta(mode=SchemaMode.UNCHANGED, statements=())
    else:
        delta = schema_delta(base=staged.originals.get(recipe.schema_path), candidate=candidate)
    pending = tuple(statement for statement in delta.statements if statement not in applied)
    in_place = delta.mode is not SchemaMode.RESTART and set(applied) <= set(delta.statements)
    reinstall = not in_place and recorded != SchemaDelta(
        mode=SchemaMode.RESTART, statements=delta.statements
    )
    reused = await _built_files(database, allocation_id=allocation_id) == dict(
        staged.file_digests
    )
    started = time.monotonic()
    build, _, _ = await asyncio.gather(
        _settled_build() if reused else _build(provider, spare.handle, recipe=recipe),
        _apply_statements(provider, spare.handle, recipe=recipe, statements=pending)
        if in_place and pending
        else _settled_build(),
        # The process serving the earlier files is replaced once the build is done,
        # so its server stream is ended while the build runs. Nothing routes to the
        # spare meanwhile.
        _settled_build()
        if reused
        else end_server_stream(
            database,
            commands=activation.commands,
            client_id=client_id,
            preview_runtime_allocation_id=allocation_id,
        ),
    )
    seconds = 0.0 if reused else time.monotonic() - started
    restart = "none"
    restart_started = time.monotonic()
    if build.exit_code == 0 and reinstall:
        restart = "runtime"
        await restart_observed_runtime(
            database,
            provider=provider,
            commands=activation.commands,
            client_id=client_id,
            preview_runtime_allocation_id=allocation_id,
            handle=spare.handle,
            supervisor_command=recipe.supervisor_command,
            application_directory=recipe.application_directory,
        )
        await wait_for_health(
            client=client,
            upstream_url=spare.upstream_url,
            recipe=recipe,
            public_preview_host=public_preview_host,
        )
    elif build.exit_code == 0 and not reused:
        restart = "application"
        await restart_application(
            provider=provider,
            preview_runtime_allocation_id=allocation_id,
            handle=spare.handle,
            restart_command=recipe.restart_application_command,
            application_directory=recipe.application_directory,
        )
    if restart != "none":
        await _await_activation(
            observation_port,
            client_id=client_id,
            interview_session_id=interview_session_id,
            allocation_id=allocation_id,
            after=datetime.now(tz=UTC) - timedelta(seconds=time.monotonic() - restart_started),
        )
    served = ServedBuild(
        exit_code=build.exit_code,
        seconds=seconds,
        reused=reused,
        restart=restart,
        restart_seconds=time.monotonic() - restart_started if restart != "none" else 0.0,
        output_tail=build.stdout[-_OUTPUT_TAIL_CHARACTERS:],
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_runtime_allocations
            set staged_schema = %s, built_files = %s,
                prepared_route = case when %s then prepared_route end,
                prepared_readiness = case when %s then prepared_readiness end,
                prepared_at = case when %s then prepared_at end,
                updated_at = now()
            where id = %s
            """,
            (
                Jsonb(
                    {
                        "mode": SchemaMode.IN_PLACE.value if in_place else SchemaMode.RESTART.value,
                        "digest": delta.digest,
                        "statements": list(delta.statements),
                    }
                ),
                Jsonb(dict(staged.file_digests)) if served.passed else None,
                restart == "none",
                restart == "none",
                restart == "none",
                allocation_id,
            ),
        )
    _log.info(
        "preview_spare_served",
        preview_runtime_allocation_id=str(allocation_id),
        schema="in_place" if in_place else "restart",
        schema_statements=len(pending) if in_place else len(delta.statements),
        build_passed=served.passed,
        build_reused=reused,
        build_seconds=round(seconds, 1),
        restart=restart,
        restart_seconds=round(served.restart_seconds, 1),
    )
    return delta, served


async def _build(
    provider: PreviewRuntimeProvider, handle: PreviewRuntimeHandle, *, recipe: TemplateRecipe
) -> WorkspaceExecution:
    """Build the application from the files written into it, for production."""
    return await provider.execute(
        handle=handle,
        script=recipe.rebuild_command,
        working_directory=recipe.application_directory,
        timeout_seconds=_BUILD_TIMEOUT_SECONDS,
    )


async def _settled_build() -> WorkspaceExecution:
    """A build, or a schema step, that did not need to run."""
    return WorkspaceExecution(command="", exit_code=0, stdout="", duration_seconds=0.0)


async def _built_files(database: Database, *, allocation_id: UUID) -> dict[str, str] | None:
    """The file digests the spare's served build was built from."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select built_files from public.preview_runtime_allocations where id = %s",
            (allocation_id,),
        )
        row = await cur.fetchone()
    built = None if row is None else row["built_files"]
    return {str(path): str(digest) for path, digest in built.items()} if isinstance(built, dict) else None


async def _await_activation(
    observation_port: ObservationReadinessPort,
    *,
    client_id: UUID,
    interview_session_id: UUID,
    allocation_id: UUID,
    after: datetime,
) -> None:
    """Wait, within a bound, for the restarted process's Server SDK to activate.

    A spare whose activation is late is not refused here: its readiness proof finds
    the activation missing, and the swap proves the spare again.
    """
    deadline = time.monotonic() + _ACTIVATION_WAIT_SECONDS
    while time.monotonic() < deadline:
        observed = await observation_port.activation(
            client_id=client_id,
            interview_session_id=interview_session_id,
            preview_runtime_allocation_id=allocation_id,
        )
        if observed.activated and observed.activated_at is not None and observed.activated_at >= after:
            return
        await asyncio.sleep(_ACTIVATION_POLL_SECONDS)
    _log.warning(
        "preview_spare_activation_late",
        preview_runtime_allocation_id=str(allocation_id),
        waited_seconds=_ACTIVATION_WAIT_SECONDS,
    )


async def _recorded_schema(database: Database, *, allocation_id: UUID) -> SchemaDelta:
    """The schema change a spare's database last followed, and how."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select staged_schema from public.preview_runtime_allocations where id = %s",
            (allocation_id,),
        )
        row = await cur.fetchone()
    recorded = None if row is None else row["staged_schema"]
    if not isinstance(recorded, dict) or recorded.get("mode") not in (
        SchemaMode.IN_PLACE.value,
        SchemaMode.RESTART.value,
    ):
        return SchemaDelta(mode=SchemaMode.UNCHANGED, statements=())
    statements = recorded.get("statements")
    return SchemaDelta(
        mode=SchemaMode(str(recorded["mode"])),
        statements=tuple(str(item) for item in statements) if isinstance(statements, list) else (),
    )


async def _apply_statements(
    provider: PreviewRuntimeProvider,
    handle: PreviewRuntimeHandle,
    *,
    recipe: TemplateRecipe,
    statements: Sequence[str],
) -> WorkspaceExecution:
    """Run additive statements in the spare's database in one transaction."""
    path = "/tmp/utrace-schema-delta.sql"
    await provider.write_file(
        handle=handle,
        path=path,
        content=("".join(f"{statement};\n" for statement in statements)).encode("utf-8"),
    )
    result = await provider.execute(
        handle=handle,
        script=recipe.schema_command.replace("{file}", path),
        working_directory=recipe.application_directory,
        timeout_seconds=_DIRECTORY_TIMEOUT_SECONDS,
    )
    if result.exit_code != 0:
        raise GuardRejectedError(
            machine="preview_version",
            trigger="apply_schema",
            reason=f"the candidate's schema change did not apply: {result.stdout[-1000:]}",
        )
    return result


async def render_spare(
    *,
    client: httpx.AsyncClient,
    recipe: TemplateRecipe,
    spare: SpareRuntime,
    current: PreviewRouteRecord,
    pages: Sequence[str] | None,
    expected_targets: frozenset[str],
) -> RenderCheck:
    """Run the render check against a spare through its runtime port, as a fresh browser."""
    async with httpx.AsyncClient(timeout=client.timeout, follow_redirects=False) as browser:
        return await run_render_check(
            browser=browser,
            target=spare_probe_target(spare=spare, current=current),
            recipe=recipe,
            handoff_secret=spare.handoff_secret,
            authenticated_identity=spare.authenticated_identity,
            pages=pages,
            expected_targets=expected_targets,
        )


def spare_probe_target(*, spare: SpareRuntime, current: PreviewRouteRecord) -> ProbeTarget:
    """Reach a spare through its port, with the claims the session's next route carries."""
    draft = draft_route_record(
        current=current,
        preview_runtime_allocation_id=spare.preview_runtime_allocation_id,
        preview_version_id=current.preview_version_id,
        upstream_port_url=spare.upstream_url,
        application_port=spare.handle.application_port,
        route_context_signing_secret=spare.route_secret,
        preview_entry_token=None,
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
    )
    return upstream_target(
        record=draft, signing_secret=spare.route_secret, public_path_enabled=False
    )


async def request_spare_candidate(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
) -> bool:
    """Queue a spare for the session unless one is starting, ready, or queued already."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select
              exists (
                select 1 from public.preview_runtime_allocations
                where interview_session_id = %s and allocation_kind = 'candidate'
                  and preview_version_id is null and state in ('pending', 'seeding')
                  and standby_draft_id is null
              ) as spare,
              exists (
                select 1 from public.jobs
                where job_type = %s and concurrency_key = %s
                  and state in ('pending', 'ready', 'leased')
              ) as queued
            """,
            (interview_session_id, PREPARE_SPARE_CANDIDATE, _concurrency_key(interview_session_id)),
        )
        row = await cur.fetchone()
    if row is None or row["spare"] or row["queued"]:
        return False
    now = datetime.now(tz=UTC)
    stream = StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=product_signal_id)
    await append_fact(
        conn,
        client_id=client_id,
        event=EventAppend(
            message_id=uuid7(),
            stream=stream,
            event_type="preview_spare_candidate_requested",
            payload={"interview_session_id": str(interview_session_id)},
            actor=_ACTOR,
            causation_id=None,
            correlation_id=product_signal_id,
            occurred_at=now,
        ),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=stream,
            action="request_spare_candidate",
            outcome="queued",
            reason_code=None,
            actor=_ACTOR,
            pinned_configuration_release_id=None,
            detail={"interview_session_id": str(interview_session_id)},
        ),
        jobs=(
            JobIntent(
                job_id=uuid7(),
                job_type=PREPARE_SPARE_CANDIDATE,
                input={
                    "product_signal_id": str(product_signal_id),
                    "interview_session_id": str(interview_session_id),
                },
                deadline_at=now + timedelta(hours=1),
                timeout_seconds=_SPARE_JOB_TIMEOUT_SECONDS,
                max_runs=2,
                resource_budget={"runtimes": 1},
                retryable_categories=["timeout", "rate_limited", "provider_unavailable"],
                backoff_base_seconds=5,
                backoff_max_seconds=30,
                concurrency_key=_concurrency_key(interview_session_id),
                cancellation_scope_id=None,
                not_before=now,
            ),
        ),
    )
    return True


async def request_standby_spare(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
) -> bool:
    """Queue the session's standby spare to follow its newest built draft.

    Nothing is queued while a standby Job waits to run, since it builds the newest
    draft when it does; one already running is followed by another, for files newer
    than the ones it builds.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select exists (
              select 1 from public.jobs
              where job_type = %s and concurrency_key = %s and state in ('pending', 'ready')
            ) as queued
            """,
            (PREPARE_SPARE_CANDIDATE, _standby_key(interview_session_id)),
        )
        row = await cur.fetchone()
    if row is None or row["queued"]:
        return False
    now = datetime.now(tz=UTC)
    stream = StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=product_signal_id)
    await append_fact(
        conn,
        client_id=client_id,
        event=EventAppend(
            message_id=uuid7(),
            stream=stream,
            event_type="preview_standby_spare_requested",
            payload={"interview_session_id": str(interview_session_id)},
            actor=_ACTOR,
            causation_id=None,
            correlation_id=product_signal_id,
            occurred_at=now,
        ),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=stream,
            action="request_standby_spare",
            outcome="queued",
            reason_code=None,
            actor=_ACTOR,
            pinned_configuration_release_id=None,
            detail={"interview_session_id": str(interview_session_id)},
        ),
        jobs=(
            JobIntent(
                job_id=uuid7(),
                job_type=PREPARE_SPARE_CANDIDATE,
                input={
                    "product_signal_id": str(product_signal_id),
                    "interview_session_id": str(interview_session_id),
                    "standby": True,
                },
                deadline_at=now + timedelta(hours=1),
                timeout_seconds=_SPARE_JOB_TIMEOUT_SECONDS,
                max_runs=2,
                resource_budget={"runtimes": 1},
                retryable_categories=["timeout", "rate_limited", "provider_unavailable"],
                backoff_base_seconds=5,
                backoff_max_seconds=30,
                concurrency_key=_standby_key(interview_session_id),
                cancellation_scope_id=None,
                not_before=now,
            ),
        ),
    )
    return True


async def prepare_standby_spare(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    client: httpx.AsyncClient,
    settings: PreviewSettings,
    template: RuntimeTemplate,
    recipe: TemplateRecipe,
    interview_session_id: UUID,
    activation: ServerActivationContext,
    observation_port: ObservationReadinessPort,
) -> None:
    """Bring the session's standby spare to the files of its newest built draft.

    The standby spare builds the version the session is about to be served, before
    it is: started for the first draft built in the session, and restaged and
    rebuilt, incrementally, for each newer one, so it never serves a stale draft's
    build. Nothing stages candidates into it. Once the version it holds is served,
    it becomes the session's next spare (`settle_standby_spares`).
    """
    newest = await _newest_built_draft(database, interview_session_id=interview_session_id)
    if newest is None:
        return
    wanted = {candidate.path: candidate.digest for candidate in newest.files}
    current = await _current_route(database, interview_session_id=interview_session_id)
    if _serves_version(current) and current.preview_version_id is not None:
        served = await load_artifact(database, preview_version_id=current.preview_version_id)
        if {candidate.path: candidate.digest for candidate in served.files} == wanted:
            # The session is served this draft already, so no standby builds it; it
            # gets a next spare that does, unless one exists.
            await settle_standby_spares(
                database,
                provider=provider,
                activation=activation,
                client_id=template.client_id,
                interview_session_id=interview_session_id,
                served_files=wanted,
            )
            async with database.transaction() as conn:
                await request_spare_candidate(
                    conn,
                    client_id=template.client_id,
                    product_signal_id=current.product_signal_id,
                    interview_session_id=interview_session_id,
                )
            return
    standby = await _standby_spare(database, provider=provider, interview_session_id=interview_session_id)
    if standby is not None and await _built_files(
        database, allocation_id=standby.preview_runtime_allocation_id
    ) == wanted:
        await _mark_standby(
            database, allocation_id=standby.preview_runtime_allocation_id, draft=newest.prepared_draft_id
        )
    elif standby is None:
        await start_spare_candidate(
            database,
            provider=provider,
            client=client,
            settings=settings,
            template=template,
            recipe=recipe,
            interview_session_id=interview_session_id,
            activation=activation,
            observation_port=observation_port,
            standby=newest,
        )
    else:
        # Not ready while it rebuilds, so a swap meanwhile leaves it to this Job.
        await _mark_standby(database, allocation_id=standby.preview_runtime_allocation_id, draft=None)
        await _prebuild(
            database,
            provider=provider,
            client=client,
            recipe=recipe,
            spare=standby,
            source=f"draft {newest.prepared_draft_id}",
            files=newest.files,
            activation=activation,
            observation_port=observation_port,
            client_id=template.client_id,
            interview_session_id=interview_session_id,
            public_preview_host=current.public_preview_host,
        )
        await _mark_standby(
            database, allocation_id=standby.preview_runtime_allocation_id, draft=newest.prepared_draft_id
        )
    await settle_standby_spares(
        database,
        provider=provider,
        activation=activation,
        client_id=template.client_id,
        interview_session_id=interview_session_id,
        served_files=None,
    )
    # This Job counts as still to run while it settles, so a standby it dropped is
    # replaced here; nothing is queued while the session has a spare.
    async with database.transaction() as conn:
        await request_spare_candidate(
            conn,
            client_id=template.client_id,
            product_signal_id=current.product_signal_id,
            interview_session_id=interview_session_id,
        )


def standby_disposition(
    *, built_files: Mapping[str, str] | None, ready: bool, served_files: Mapping[str, str]
) -> str:
    """What becomes of a standby spare once a version is served: `release`, `drop`, or `keep`.

    One that serves a build of exactly the served version's files becomes the
    session's next spare. One that serves any other build is dropped, so no spare
    ever holds files the session is not served. One still building is kept, and
    its Job settles it when it is done.
    """
    if not ready:
        return "keep"
    if built_files is not None and dict(built_files) == dict(served_files):
        return "release"
    return "drop"


async def settle_standby_spares(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    activation: ServerActivationContext,
    client_id: UUID,
    interview_session_id: UUID,
    served_files: Mapping[str, str] | None,
) -> bool:
    """Settle the session's standby spare against the version it is served now.

    `served_files` are the served version's file digests; `None` reads them from
    the session's route. A session served no version yet keeps its standby. A
    standby that is dropped is torn down, and the session asks for a new spare,
    which builds the served version itself. Returns whether a standby was released
    or is still building, so becomes the session's next spare.
    """
    if served_files is None:
        current = await _current_route(database, interview_session_id=interview_session_id)
        if not _serves_version(current) or current.preview_version_id is None:
            return False
        artifact = await load_artifact(database, preview_version_id=current.preview_version_id)
        served_files = {candidate.path: candidate.digest for candidate in artifact.files}
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, product_signal_id, built_files, spare_ready_at is not null as ready,
                   exists (
                     select 1 from public.preview_runtime_allocations as others
                     where others.interview_session_id = allocations.interview_session_id
                       and others.allocation_kind = 'candidate'
                       and others.preview_version_id is null
                       and others.state in ('pending', 'seeding')
                       and others.standby_draft_id is null
                   ) as spare_exists
            from public.preview_runtime_allocations as allocations
            where interview_session_id = %s and allocation_kind = 'candidate'
              and preview_version_id is null and state in ('pending', 'seeding')
              and standby_draft_id is not null
            """,
            (interview_session_id,),
        )
        standbys = await cur.fetchall()
        await cur.execute(
            """
            select exists (
              select 1 from public.jobs
              where job_type = %s and concurrency_key = %s
                and state in ('pending', 'ready', 'leased')
            ) as standby_queued
            """,
            (PREPARE_SPARE_CANDIDATE, _standby_key(interview_session_id)),
        )
        queued = await cur.fetchone()
    dropped = None
    # A standby Job still to run settles what it builds, and asks for a spare if
    # it ends with none.
    following = queued is not None and bool(queued["standby_queued"])
    for row in standbys:
        built = row["built_files"]
        disposition = standby_disposition(
            built_files=built if isinstance(built, dict) else None,
            ready=bool(row["ready"]),
            served_files=served_files,
        )
        if disposition == "release" and row["spare_exists"]:
            # The session has its next spare already; two would only cost more.
            disposition = "drop"
        allocation_id = as_uuid(row, "id")
        following = following or disposition != "drop"
        if disposition == "release":
            await _mark_standby(database, allocation_id=allocation_id, draft=None, ready=True)
        elif disposition == "drop":
            dropped = as_uuid(row, "product_signal_id")
            await tear_down_allocation(
                database,
                provider=provider,
                activation=activation,
                allocation_id=allocation_id,
                reason="standby_superseded",
            )
        _log.info(
            "preview_standby_spare_settled",
            preview_runtime_allocation_id=str(allocation_id),
            disposition=disposition,
        )
    if dropped is not None and not following:
        async with database.transaction() as conn:
            await request_spare_candidate(
                conn,
                client_id=client_id,
                product_signal_id=dropped,
                interview_session_id=interview_session_id,
            )
    return following


async def _newest_built_draft(
    database: Database, *, interview_session_id: UUID
) -> StandbyDraft | None:
    """The case's newest current draft whose build in a spare passed."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select drafts.id, drafts.files
            from public.prepared_drafts as drafts
            join public.interview_sessions as sessions
              on sessions.product_signal_id = drafts.product_signal_id
            where sessions.id = %s and drafts.state in ('drafted', 'adopted')
              and drafts.files is not null and drafts.staged_runtime_ref is not null
              and drafts.fast_checks -> 'build' ->> 'exit_code' = '0'
            order by drafts.updated_at desc
            limit 1
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None or not isinstance(row["files"], dict):
        return None
    return StandbyDraft(
        prepared_draft_id=as_uuid(row, "id"),
        files=tuple(
            CandidateFile(
                path=str(path),
                content=str(content),
                digest=f"sha256:{hashlib.sha256(str(content).encode('utf-8')).hexdigest()}",
            )
            for path, content in sorted(row["files"].items())
        ),
    )


async def _standby_spare(
    database: Database, *, provider: PreviewRuntimeProvider, interview_session_id: UUID
) -> SpareRuntime | None:
    """The session's standby spare, when it has one."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, runtime_ref, application_port, handoff_secret,
                   route_context_signing_secret, authenticated_identity
            from public.preview_runtime_allocations
            where interview_session_id = %s and allocation_kind = 'candidate'
              and preview_version_id is null and state in ('pending', 'seeding')
              and standby_draft_id is not null
            order by created_at desc
            limit 1
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    handle = await provider.attach(
        runtime_ref=as_str(row, "runtime_ref"),
        application_port=int(str(row["application_port"])),
    )
    return SpareRuntime(
        preview_runtime_allocation_id=as_uuid(row, "id"),
        handle=handle,
        upstream_url=provider.upstream_url(handle=handle),
        handoff_secret=as_str(row, "handoff_secret"),
        route_secret=as_str(row, "route_context_signing_secret"),
        authenticated_identity=as_str(row, "authenticated_identity"),
    )


async def _mark_standby(
    database: Database, *, allocation_id: UUID, draft: UUID | None, ready: bool = False
) -> None:
    """Record whose files a standby spare builds, and whether it is ready.

    `draft` set marks a ready standby; `draft` unset with `ready` releases it as the
    session's next spare, ready since it was built; neither marks it rebuilding.
    """
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_runtime_allocations
            set standby_draft_id = coalesce(%s, case when %s then null else standby_draft_id end),
                spare_ready_at = case
                  when %s then now()
                  when %s then coalesce(spare_ready_at, now())
                end,
                updated_at = now()
            where id = %s
            """,
            (draft, ready, draft is not None, ready, allocation_id),
        )


async def session_needs_spare(database: Database, *, interview_session_id: UUID) -> bool:
    """Whether the session is live, routed, and holds no spare yet."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select sessions.state = 'live' as live,
                   exists (
                     select 1 from public.preview_runtime_allocations
                     where interview_session_id = sessions.id
                       and allocation_kind = 'candidate'
                       and preview_version_id is null and state in ('pending', 'seeding')
                       and standby_draft_id is null
                   ) as spare
            from public.interview_sessions as sessions
            where sessions.id = %s
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    return row is not None and bool(row["live"]) and not row["spare"]


async def _current_route(database: Database, *, interview_session_id: UUID) -> PreviewRouteRecord:
    current = await route_for_session(database, interview_session_id=interview_session_id)
    if current is None:
        raise GuardRejectedError(
            machine="preview_route",
            trigger="prepare_spare",
            reason="the Interview Session has no committed route a spare could follow",
        )
    return current


async def _read_optional(
    provider: PreviewRuntimeProvider, handle: PreviewRuntimeHandle, path: str
) -> str | None:
    """A base file's content, or `None` when the base has no file there."""
    listed = await provider.execute(
        handle=handle,
        script=f"test -f {shlex.quote(path)}",
        working_directory="/",
        timeout_seconds=_DIRECTORY_TIMEOUT_SECONDS,
    )
    if listed.exit_code != 0:
        return None
    return (await provider.read_file(handle=handle, path=path)).decode("utf-8")


async def _restore(
    provider: PreviewRuntimeProvider,
    handle: PreviewRuntimeHandle,
    *,
    root: str,
    paths: Sequence[str],
    originals: Mapping[str, str | None],
) -> None:
    """Put back the base files an earlier staged set replaced, and remove ones it added."""
    removed = [path for path in paths if originals.get(path) is None]
    if removed:
        await provider.execute(
            handle=handle,
            script="rm -f " + " ".join(shlex.quote(f"{root}/{path}") for path in removed),
            working_directory=root,
            timeout_seconds=_DIRECTORY_TIMEOUT_SECONDS,
        )
    await asyncio.gather(
        *(
            provider.write_file(
                handle=handle, path=f"{root}/{path}", content=str(originals[path]).encode("utf-8")
            )
            for path in paths
            if originals.get(path) is not None
        )
    )


async def _write_files(
    provider: PreviewRuntimeProvider,
    handle: PreviewRuntimeHandle,
    *,
    recipe: TemplateRecipe,
    files: Sequence[CandidateFile],
) -> None:
    """Write the files and prove the installed bytes match their digests."""
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
                    "the candidate runtime refused to create the change's directories: "
                    f"{created.stdout[-500:]}"
                ),
            )
    targets = [
        f"{recipe.application_directory.rstrip('/')}/{candidate.path}" for candidate in files
    ]
    await asyncio.gather(
        *(
            provider.write_file(handle=handle, path=target, content=candidate.content.encode("utf-8"))
            for candidate, target in zip(files, targets, strict=True)
        )
    )
    installed = await asyncio.gather(
        *(provider.read_file(handle=handle, path=target) for target in targets)
    )
    for candidate, body in zip(files, installed, strict=True):
        digest = f"sha256:{hashlib.sha256(body).hexdigest()}"
        if digest != candidate.digest:
            raise GuardRejectedError(
                machine="preview_version",
                trigger="install_files",
                reason=f"{candidate.path} installed as {digest}, not the approved {candidate.digest}",
            )


def _facts(*, seed_manifest_matches: bool) -> PreviewRuntimeAllocationFacts:
    return PreviewRuntimeAllocationFacts(
        runtime_reachable=True,
        seed_manifest_matches=seed_manifest_matches,
        readiness_admits_traffic=False,
        routes_revoked=False,
        evidence_extracted=False,
    )


def _concurrency_key(interview_session_id: UUID) -> str:
    return f"preview_spare:{interview_session_id}"


def _serves_version(route: PreviewRouteRecord) -> bool:
    """Whether a route serves a Preview Version rather than the base preview."""
    return route.preview_version_id not in (None, BASE_PREVIEW_VERSION_ID)


def _standby_key(interview_session_id: UUID) -> str:
    return f"preview_standby_spare:{interview_session_id}"


def _encoded(originals: Mapping[str, str | None]) -> dict[str, str | None]:
    return {
        path: None if content is None else base64.b64encode(content.encode("utf-8")).decode("ascii")
        for path, content in originals.items()
    }


def decoded_original(value: object) -> str | None:
    """One base file as `staged_originals` stores it, or `None` for one the base lacked."""
    return None if value is None else base64.b64decode(str(value)).decode("utf-8")
