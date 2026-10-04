"""The two public telemetry ingress edges.

Browser observations arrive on a dedicated authenticated WebSocket; server spans
arrive as authenticated OTLP/HTTP binary Protobuf. Neither endpoint accepts
product queries, browser commands, domain messages, media, general logs, or
metrics.

Both edges write a durable receipt before they answer. Backpressure is explicit:
the browser receives flow control without advancing its acknowledgement, and the
server receives a retryable status with ``Retry-After``.

Contract: docs/arch-components/telemetry-and-evidence.md#ingress-topology-and-ownership
"""

from fastapi import APIRouter, Request, Response, WebSocket

from app.auth.dependencies import CurrentDatabase
from app.db.pool import Database
from app.ingestion.browser_gateway import BrowserIngestSession
from app.ingestion.errors import (
    BackpressureError,
    IngestionError,
    TelemetryAuthenticationError,
)
from app.ingestion.otlp import full_success_body
from app.ingestion.otlp_receiver import accept_export
from app.ingestion.rate_budget import RateBudget
from app.ingestion.wiring import require_artifact_store, require_server_rate_budget
from app.logging import get_logger

__all__ = ["router"]

_log = get_logger(__name__)

router = APIRouter(prefix="/v1/ingest", tags=["ingestion"])

_STATUS_BY_CODE = {
    "credential_rejected": 401,
    "duplicate_conflict": 409,
    "payload_limit_violation": 413,
    "quota_backpressure": 429,
    "schema_incompatible": 400,
    "unsafe_data_detected": 400,
    "clock_quality_failure": 400,
    "artifact_integrity_failure": 500,
}

_BEARER = "bearer"


def _websocket_database(websocket: WebSocket) -> Database:
    """Return the process-wide connector for a WebSocket route.

    The HTTP dependency takes a ``Request``; a WebSocket route reads the same
    application state directly.
    """
    database = websocket.app.state.database
    if not isinstance(database, Database):
        raise TypeError("the application was assembled without a database connector")
    return database


@router.websocket("/browser")
async def ingest_browser(websocket: WebSocket) -> None:
    """Carry structured browser observations from the shared browser runtime."""
    session = BrowserIngestSession(
        websocket,
        database=_websocket_database(websocket),
        artifacts=require_artifact_store(websocket.app),
    )
    await session.run()


@router.post("/otlp/v1/traces")
async def ingest_otlp_traces(
    request: Request, database: CurrentDatabase
) -> Response:
    """Accept correlated OpenTelemetry spans from the preview Server SDK.

    A success is a full success: the response body is an empty OTLP
    ``ExportTraceServiceResponse``. uTrace never populates ``partial_success``,
    because a partially accepted batch would leave the trace ambiguously
    incomplete.
    """
    # The credential is read before any resource is built, so an unauthenticated
    # export is refused without the process having to hold ingestion configuration.
    token = _bearer_token(request.headers.get("authorization"))
    artifacts = require_artifact_store(request.app)
    budget: RateBudget = require_server_rate_budget(request.app)
    body = await request.body()
    try:
        accepted = await accept_export(
            database,
            artifacts=artifacts,
            token=token,
            headers={key.lower(): value for key, value in request.headers.items()},
            body=body,
            content_encoding=request.headers.get("content-encoding"),
            rate_budget=budget,
        )
    except BackpressureError as error:
        return Response(
            status_code=429,
            headers={"retry-after": str(max(error.retry_after_ms // 1_000, 1))},
            content=b"",
        )
    except IngestionError as error:
        return _refusal(error)
    _log.info(
        "otlp_export_accepted",
        activation_id=str(accepted.activation_id),
        ingestion_receipt_id=str(accepted.ingestion_receipt_id),
        stored_spans=accepted.stored_spans,
        duplicate_spans=accepted.duplicate_spans,
        replayed=accepted.replayed,
    )
    return Response(
        status_code=200,
        media_type="application/x-protobuf",
        content=full_success_body(),
    )


def _bearer_token(authorization: str | None) -> str:
    """Read the write-only credential from the standard Authorization header."""
    if authorization is None:
        raise TelemetryAuthenticationError("the Authorization header is missing")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != _BEARER or len(token) == 0:
        raise TelemetryAuthenticationError("expected a Bearer telemetry credential")
    return token


def _refusal(error: IngestionError) -> Response:
    """Refuse the whole batch with a stable code and no product value."""
    status = _STATUS_BY_CODE.get(error.code, 400)
    _log.warning(
        "otlp_export_refused",
        code=error.code,
        stage=error.stage,
        status=status,
        retry_classification=error.retry_classification,
    )
    return Response(
        status_code=status,
        media_type="application/json",
        content=f'{{"code":"{error.code}","stage":"{error.stage}"}}'.encode(),
    )
