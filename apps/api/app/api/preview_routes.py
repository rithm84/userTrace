"""``GET /v1/preview/routes/{host}``: one committed route revision.

The preview front door calls this on every request with its own service token
plus the participant's origin-bound session credential. It never caches the
answer, so the response carries everything one hop needs: the runtime's
application port URL, the secret the door signs route context with, the revision that has
to still be current, and when the mapping stops being valid.

``POST /v1/preview/routes/{host}/product-sign-in`` is the product handoff: once
the claim has admitted the participant's browser, the door asks for a one-time
token that signs them into the client product as the allocation's prepared
identity, and posts it to the sign-in path the pinned template declares.

Refusals use the typed preview denial shape rather than the control plane's
general error body, because the door distinguishes "this session is over" from
"uTrace is broken" and shows the participant a different surface for each.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Response

from app.auth.dependencies import CurrentDatabase, CurrentSettings, FrontDoorService
from app.db.pool import Database
from app.db.rows import as_str
from app.errors import ControlPlaneError
from app.logging import get_logger
from app.preview.handoff import mint_product_handoff, verify_session_credential
from app.preview.pool import load_pins, load_runtime_secret
from app.preview.records import PreviewRouteRecord, RouteReadinessState
from app.preview.routing import route_for_host
from app.preview.template import load_recipe
from app.preview.wire import preview_denial, preview_json

__all__ = ["router"]

_log = get_logger(__name__)

router = APIRouter(prefix="/v1/preview", tags=["front-door"])

PRODUCT_SIGN_IN_SCHEMA_VERSION = "utrace_preview_product_sign_in_v1"

#: The door posts the token as soon as it receives it, so a minute is ample.
_PRODUCT_SIGN_IN_LIFETIME_SECONDS = 60


@router.get(
    "/routes/{host}",
    response_model=None,
    responses={
        200: {"description": "The one committed route revision for this origin."},
        401: {"description": "A typed preview denial."},
        404: {"description": "A typed preview denial."},
        410: {"description": "A typed preview denial."},
    },
)
async def get_route(
    service: FrontDoorService,
    database: CurrentDatabase,
    settings: CurrentSettings,
    host: Annotated[str, Path(min_length=3, max_length=253)],
    x_utrace_session_credential: Annotated[str | None, Header()] = None,
) -> Response:
    """Resolve one preview origin to its current committed route revision."""
    record = await _authorized_route(
        database,
        signing_key=settings.lane_signing_key,
        public_preview_host=host.lower(),
        session_credential=x_utrace_session_credential,
    )
    if isinstance(record, Response):
        return record

    _log.info(
        "preview_route_resolved",
        client_id=str(record.client_id),
        public_preview_host=record.public_preview_host,
        route_revision=record.route_revision,
        readiness_state=record.readiness_state.value,
    )
    return preview_json(status=200, payload=_wire(record))


@router.post(
    "/routes/{host}/product-sign-in",
    response_model=None,
    responses={
        200: {"description": "A one-time token for the product's sign-in path."},
        401: {"description": "A typed preview denial."},
        404: {"description": "A typed preview denial."},
        410: {"description": "A typed preview denial."},
    },
)
async def mint_product_sign_in(
    service: FrontDoorService,
    database: CurrentDatabase,
    settings: CurrentSettings,
    host: Annotated[str, Path(min_length=3, max_length=253)],
    x_utrace_session_credential: Annotated[str | None, Header()] = None,
) -> Response:
    """Mint the token that signs the participant into the routed runtime's product."""
    record = await _authorized_route(
        database,
        signing_key=settings.lane_signing_key,
        public_preview_host=host.lower(),
        session_credential=x_utrace_session_credential,
    )
    if isinstance(record, Response):
        return record

    # The route names the runtime the door forwards to, and each runtime was
    # created with its own secret, so a candidate switch signs in to the candidate.
    allocation_id = record.preview_runtime_allocation_id
    pins = await load_pins(database, allocation_id=allocation_id)
    recipe = await load_recipe(database, template_id=pins.preview_runtime_template_id)
    token = mint_product_handoff(
        handoff_secret=await load_runtime_secret(
            database, allocation_id=allocation_id, column="handoff_secret"
        ),
        audience_host=record.public_preview_host,
        subject_email=pins.authenticated_identity,
        lifetime_seconds=_PRODUCT_SIGN_IN_LIFETIME_SECONDS,
    )
    _log.info(
        "preview_product_sign_in_minted",
        client_id=str(record.client_id),
        interview_session_id=str(record.interview_session_id),
        preview_runtime_allocation_id=str(allocation_id),
        token_id=str(token.token_id),
    )
    return preview_json(
        status=200,
        payload={
            "schema_version": PRODUCT_SIGN_IN_SCHEMA_VERSION,
            "path": recipe.handoff_path,
            "token": token.token,
        },
    )


async def _authorized_route(
    database: Database,
    *,
    signing_key: str,
    public_preview_host: str,
    session_credential: str | None,
) -> PreviewRouteRecord | Response:
    """Return the origin's route when the session credential authorizes it, else the denial."""
    if session_credential is None:
        return preview_denial(status=401, reason="unauthorized_session")
    try:
        claims = verify_session_credential(
            signing_key=signing_key,
            credential=session_credential,
            public_preview_host=public_preview_host,
        )
    except ControlPlaneError:
        return preview_denial(status=401, reason="unauthorized_session")

    record = await route_for_host(database, public_preview_host=public_preview_host)
    if record is None:
        return preview_denial(status=404, reason="unknown_host")
    if record.client_id != claims.client_id or (
        record.interview_session_id != claims.interview_session_id
    ):
        return preview_denial(status=403, reason="unauthorized_session")
    if await _session_closed(database, interview_session_id=record.interview_session_id):
        return preview_denial(status=410, reason="session_ended")
    return record


async def _session_closed(
    database: Database, *, interview_session_id: UUID
) -> bool:
    """Whether the bound Interview Session has ended."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state from public.interview_sessions where id = %s",
            (interview_session_id,),
        )
        row = await cur.fetchone()
    return row is None or as_str(row, "state") == "closed"


def _wire(record: PreviewRouteRecord) -> dict[str, object]:
    """Render the route record in the front door's published shape."""
    return {
        "schema_version": record.schema_version,
        "preview_route_id": str(record.preview_route_id),
        "client_id": str(record.client_id),
        "interview_session_id": str(record.interview_session_id),
        "product_signal_id": str(record.product_signal_id),
        "preview_version_id": str(record.preview_version_id),
        "preview_runtime_allocation_id": str(record.preview_runtime_allocation_id),
        "public_preview_host": record.public_preview_host,
        "route_revision": record.route_revision,
        "readiness_state": RouteReadinessState(record.readiness_state).value,
        "upstream": {
            "port_url": record.upstream.port_url,
            "application_port": record.upstream.application_port,
        },
        "route_context_signing_secret": record.route_context_signing_secret,
        # The door attaches this as `x-utrace-preview-entry`. It is null until
        # the telemetry-ingestion stream issues one for this runtime, because a
        # route record must not carry an entry token nobody activated.
        "preview_entry_token": record.preview_entry_token,
        "expires_at": record.expires_at.isoformat().replace("+00:00", "Z"),
    }
