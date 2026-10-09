"""Provisioning a warm runtime, and preparing a claimed one for a session.

Two flows live here.

**Warm-pool maintenance** creates a runtime from the pinned template, waits for
the product to answer its health contract, verifies the dataset the image carries
against the approved seed identity through the runtime's own database, and only
then lets the allocation become claimable. A runtime that fails any of that is
destroyed, never repaired in place.

**Session preparation** claims one warm runtime for an Interview Session, or
creates one from the template when none is ready, gives it the session's opaque
origin, commits the first route revision to the runtime's application port URL,
and probes the whole path end to end. Readiness is withheld until every gating
probe admits traffic.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx

from app.db.pool import Database
from app.db.rows import as_datetime
from app.errors import GuardRejectedError
from app.logging import get_logger
from app.preview.activation import (
    ServerActivationContext,
    install_server_activation,
    restart_observed_runtime,
)
from app.preview.config import PreviewSettings
from app.preview.machines import PreviewRuntimeAllocationFacts
from app.preview.pool import (
    acquire_allocation,
    claim_base_allocation,
    commit_allocation_transition,
    insert_allocation,
    load_pins,
    mint_secret,
    require_current_fencing_token,
    session_allocation_id,
)
from app.preview.ports import ObservationReadinessPort
from app.preview.readiness import (
    front_door_claim,
    provisioning_target,
    record_readiness,
    run_provisioning_readiness,
    run_session_readiness,
)
from app.preview.recipe import TemplateRecipe
from app.preview.records import (
    AllocationPins,
    PoolRole,
    PreviewRouteRecord,
    ReadinessReport,
    RuntimeTemplate,
    SeedTarget,
)
from app.preview.routing import (
    commit_route,
    mint_origin_host,
)
from app.preview.runtime import PreviewRuntimeHandle, PreviewRuntimeProvider
from app.preview.seeding import InstalledDataset, install_dataset, verify_dataset
from app.preview.teardown import tear_down_allocation
from app.preview.template import (
    build_runtime_env,
    load_recipe,
    mint_runtime_ref,
    runtime_network_policy,
)

__all__ = [
    "PreparedSessionPreview",
    "maintain_warm_pool",
    "prepare_session_preview",
    "provision_warm_runtime",
    "reseed_allocation",
    "wait_for_health",
    "withheld_reason",
]

_log = get_logger(__name__)

# A new version is announced the moment it answers, so the poll is short: a two
# second poll left the participant waiting up to two seconds after it was up.
_HEALTH_POLL_SECONDS = 0.25
# A candidate reinstalls the approved dataset at every start, so the window has
# to cover a full reseed of the fixture, not just a process restart: six minutes.
_HEALTH_ATTEMPTS = 1440


async def wait_for_health(
    *,
    client: httpx.AsyncClient,
    upstream_url: str,
    recipe: TemplateRecipe,
    public_preview_host: str,
) -> None:
    """Wait until the runtime answers its health contract through its port URL.

    The health route is the only surface the front door reaches without a route
    context, so it is also the only one that can be polled before a route exists.
    """
    target = provisioning_target(
        upstream_url=upstream_url, public_preview_host=public_preview_host
    )
    last = "no response"
    for _ in range(_HEALTH_ATTEMPTS):
        try:
            response = await client.get(
                target.url(recipe.health_path), headers=target.headers({})
            )
        except httpx.HTTPError as error:
            last = f"{type(error).__name__}: {error}"
        else:
            if response.status_code == 200:
                return
            last = f"status {response.status_code}: {response.text[:500]}"
        await asyncio.sleep(_HEALTH_POLL_SECONDS)
    raise GuardRejectedError(
        machine="preview_runtime_allocation",
        trigger="wait_for_health",
        reason=(
            "the preview runtime did not answer its health contract within "
            f"{int(_HEALTH_ATTEMPTS * _HEALTH_POLL_SECONDS)} seconds: {last}"
        ),
    )


async def provision_warm_runtime(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    client: httpx.AsyncClient,
    template: RuntimeTemplate,
    recipe: TemplateRecipe,
    settings: PreviewSettings,
    activation: ServerActivationContext,
    origin_suffix: str,
) -> UUID:
    """Create one unclaimed warm runtime and prove it before it becomes claimable.

    The runtime is created with its own handoff and route-context secrets, which
    are what bind its authentication handoff and its route authority to this one
    sandbox. They are recorded on the allocation because every later route
    revision pointing at this runtime has to carry the same values.
    """
    # A warm runtime has no session origin yet; a reserved label under the same
    # suffix identifies it in the probe records. No route revision is ever
    # committed for it, so the name is never routable.
    provisioning_host = f"provisioning.{origin_suffix}"
    handoff_secret = mint_secret()
    route_secret = mint_secret()
    runtime_expires_at = datetime.now(tz=UTC) + timedelta(
        seconds=settings.execution_time_limit_seconds
    )
    handle = await provider.create_runtime(
        runtime_ref=mint_runtime_ref(purpose="preview"),
        snapshot_id=template.snapshot_id,
        application_port=template.application_port,
        env_vars=build_runtime_env(
            seed_target=SeedTarget.BASE,
            handoff_secret=handoff_secret,
            route_context_signing_secret=route_secret,
        ),
        egress=runtime_network_policy(observation_hosts=activation.endpoints.hosts()),
        execution_time_limit_seconds=settings.execution_time_limit_seconds,
        vcpus=recipe.vcpus,
        supervisor_command=recipe.supervisor_command,
        application_directory=recipe.application_directory,
    )
    allocation_id = await insert_allocation(
        database,
        template=template,
        pool_role=PoolRole.WARM_POOL,
        allocation_kind="base",
        product_signal_id=None,
        preview_version_id=None,
        runtime_ref=handle.runtime_ref,
        origin_host=None,
        handoff_secret=handoff_secret,
        runtime_expires_at=runtime_expires_at,
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_runtime_allocations
            set route_context_signing_secret = %s, updated_at = now()
            where id = %s
            """,
            (route_secret, allocation_id),
        )

    try:
        pins, _ = await acquire_allocation(database, allocation_id=allocation_id)
        async with database.transaction() as conn:
            await commit_allocation_transition(
                conn,
                client_id=template.client_id,
                allocation_id=allocation_id,
                trigger="begin_seeding",
                facts=_facts(
                    runtime_reachable=True,
                    seed_manifest_matches=False,
                    readiness_admits_traffic=False,
                ),
                mutation={},
                event_type="preview_runtime_allocation_seeding",
                payload={"runtime_ref": handle.runtime_ref},
                reason_code="warm_pool_provisioning",
                correlation_id=None,
                additional_locks=(),
            )

        upstream_url = provider.upstream_url(handle=handle)
        await wait_for_health(
            client=client,
            upstream_url=upstream_url,
            recipe=recipe,
            public_preview_host=provisioning_host,
        )
        await verify_dataset(
            provider=provider,
            handle=handle,
            recipe=recipe,
            seed=template.seed,
            expected_target=SeedTarget.BASE,
        )
        report = await run_provisioning_readiness(
            client=client,
            target=provisioning_target(
                upstream_url=upstream_url,
                public_preview_host=provisioning_host,
            ),
            recipe=recipe,
            pins=pins,
            seed_target=SeedTarget.BASE,
            gating_probes=template.gating_probes,
        )
        await record_readiness(
            database,
            client_id=template.client_id,
            report=report,
            preview_route_id=None,
        )
        if not report.ready:
            raise GuardRejectedError(
                machine="preview_runtime_allocation",
                trigger="mark_ready",
                reason=withheld_reason(report),
            )
        async with database.transaction() as conn:
            await commit_allocation_transition(
                conn,
                client_id=template.client_id,
                allocation_id=allocation_id,
                trigger="mark_ready",
                facts=_facts(
                    runtime_reachable=True,
                    seed_manifest_matches=True,
                    readiness_admits_traffic=True,
                ),
                mutation={},
                event_type="preview_runtime_allocation_ready",
                payload={"runtime_ref": handle.runtime_ref},
                reason_code="warm_pool_ready",
                correlation_id=None,
                additional_locks=(),
            )
    except BaseException:
        await tear_down_allocation(
            database,
            provider=provider,
            activation=activation,
            allocation_id=allocation_id,
            reason="warm_pool_provisioning_failed",
        )
        raise
    _log.info(
        "preview_warm_runtime_ready",
        client_id=str(template.client_id),
        preview_runtime_allocation_id=str(allocation_id),
        runtime_ref=handle.runtime_ref,
    )
    return allocation_id


async def maintain_warm_pool(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    client: httpx.AsyncClient,
    template: RuntimeTemplate,
    recipe: TemplateRecipe,
    settings: PreviewSettings,
    activation: ServerActivationContext,
    origin_suffix: str,
    shortfall: int,
) -> tuple[UUID, ...]:
    """Provision up to ``shortfall`` warm runtimes, one at a time.

    Provisioning is serial on purpose: a template that is broken fails on the
    first runtime instead of burning the client's whole sandbox quota before
    anyone reads the error.
    """
    provisioned: list[UUID] = []
    for _ in range(max(shortfall, 0)):
        provisioned.append(
            await provision_warm_runtime(
                database,
                provider=provider,
                client=client,
                template=template,
                recipe=recipe,
                settings=settings,
                activation=activation,
                origin_suffix=origin_suffix,
            )
        )
    return tuple(provisioned)


async def _renew_for_session(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    handle: PreviewRuntimeHandle,
    allocation_id: UUID,
    runtime_expires_at: datetime,
    execution_time_limit_seconds: int,
) -> None:
    """Give the session the runtime's full execution limit, counted from now.

    A warm runtime's sandbox has been counting down since it was created, so a
    session would otherwise inherit only what was left of it.
    """
    extension = int(
        (
            datetime.now(tz=UTC)
            + timedelta(seconds=execution_time_limit_seconds)
            - runtime_expires_at
        ).total_seconds()
    )
    if extension < 1:
        return
    await provider.extend_runtime(handle=handle, seconds=extension)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_runtime_allocations
            set runtime_expires_at = %s, updated_at = now()
            where id = %s
            """,
            (runtime_expires_at + timedelta(seconds=extension), allocation_id),
        )
    _log.info(
        "preview_runtime_renewed_for_session",
        preview_runtime_allocation_id=str(allocation_id),
        extended_seconds=extension,
    )


@dataclass(frozen=True, slots=True)
class PreparedSessionPreview:
    """The route and readiness a claimed runtime reached for one session."""

    pins: AllocationPins
    route: PreviewRouteRecord
    report: ReadinessReport


async def prepare_session_preview(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    client: httpx.AsyncClient,
    settings: PreviewSettings,
    origin_suffix: str,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
    template: RuntimeTemplate,
    activation: ServerActivationContext,
    observation_port: ObservationReadinessPort,
) -> PreparedSessionPreview:
    """Claim a warm runtime, prepare it for the session, and prove the path.

    When no warm runtime is ready, one is created from the template for this
    session first; greeting and problem discussion continue meanwhile under the
    session readiness boundary. Claiming is what makes the allocation a prepared
    preview: it gains the session's opaque origin, and only then can its Server
    SDK receive a credential bound to both. The supervisor is restarted once with
    that credential installed, so the product process the route points at is the
    one whose SDK activated.

    A session binds exactly one base allocation, so preparing again after a
    failure finishes the runtime the session already holds instead of claiming
    another.
    """
    recipe = await load_recipe(database, template_id=template.id)
    bound = await session_allocation_id(
        database, interview_session_id=interview_session_id
    )
    pins: AllocationPins | None
    if bound is not None:
        pins = await load_pins(database, allocation_id=bound)
    else:
        pins = await claim_base_allocation(
            database,
            interview_session_id=interview_session_id,
            product_signal_id=product_signal_id,
            client_id=client_id,
            template_id=template.id,
        )
        if pins is None:
            await provision_warm_runtime(
                database,
                provider=provider,
                client=client,
                template=template,
                recipe=recipe,
                settings=settings,
                activation=activation,
                origin_suffix=origin_suffix,
            )
            pins = await claim_base_allocation(
                database,
                interview_session_id=interview_session_id,
                product_signal_id=product_signal_id,
                client_id=client_id,
                template_id=template.id,
            )
    if pins is None:
        raise GuardRejectedError(
            machine="preview_runtime_allocation",
            trigger="claim",
            reason="the runtime provisioned for this session was claimed by another one",
        )
    origin_host = mint_origin_host(origin_suffix=origin_suffix)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_runtime_allocations
            set origin_host = %s, revision = revision + 1, updated_at = now()
            where id = %s
            """,
            (origin_host, pins.preview_runtime_allocation_id),
        )
        await cur.execute(
            """
            select handoff_secret, route_context_signing_secret, runtime_expires_at
            from public.preview_runtime_allocations where id = %s
            """,
            (pins.preview_runtime_allocation_id,),
        )
        secrets_row = await cur.fetchone()
    if secrets_row is None:
        raise GuardRejectedError(
            machine="preview_runtime_allocation",
            trigger="prepare_session_preview",
            reason="the claimed allocation carries no injected runtime secrets",
        )
    handoff_secret = str(secrets_row["handoff_secret"])
    route_secret = str(secrets_row["route_context_signing_secret"])

    handle = await provider.attach(
        runtime_ref=pins.runtime_ref, application_port=pins.application_port
    )
    await _renew_for_session(
        database,
        provider=provider,
        handle=handle,
        allocation_id=pins.preview_runtime_allocation_id,
        runtime_expires_at=as_datetime(secrets_row, "runtime_expires_at"),
        execution_time_limit_seconds=settings.execution_time_limit_seconds,
    )
    await install_server_activation(
        provider=provider,
        handle=handle,
        endpoints=activation.endpoints,
        application_directory=recipe.application_directory,
        lane_signing_key=activation.lane_signing_key,
        client_id=client_id,
        interview_session_id=interview_session_id,
        preview_runtime_allocation_id=pins.preview_runtime_allocation_id,
        public_preview_host=origin_host,
    )
    await restart_observed_runtime(
        database,
        provider=provider,
        commands=activation.commands,
        client_id=client_id,
        preview_runtime_allocation_id=pins.preview_runtime_allocation_id,
        handle=handle,
        supervisor_command=recipe.supervisor_command,
        application_directory=recipe.application_directory,
    )
    upstream_url = provider.upstream_url(handle=handle)
    await wait_for_health(
        client=client,
        upstream_url=upstream_url,
        recipe=recipe,
        public_preview_host=origin_host,
    )
    route = await commit_route(
        database,
        client_id=client_id,
        product_signal_id=product_signal_id,
        interview_session_id=interview_session_id,
        preview_runtime_allocation_id=pins.preview_runtime_allocation_id,
        preview_version_id=None,
        public_preview_host=origin_host,
        upstream_port_url=upstream_url,
        application_port=pins.application_port,
        route_context_signing_secret=route_secret,
        ttl_seconds=settings.route_ttl_seconds,
    )
    report = await run_session_readiness(
        client=client,
        record=route,
        recipe=recipe,
        pins=pins,
        seed_target=SeedTarget.BASE,
        handoff_secret=handoff_secret,
        gating_probes=template.gating_probes,
        observation_port=observation_port,
        door_claim=(
            front_door_claim(database, record=route) if settings.public_path_enabled else None
        ),
    )
    await record_readiness(
        database,
        client_id=client_id,
        report=report,
        preview_route_id=route.preview_route_id,
    )
    if not report.ready:
        raise GuardRejectedError(
            machine="preview_route",
            trigger="commit",
            reason=withheld_reason(report),
        )
    _log.info(
        "preview_session_prepared",
        client_id=str(client_id),
        interview_session_id=str(interview_session_id),
        public_preview_host=origin_host,
        route_revision=route.route_revision,
        prepared_at=datetime.now(tz=UTC).isoformat(),
    )
    return PreparedSessionPreview(pins=pins, route=route, report=report)


async def reseed_allocation(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    allocation_id: UUID,
    template: RuntimeTemplate,
    seed_target: SeedTarget,
) -> InstalledDataset:
    """Reinstall the approved dataset into a running allocation and re-verify it.

    The Data Seeding Contract permits a bounded fresh reseed when the approved
    contract already covers the needed records and transforms. It reinstalls the
    same approved identity — never a wider selection — and the installed manifest
    is read back independently afterwards, so a drifted dataset is a refusal
    rather than a quietly different preview.

    Acquiring the allocation first mints a new fencing token, so a worker that
    still holds the previous one cannot reseed underneath this one.
    """
    pins, fencing_token = await acquire_allocation(
        database, allocation_id=allocation_id
    )
    await require_current_fencing_token(
        database, allocation_id=allocation_id, fencing_token=fencing_token
    )
    recipe = await load_recipe(database, template_id=template.id)
    handle = await provider.attach(
        runtime_ref=pins.runtime_ref, application_port=pins.application_port
    )
    installed = await install_dataset(
        provider=provider,
        handle=handle,
        recipe=recipe,
        seed=template.seed,
        seed_target=seed_target,
    )
    _log.info(
        "preview_allocation_reseeded",
        preview_runtime_allocation_id=str(allocation_id),
        content_hash=installed.content_hash,
        seed_target=installed.seed_target.value,
    )
    return installed


def withheld_reason(report: ReadinessReport) -> str:
    """Render the gating probes that are withholding readiness."""
    withheld = report.withheld()
    if not withheld:
        return "readiness was withheld without a recorded probe"
    return "; ".join(
        f"{result.probe.value} is {result.outcome.value}: {dict(result.detail)}"
        for result in withheld
    )


def _facts(
    *,
    runtime_reachable: bool,
    seed_manifest_matches: bool,
    readiness_admits_traffic: bool,
) -> PreviewRuntimeAllocationFacts:
    """Build allocation facts for a provisioning transition."""
    return PreviewRuntimeAllocationFacts(
        runtime_reachable=runtime_reachable,
        seed_manifest_matches=seed_manifest_matches,
        readiness_admits_traffic=readiness_admits_traffic,
        routes_revoked=False,
        evidence_extracted=False,
    )
