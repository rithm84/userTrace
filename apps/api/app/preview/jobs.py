"""The ``preview.*`` Jobs and their handlers.

Five durable responsibilities live here. Session preparation consumes the Job an
invitation claim commits: it claims a warm runtime, or creates one from the
template, and routes the session's origin to it. A preparation that fails enters
[bounded recovery](./session_recovery.py), whose runs retry it until the
recovery deadline and then close the session as blocked. Warm-pool maintenance is queued
by the ``utrace-preview-pool-scan`` cron scan whenever a current template is short
of its target, under a concurrency key so a second scan cannot double-provision.
Teardown destroys one allocation after its routes and credentials are revoked.
Idle-candidate expiry destroys candidate runtimes whose route revision is no
longer active; their artifact and seed manifest stay durable, so they can be
rebuilt exactly.

Each handler opens its own provider client and HTTP client for the run and closes
them again, because a Job Run is the unit of work the lease covers. It also
installs the ingestion-backed observation readiness adapter: a worker has no
request lifespan to install ports for it, and a preview whose observation nobody
can read is not routable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import httpx

from app.adapters.wire import install_ingestion_adapters
from app.connectors.vercel_sandboxes import sandbox_grant_from_environment
from app.db.pool import Database
from app.execution.jobs import ClaimedJob
from app.ingestion.settings import get_ingestion_settings
from app.intake.jobs import PREPARE_BASE_ALLOCATION
from app.logging import get_logger
from app.ports import PortRegistry
from app.preview.activation import ServerActivationContext
from app.preview.candidate import expire_idle_candidates
from app.preview.config import PreviewSettings, get_preview_settings
from app.preview.ports import ObservationReadinessPort
from app.preview.provisioning import (
    PreparedSessionPreview,
    maintain_warm_pool,
    prepare_session_preview,
)
from app.preview.records import RuntimeTemplate
from app.preview.recovery import recover_route_after_restart, withdraw_readiness_for_restart
from app.preview.routing import route_for_session
from app.preview.runtime import PreviewRuntimeProvider
from app.preview.session_recovery import (
    PREPARATION_FAILURES,
    RECOVER_SESSION_PREVIEW,
    RECOVERY_WINDOW,
    close_unrecoverable_session,
    recovery_open,
    schedule_recovery,
)
from app.preview.teardown import tear_down_allocation
from app.preview.template import load_current_template, load_recipe
from app.settings import get_settings

__all__ = [
    "EXPIRE_IDLE_CANDIDATES",
    "MAINTAIN_WARM_POOL",
    "TEAR_DOWN_ALLOCATION",
    "build_preview_handlers",
    "preview_session",
]

_log = get_logger(__name__)

MAINTAIN_WARM_POOL = "preview.maintain_warm_pool"
TEAR_DOWN_ALLOCATION = "preview.tear_down_allocation"
EXPIRE_IDLE_CANDIDATES = "preview.expire_idle_candidates"

@dataclass(frozen=True, slots=True)
class PreviewSession:
    """The clients, policy, and ports one preview Job Run operates with."""

    provider: PreviewRuntimeProvider
    http: httpx.AsyncClient
    settings: PreviewSettings
    activation: ServerActivationContext
    observation: ObservationReadinessPort
    origin_suffix: str


@asynccontextmanager
async def preview_session(database: Database) -> AsyncIterator[PreviewSession]:
    """Open the provider, HTTP client, and ports for one preview Job Run."""
    settings = get_preview_settings()
    control_plane = get_settings()
    ingestion = get_ingestion_settings()
    ports = install_ingestion_adapters(
        PortRegistry(voice=None, observation=None, evidence=None),
        database=database,
    )
    async with (
        PreviewRuntimeProvider(grant=sandbox_grant_from_environment()) as provider,
        httpx.AsyncClient(
            timeout=settings.readiness_timeout_seconds, follow_redirects=False
        ) as http,
    ):
        yield PreviewSession(
            provider=provider,
            http=http,
            settings=settings,
            activation=ServerActivationContext.build(
                settings=settings,
                lane_signing_key=control_plane.lane_signing_key,
                service_token=control_plane.front_door_service_token,
                manifest_key_id=ingestion.server_manifest_trust_anchor_key_id,
                manifest_public_key=ingestion.manifest_trust_anchor_key(),
            ),
            observation=ports.require_observation(),
            origin_suffix=control_plane.preview_origin_suffix,
        )


async def prepare_session_preview_job(database: Database, claimed: ClaimedJob) -> None:
    """Prepare the base preview an invitation claim asked for.

    A failure pins the recovery deadline and commits the first recovery run
    before this run records it.
    """
    product_signal_id = UUID(str(claimed.input["product_signal_id"]))
    interview_session_id = UUID(str(claimed.input["interview_session_id"]))
    async with preview_session(database) as session:
        template = await load_current_template(database, client_id=claimed.client_id)
        try:
            prepared = await prepare_session_preview(
                database,
                provider=session.provider,
                client=session.http,
                settings=session.settings,
                origin_suffix=session.origin_suffix,
                client_id=claimed.client_id,
                product_signal_id=product_signal_id,
                interview_session_id=interview_session_id,
                template=template,
                activation=session.activation,
                observation_port=session.observation,
            )
        except PREPARATION_FAILURES as failure:
            await schedule_recovery(
                database,
                client_id=claimed.client_id,
                product_signal_id=product_signal_id,
                interview_session_id=interview_session_id,
                recover_by=datetime.now(tz=UTC) + RECOVERY_WINDOW,
                failure=str(failure),
            )
            raise
    _log.info(
        "preview_session_preparation_completed",
        client_id=str(claimed.client_id),
        preview_runtime_allocation_id=str(prepared.pins.preview_runtime_allocation_id),
        route_revision=prepared.route.route_revision,
    )


async def _recover(
    database: Database,
    session: PreviewSession,
    *,
    template: RuntimeTemplate,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
) -> PreparedSessionPreview:
    """Retry readiness on the committed route, or finish preparing when none was committed."""
    if await route_for_session(database, interview_session_id=interview_session_id) is None:
        return await prepare_session_preview(
            database,
            provider=session.provider,
            client=session.http,
            settings=session.settings,
            origin_suffix=session.origin_suffix,
            client_id=client_id,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
            template=template,
            activation=session.activation,
            observation_port=session.observation,
        )
    await withdraw_readiness_for_restart(
        database, interview_session_id=interview_session_id, reason="session_preview_recovery"
    )
    return await recover_route_after_restart(
        database,
        provider=session.provider,
        client=session.http,
        settings=session.settings,
        interview_session_id=interview_session_id,
        template=template,
        observation_port=session.observation,
    )


async def recover_session_preview_job(database: Database, claimed: ClaimedJob) -> None:
    """Retry a session preview that failed to prepare, or close it past its deadline."""
    product_signal_id = UUID(str(claimed.input["product_signal_id"]))
    interview_session_id = UUID(str(claimed.input["interview_session_id"]))
    recover_by = datetime.fromisoformat(str(claimed.input["recover_by"]))
    if not await recovery_open(database, interview_session_id=interview_session_id):
        _log.info(
            "session_preview_recovery_not_needed",
            client_id=str(claimed.client_id),
            interview_session_id=str(interview_session_id),
        )
        return
    if datetime.now(tz=UTC) >= recover_by:
        outcome = await close_unrecoverable_session(
            database,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
        )
        _log.warning(
            "session_preview_recovery_expired",
            client_id=str(claimed.client_id),
            interview_session_id=str(interview_session_id),
            recover_by=recover_by.isoformat(),
            outcome=outcome,
        )
        return
    async with preview_session(database) as session:
        template = await load_current_template(database, client_id=claimed.client_id)
        try:
            prepared = await _recover(
                database,
                session,
                template=template,
                client_id=claimed.client_id,
                product_signal_id=product_signal_id,
                interview_session_id=interview_session_id,
            )
        except PREPARATION_FAILURES as failure:
            await schedule_recovery(
                database,
                client_id=claimed.client_id,
                product_signal_id=product_signal_id,
                interview_session_id=interview_session_id,
                recover_by=recover_by,
                failure=str(failure),
            )
            raise
    _log.info(
        "session_preview_recovered",
        client_id=str(claimed.client_id),
        interview_session_id=str(interview_session_id),
        route_revision=prepared.route.route_revision,
    )


async def maintain_warm_pool_job(database: Database, claimed: ClaimedJob) -> None:
    """Provision the runtimes one template is short of its target."""
    template_id = UUID(str(claimed.input["preview_runtime_template_id"]))
    shortfall = int(str(claimed.input["shortfall"]))
    async with preview_session(database) as session:
        template = await load_current_template(database, client_id=claimed.client_id)
        if template.id != template_id:
            _log.info(
                "preview_warm_pool_scan_stale",
                client_id=str(claimed.client_id),
                scanned_template_id=str(template_id),
                current_template_id=str(template.id),
            )
            return
        recipe = await load_recipe(database, template_id=template.id)
        provisioned = await maintain_warm_pool(
            database,
            provider=session.provider,
            client=session.http,
            template=template,
            recipe=recipe,
            settings=session.settings,
            activation=session.activation,
            origin_suffix=session.origin_suffix,
            shortfall=shortfall,
        )
    _log.info(
        "preview_warm_pool_maintained",
        client_id=str(claimed.client_id),
        preview_runtime_template_id=str(template_id),
        provisioned=len(provisioned),
    )


async def tear_down_allocation_job(database: Database, claimed: ClaimedJob) -> None:
    """Revoke, extract, and destroy one preview runtime."""
    allocation_id = UUID(str(claimed.input["preview_runtime_allocation_id"]))
    reason = str(claimed.input["reason"])
    async with preview_session(database) as session:
        await tear_down_allocation(
            database,
            provider=session.provider,
            activation=session.activation,
            allocation_id=allocation_id,
            reason=reason,
        )


async def expire_idle_candidates_job(database: Database, claimed: ClaimedJob) -> None:
    """Destroy candidate runtimes no route revision points at any more."""
    idle_seconds = int(str(claimed.input["idle_seconds"]))
    async with preview_session(database) as session:
        expired = await expire_idle_candidates(
            database,
            provider=session.provider,
            activation=session.activation,
            client_id=claimed.client_id,
            idle_seconds=idle_seconds,
        )
    _log.info(
        "preview_idle_candidates_expired",
        client_id=str(claimed.client_id),
        expired=len(expired),
    )


def build_preview_handlers() -> Mapping[
    str, Callable[[Database, ClaimedJob], Awaitable[None]]
]:
    """Return the preview area's Job handlers."""
    return {
        PREPARE_BASE_ALLOCATION: prepare_session_preview_job,
        RECOVER_SESSION_PREVIEW: recover_session_preview_job,
        MAINTAIN_WARM_POOL: maintain_warm_pool_job,
        TEAR_DOWN_ALLOCATION: tear_down_allocation_job,
        EXPIRE_IDLE_CANDIDATES: expire_idle_candidates_job,
    }
