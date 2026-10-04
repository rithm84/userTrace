"""Staff reads of a Case File's amendments and delivery.

The sealed core is served by the kernel's Case File route. These are the parts
that are deliberately *not* in it: the separately identified amendments and the
verified GitHub delivery identities.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict
from utrace_contracts.domain.lifecycle import DeliveryState

from app.auth.dependencies import CurrentDatabase, StaffOfPath
from app.errors import AggregateNotFoundError
from app.outputs.amendments import CaseFileAmendment, load_amendments
from app.outputs.assembly import load_case_file_row
from app.outputs.delivery import load_delivery

__all__ = ["router"]

router = APIRouter(prefix="/v1/clients", tags=["staff"])


class ApiModel(BaseModel):
    """Base for every response here: closed to extra fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CaseFileAmendmentsResponse(ApiModel):
    """The corrections recorded beside one Case File."""

    case_file_id: UUID
    amendments: list[CaseFileAmendment]


class DeliveryResponse(ApiModel):
    """The one authorized draft pull request bound to the validated candidate."""

    delivery_id: UUID
    state: DeliveryState
    repository_ref: str
    branch_ref: str
    base_branch: str
    base_revision: str
    validated_source_commit: str
    pull_request_ref: str | None
    pull_request_number: int | None
    verified_head_sha: str | None
    blocker_reason: str | None
    case_file_url: str


async def _scoped_case_file(
    database: CurrentDatabase, *, client_id: UUID, case_file_id: UUID
) -> UUID:
    """Return the case's identity after rechecking the object's client linkage."""
    async with database.connection() as conn:
        case_file = await load_case_file_row(conn, case_file_id=case_file_id)
    if case_file.client_id != client_id:
        raise AggregateNotFoundError(table="case_files", row_id=case_file_id)
    return case_file.product_signal_id


@router.get(
    "/{client_id}/case-files/{case_file_id}/amendments",
    response_model=CaseFileAmendmentsResponse,
    response_model_exclude_none=True,
)
async def get_case_file_amendments(
    staff: StaffOfPath,
    database: CurrentDatabase,
    case_file_id: Annotated[UUID, Path()],
) -> CaseFileAmendmentsResponse:
    """Return the separately identified corrections beside one Case File."""
    await _scoped_case_file(
        database, client_id=staff.client_id, case_file_id=case_file_id
    )
    return CaseFileAmendmentsResponse(
        case_file_id=case_file_id,
        amendments=list(
            await load_amendments(
                database, client_id=staff.client_id, case_file_id=case_file_id
            )
        ),
    )


@router.get(
    "/{client_id}/case-files/{case_file_id}/delivery",
    response_model=DeliveryResponse,
    response_model_exclude_none=True,
)
async def get_case_file_delivery(
    staff: StaffOfPath,
    database: CurrentDatabase,
    case_file_id: Annotated[UUID, Path()],
) -> DeliveryResponse:
    """Return the verified delivery identities bound to the validated candidate."""
    async with database.connection() as conn:
        case_file = await load_case_file_row(conn, case_file_id=case_file_id)
        if case_file.client_id != staff.client_id or case_file.delivery_id is None:
            raise AggregateNotFoundError(table="deliveries", row_id=case_file_id)
        delivery = await load_delivery(conn, delivery_id=case_file.delivery_id)
    return DeliveryResponse(
        delivery_id=delivery.id,
        state=delivery.state,
        repository_ref=delivery.repository_ref,
        branch_ref=delivery.branch_ref,
        base_branch=delivery.base_branch,
        base_revision=delivery.base_revision,
        validated_source_commit=delivery.head_commit,
        pull_request_ref=delivery.pull_request_ref,
        pull_request_number=delivery.pull_request_number,
        verified_head_sha=delivery.verified_head_sha,
        blocker_reason=delivery.blocker_reason,
        case_file_url=delivery.case_file_url,
    )
