"""Withdrawing a session route, recovering it, and confirming observation.

Three things happen to an already-prepared session, and none of them provision
anything. A runtime restart withdraws readiness first, and a new revision is
committed only once recovery has re-proved the complete path; the withdrawn
revision is superseded rather than restored.

Observation confirmation is the third. The preview stream commits a route
before any browser can reach it, so the browser and correlation answers at
commit time are necessarily "not yet"; they are asked again here, once the
session is live and the participant's own traffic exists.
"""

from __future__ import annotations

from uuid import UUID

import httpx

from app.db.pool import Database
from app.errors import GuardRejectedError
from app.logging import get_logger
from app.preview.config import PreviewSettings
from app.preview.pool import load_pins
from app.preview.ports import ObservationReadinessPort
from app.preview.provisioning import (
    PreparedSessionPreview,
    wait_for_health,
    withheld_reason,
)
from app.preview.readiness import (
    front_door_claim,
    record_readiness,
    run_session_observation,
    run_session_readiness,
)
from app.preview.records import (
    PreviewRouteRecord,
    ReadinessReport,
    RouteReadinessState,
    RuntimeTemplate,
    SeedTarget,
)
from app.preview.routing import (
    commit_route,
    route_for_session,
    set_route_readiness,
)
from app.preview.runtime import PreviewRuntimeProvider
from app.preview.template import load_recipe

__all__ = [
    "confirm_session_observation",
    "recover_route_after_restart",
    "withdraw_readiness_for_restart",
]

_log = get_logger(__name__)


async def withdraw_readiness_for_restart(
    database: Database, *, interview_session_id: UUID, reason: str
) -> PreviewRouteRecord:
    """Withdraw a session route's durable readiness before its runtime restarts.

    Readiness is withdrawn first: the front door serves the reconnecting surface
    and the session tab refuses to hand out a claim until a new revision is
    committed.
    """
    route = await route_for_session(
        database, interview_session_id=interview_session_id
    )
    if route is None:
        raise GuardRejectedError(
            machine="preview_route",
            trigger="withdraw_readiness",
            reason="the Interview Session has no committed route",
        )
    await set_route_readiness(
        database,
        preview_route_id=route.preview_route_id,
        readiness_state=RouteReadinessState.RECONNECTING,
        reason=reason,
    )
    return route


async def recover_route_after_restart(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    client: httpx.AsyncClient,
    settings: PreviewSettings,
    interview_session_id: UUID,
    template: RuntimeTemplate,
    observation_port: ObservationReadinessPort,
) -> PreparedSessionPreview:
    """Reprove the whole path after a restart and commit a new route revision.

    Recovery reproves the complete path and commits a new revision before traffic
    resumes. The withdrawn revision is superseded rather than restored.
    """
    withdrawn = await route_for_session(
        database, interview_session_id=interview_session_id
    )
    if withdrawn is None:
        raise GuardRejectedError(
            machine="preview_route",
            trigger="recover",
            reason="the Interview Session has no committed route to recover",
        )
    pins = await load_pins(
        database, allocation_id=withdrawn.preview_runtime_allocation_id
    )
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select handoff_secret, route_context_signing_secret
            from public.preview_runtime_allocations where id = %s
            """,
            (pins.preview_runtime_allocation_id,),
        )
        secrets_row = await cur.fetchone()
    if secrets_row is None:
        raise GuardRejectedError(
            machine="preview_runtime_allocation",
            trigger="recover",
            reason="the allocation carries no injected runtime secrets",
        )
    handoff_secret = str(secrets_row["handoff_secret"])
    route_secret = str(secrets_row["route_context_signing_secret"])

    handle = await provider.attach(
        runtime_ref=pins.runtime_ref, application_port=pins.application_port
    )
    upstream_url = provider.upstream_url(handle=handle)
    recipe = await load_recipe(database, template_id=template.id)
    await wait_for_health(
        client=client,
        upstream_url=upstream_url,
        recipe=recipe,
        public_preview_host=withdrawn.public_preview_host,
    )
    route = await commit_route(
        database,
        client_id=withdrawn.client_id,
        product_signal_id=withdrawn.product_signal_id,
        interview_session_id=interview_session_id,
        preview_runtime_allocation_id=pins.preview_runtime_allocation_id,
        preview_version_id=None,
        public_preview_host=withdrawn.public_preview_host,
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
        client_id=withdrawn.client_id,
        report=report,
        preview_route_id=route.preview_route_id,
    )
    if not report.ready:
        await set_route_readiness(
            database,
            preview_route_id=route.preview_route_id,
            readiness_state=RouteReadinessState.RECONNECTING,
            reason="recovery_readiness_withheld",
        )
        raise GuardRejectedError(
            machine="preview_route", trigger="recover", reason=withheld_reason(report)
        )
    _log.info(
        "preview_route_recovered",
        interview_session_id=str(interview_session_id),
        route_revision=route.route_revision,
    )
    return PreparedSessionPreview(pins=pins, route=route, report=report)


async def confirm_session_observation(
    database: Database,
    *,
    interview_session_id: UUID,
    observation_port: ObservationReadinessPort,
) -> ReadinessReport:
    """Re-answer the observation probes for a session whose route already carries traffic.

    The preview stream commits a route before any browser can reach it, so the
    browser and correlation answers at commit time are necessarily "not yet".
    This is how the Interview Session's observation readiness becomes a recorded
    fact: the probes are run again against the live route and appended to the
    same durable readiness record.
    """
    route = await route_for_session(
        database, interview_session_id=interview_session_id
    )
    if route is None:
        raise GuardRejectedError(
            machine="preview_route",
            trigger="confirm_observation",
            reason="the Interview Session has no committed route to observe",
        )
    report = await run_session_observation(
        record=route, observation_port=observation_port
    )
    await record_readiness(
        database,
        client_id=route.client_id,
        report=report,
        preview_route_id=route.preview_route_id,
    )
    return report
