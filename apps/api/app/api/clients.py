"""Client configuration and account projections.

Three reads the demo harness and the client dashboard both need and that the
other staff routers do not cover: which clients the caller may act for, what the
active immutable configuration release pins, and which customer accounts exist
with their sensitivity and billing mapping. Nothing here writes.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict

from app.auth.dependencies import AuthenticatedIdentity, CurrentDatabase, StaffOfPath
from app.clients.configuration import ActiveRelease, load_active_release
from app.queries import (
    CustomerAccountRecord,
    StaffClientMembership,
    load_customer_accounts,
    load_staff_clients,
)

__all__ = [
    "ConfigurationReleaseResponse",
    "CustomerAccountListResponse",
    "StaffClientListResponse",
    "router",
]

router = APIRouter(prefix="/v1/clients", tags=["staff"])


class ApiModel(BaseModel):
    """Base for every response here: closed to extra fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class StaffClientListResponse(ApiModel):
    """Every client the authenticated identity holds active staff access to."""

    clients: list[StaffClientMembership]


class ConfigurationReleaseResponse(ApiModel):
    """The client's active immutable configuration release."""

    release: ActiveRelease


class CustomerAccountListResponse(ApiModel):
    """The client's customer accounts, their sensitivity, and their mapping."""

    customer_accounts: list[CustomerAccountRecord]


@router.get("", response_model=StaffClientListResponse, response_model_exclude_none=True)
async def list_staff_clients(
    identity: AuthenticatedIdentity, database: CurrentDatabase
) -> StaffClientListResponse:
    """Return the clients this identity may act for, so a caller can start."""
    return StaffClientListResponse(
        clients=await load_staff_clients(database, auth_user_id=identity.auth_user_id)
    )


@router.get(
    "/{client_id}/configuration",
    response_model=ConfigurationReleaseResponse,
    response_model_exclude_none=True,
)
async def get_configuration(
    staff: StaffOfPath,
    database: CurrentDatabase,
    client_id: Annotated[UUID, Path()],
) -> ConfigurationReleaseResponse:
    """Return the active configuration release, which is what new work pins."""
    return ConfigurationReleaseResponse(
        release=await load_active_release(database, client_id=staff.client_id)
    )


@router.get(
    "/{client_id}/customer-accounts",
    response_model=CustomerAccountListResponse,
    response_model_exclude_none=True,
)
async def list_customer_accounts(
    staff: StaffOfPath,
    database: CurrentDatabase,
    client_id: Annotated[UUID, Path()],
) -> CustomerAccountListResponse:
    """Return the client's customer accounts with sensitivity and billing mapping."""
    return CustomerAccountListResponse(
        customer_accounts=await load_customer_accounts(database, client_id=staff.client_id)
    )
