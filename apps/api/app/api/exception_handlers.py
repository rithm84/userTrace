"""The error contract.

Every deliberate refusal becomes one typed body with the same shape, so callers
can distinguish a stale revision from a policy denial from an unwired capability
without parsing prose. Nothing is remapped to a generic failure.
"""

from collections.abc import Awaitable, Callable, Mapping

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.responses import ErrorResponse
from app.errors import (
    AggregateNotFoundError,
    AuthorizationError,
    BudgetExhaustedError,
    ControlPlaneError,
    GuardRejectedError,
    IllegalTransitionError,
    LaneCredentialError,
    LeaseLostError,
    NotYetWiredError,
    ReferenceFormatError,
    RevisionConflictError,
)
from app.ingestion.errors import (
    ArtifactIntegrityError,
    BackpressureError,
    ClockQualityError,
    IntegrityViolationError,
    NeverCollectViolationError,
    PayloadLimitError,
    QueryAuthorizationError,
    SourceValidationError,
    TelemetryAuthenticationError,
    WindowFrozenError,
)
from app.logging import get_logger

__all__ = ["install_exception_handlers"]

_log = get_logger(__name__)

_STATUS_BY_ERROR: Mapping[type[ControlPlaneError], int] = {
    AggregateNotFoundError: 404,
    AuthorizationError: 403,
    BudgetExhaustedError: 409,
    GuardRejectedError: 409,
    IllegalTransitionError: 409,
    LaneCredentialError: 401,
    LeaseLostError: 409,
    NotYetWiredError: 501,
    ReferenceFormatError: 400,
    RevisionConflictError: 409,
    # Telemetry ingestion refusals. A subclass is matched through its base, so
    # IdentityReuseError answers 409 through IntegrityViolationError while its
    # own name still reaches the caller.
    ArtifactIntegrityError: 500,
    BackpressureError: 429,
    ClockQualityError: 400,
    IntegrityViolationError: 409,
    NeverCollectViolationError: 400,
    PayloadLimitError: 413,
    QueryAuthorizationError: 403,
    SourceValidationError: 400,
    TelemetryAuthenticationError: 401,
    WindowFrozenError: 409,
}


def _subject(error: ControlPlaneError) -> str | None:
    match error:
        case AggregateNotFoundError():
            return f"{error.table}:{error.row_id}"
        case RevisionConflictError():
            return f"{error.table}:{error.row_id}"
        case AuthorizationError():
            return error.subject
        case LaneCredentialError():
            return error.audience
        case NotYetWiredError():
            return error.owning_workstream
        case IntegrityViolationError():
            return f"{error.identity_kind}:{error.identity_value}"
        case QueryAuthorizationError():
            return error.subject
        case TelemetryAuthenticationError():
            return "telemetry_credential"
        case _:
            return None


def install_exception_handlers(app: FastAPI) -> None:
    """Register one handler per deliberate refusal type."""
    for error_type, status in _STATUS_BY_ERROR.items():
        app.add_exception_handler(error_type, _make_handler(status))


def _make_handler(
    status: int,
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    async def handler(request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, ControlPlaneError):
            raise exc
        body = ErrorResponse(
            error=type(exc).__name__, detail=str(exc), subject=_subject(exc)
        )
        _log.info(
            "request_refused",
            path=request.url.path,
            method=request.method,
            status=status,
            error=body.error,
            subject=body.subject,
        )
        return JSONResponse(
            status_code=status,
            content=body.model_dump(mode="json", exclude_none=True),
        )

    return handler
