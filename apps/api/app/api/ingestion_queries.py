"""The typed read-only telemetry query surface, over HTTP.

Every shape takes the same scope: authenticated client, purpose, exact session or
frozen evidence window, allowed source classes, bounds, projection, limit, and
cursor. Nothing here mutates state, promotes evidence, starts work, or broadens
its own authorization.

Staff read these through their client membership; the interview and validation
controllers call the same functions in process.

Contract: docs/arch-components/telemetry-and-evidence.md#query-and-subscription-contracts
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict

from app.auth.dependencies import CurrentDatabase, StaffOfPath
from app.evidence.queries import (
    QueryScope,
    TelemetryQueryResult,
    before_and_after_comparison,
    browser_request_and_server_chain,
    delivery_gap_report,
    product_state_projection,
    registered_operation_errors,
    session_timeline,
    target_history,
    trace_with_completeness,
)
from app.ingestion.errors import QueryAuthorizationError

__all__ = ["router"]

router = APIRouter(prefix="/v1/clients/{client_id}/telemetry", tags=["telemetry"])


class QueryRequest(BaseModel):
    """The scope a caller submits. The path's client is the authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: QueryScope


class ComparisonRequest(QueryRequest):
    """A before-and-after validation comparison over two frozen windows."""

    before_evidence_window_id: UUID
    after_evidence_window_id: UUID


def _bound(scope: QueryScope, client_id: UUID) -> QueryScope:
    """Refuse a scope whose client disagrees with the authenticated path."""
    if scope.client_id != client_id:
        raise QueryAuthorizationError(
            subject=f"client {client_id}",
            reason="the query scope names a different client",
        )
    return scope


@router.post("/product-state", response_model=TelemetryQueryResult)
async def read_product_state(
    staff: StaffOfPath,
    database: CurrentDatabase,
    client_id: Annotated[UUID, Path()],
    body: QueryRequest,
) -> TelemetryQueryResult:
    """Return the current product-state projection for one session."""
    return await product_state_projection(database, _bound(body.scope, client_id))


@router.post("/session-timeline", response_model=TelemetryQueryResult)
async def read_session_timeline(
    staff: StaffOfPath,
    database: CurrentDatabase,
    client_id: Annotated[UUID, Path()],
    body: QueryRequest,
) -> TelemetryQueryResult:
    """Return one bounded session timeline."""
    return await session_timeline(database, _bound(body.scope, client_id))


@router.post("/traces/{trace_id}", response_model=TelemetryQueryResult)
async def read_trace(
    staff: StaffOfPath,
    database: CurrentDatabase,
    client_id: Annotated[UUID, Path()],
    trace_id: Annotated[str, Path(pattern="^[0-9a-f]{32}$")],
    body: QueryRequest,
) -> TelemetryQueryResult:
    """Return one trace with its completeness."""
    return await trace_with_completeness(
        database, _bound(body.scope, client_id), trace_id=trace_id
    )


@router.post("/requests/{browser_request_id}", response_model=TelemetryQueryResult)
async def read_request_chain(
    staff: StaffOfPath,
    database: CurrentDatabase,
    client_id: Annotated[UUID, Path()],
    browser_request_id: Annotated[str, Path(min_length=1, max_length=128)],
    body: QueryRequest,
) -> TelemetryQueryResult:
    """Return one browser request and the server chain beneath it."""
    return await browser_request_and_server_chain(
        database, _bound(body.scope, client_id), browser_request_id=browser_request_id
    )


@router.post("/targets/{target_id}", response_model=TelemetryQueryResult)
async def read_target_history(
    staff: StaffOfPath,
    database: CurrentDatabase,
    client_id: Annotated[UUID, Path()],
    target_id: Annotated[str, Path(min_length=1, max_length=128)],
    body: QueryRequest,
) -> TelemetryQueryResult:
    """Return the exact history of one registered target."""
    return await target_history(
        database, _bound(body.scope, client_id), target_id=target_id
    )


@router.post("/operation-errors", response_model=TelemetryQueryResult)
async def read_operation_errors(
    staff: StaffOfPath,
    database: CurrentDatabase,
    client_id: Annotated[UUID, Path()],
    body: QueryRequest,
) -> TelemetryQueryResult:
    """Return errors on registered operations."""
    return await registered_operation_errors(database, _bound(body.scope, client_id))


@router.post("/validation-comparison", response_model=TelemetryQueryResult)
async def read_validation_comparison(
    staff: StaffOfPath,
    database: CurrentDatabase,
    client_id: Annotated[UUID, Path()],
    body: ComparisonRequest,
) -> TelemetryQueryResult:
    """Compare the cited evidence of two frozen windows."""
    return await before_and_after_comparison(
        database,
        _bound(body.scope, client_id),
        before_window_id=body.before_evidence_window_id,
        after_window_id=body.after_evidence_window_id,
    )


@router.post("/delivery-gaps", response_model=TelemetryQueryResult)
async def read_delivery_gaps(
    staff: StaffOfPath,
    database: CurrentDatabase,
    client_id: Annotated[UUID, Path()],
    body: QueryRequest,
) -> TelemetryQueryResult:
    """Return every explicit delivery gap and unacknowledged tail for one session."""
    return await delivery_gap_report(database, _bound(body.scope, client_id))
