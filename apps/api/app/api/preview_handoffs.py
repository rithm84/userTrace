"""``POST /v1/preview/handoffs/{code}/exchange``: the one-time front-door claim.

The session tab asks for a claim URL, the participant opens it, and the front
door presents the code here with its own service token. The exchange returns the
origin-bound session credential the door stores in its ``__Host-`` cookie and
presents on every later route resolution.

Single use is durable: the code's digest is recorded when it is minted and the
consuming update matches only an unconsumed row, so a replay loses the race
across processes rather than within one. A bare or leaked claim URL therefore has
authority exactly once, for one origin, for at most its lifetime.
"""

from typing import Annotated

from fastapi import APIRouter, Path, Response
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import CurrentDatabase, CurrentSettings, FrontDoorService
from app.errors import ControlPlaneError
from app.logging import get_logger
from app.preview.config import get_preview_settings
from app.preview.handoff import consume_front_door_code
from app.preview.wire import preview_denial, preview_json

__all__ = ["router"]

_log = get_logger(__name__)

router = APIRouter(prefix="/v1/preview", tags=["front-door"])

EXCHANGE_REQUEST_SCHEMA_VERSION = "utrace_preview_handoff_exchange_request_v1"
EXCHANGE_SCHEMA_VERSION = "utrace_preview_handoff_exchange_v1"


class HandoffExchangeRequest(BaseModel):
    """The body the front door sends with a claim code."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(pattern=f"^{EXCHANGE_REQUEST_SCHEMA_VERSION}$")
    public_preview_host: str = Field(max_length=253)


@router.post(
    "/handoffs/{code}/exchange",
    response_model=None,
    responses={
        200: {"description": "The origin-bound session credential."},
        401: {"description": "A typed preview denial."},
        409: {"description": "A typed preview denial."},
    },
)
async def exchange_handoff(
    service: FrontDoorService,
    database: CurrentDatabase,
    settings: CurrentSettings,
    code: Annotated[str, Path(min_length=24, max_length=512)],
    body: HandoffExchangeRequest,
) -> Response:
    """Exchange one one-time claim code for the door's session credential."""
    public_preview_host = body.public_preview_host.lower()
    preview = get_preview_settings()
    try:
        exchanged = await consume_front_door_code(
            database,
            signing_key=settings.lane_signing_key,
            code=code,
            public_preview_host=public_preview_host,
            session_credential_lifetime_seconds=(
                preview.session_credential_ttl_seconds
            ),
        )
    except ControlPlaneError:
        # The door is told only that this code carries no authority: which of
        # unknown, consumed, expired, or bound elsewhere it was is the control
        # plane's record, not a fact to publish on a preview origin.
        return preview_denial(status=401, reason="unknown_handoff_code")

    _log.info(
        "preview_handoff_exchanged",
        client_id=str(exchanged.client_id),
        interview_session_id=str(exchanged.interview_session_id),
        public_preview_host=exchanged.public_preview_host,
    )
    return preview_json(
        status=200,
        payload={
            "schema_version": EXCHANGE_SCHEMA_VERSION,
            "public_preview_host": exchanged.public_preview_host,
            "client_id": str(exchanged.client_id),
            "interview_session_id": str(exchanged.interview_session_id),
            "session_credential": {
                "value": exchanged.session_credential,
                "expires_at": exchanged.session_credential_expires_at.isoformat().replace(
                    "+00:00", "Z"
                ),
            },
        },
    )
