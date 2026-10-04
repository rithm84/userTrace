"""Outreach Offers: owner authorization and the per-offer disposition read.

Only the client owner may authorize or increase the issuance allowance. An admin
can read it and configure offers within it, but authorizing the ceiling is the
owner's alone, so this route refuses an admin with an explicit reason rather than
silently accepting a request it will not honour.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from utrace_contracts.domain.lifecycle import ClientMemberRole

from app.auth.dependencies import CurrentDatabase, StaffOfPath
from app.errors import AggregateNotFoundError, AuthorizationError
from app.offers.budget import authorize_allowance, read_allowance
from app.offers.read_model import OfferDisposition, load_offer, load_offers_view

__all__ = ["router"]

router = APIRouter(prefix="/v1/clients", tags=["staff"])


class ApiModel(BaseModel):
    """Base for every response here: closed to extra fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AuthorizeBudgetRequest(BaseModel):
    """The owner's authorization of an issuance ceiling, in integer minor units."""

    model_config = ConfigDict(extra="forbid")

    currency: str = Field(min_length=3, max_length=3)
    authorized_minor_units: int = Field(ge=0)


class AllowanceResponse(ApiModel):
    """The client's authorized, reserved, earned, and remaining allowance."""

    currency: str | None
    authorized_minor_units: int
    reserved_minor_units: int
    earned_minor_units: int
    remaining_minor_units: int


class OfferDispositionResponse(ApiModel):
    """One offer's disposition, issuance status, and reconciled application."""

    offer: OfferDisposition


class OffersViewResponse(ApiModel):
    """The allowance and every offer written against it."""

    currency: str | None
    authorized_minor_units: int
    reserved_minor_units: int
    earned_minor_units: int
    remaining_minor_units: int
    offers: list[OfferDisposition]


@router.post(
    "/{client_id}/offers/budget",
    response_model=AllowanceResponse,
    response_model_exclude_none=True,
)
async def authorize_offer_budget(
    staff: StaffOfPath,
    database: CurrentDatabase,
    body: AuthorizeBudgetRequest,
) -> AllowanceResponse:
    """Authorize or increase the client's invoice-credit issuance allowance."""
    if staff.role is not ClientMemberRole.OWNER:
        raise AuthorizationError(
            subject=f"issuance allowance for client {staff.client_id}",
            reason="only the client owner may authorize or increase the budget",
        )
    allowance = await authorize_allowance(
        database,
        client_id=staff.client_id,
        currency=body.currency,
        authorized_minor_units=body.authorized_minor_units,
        authorized_by_auth_user_id=staff.auth_user_id,
    )
    return AllowanceResponse(
        currency=allowance.currency,
        authorized_minor_units=allowance.authorized_minor_units,
        reserved_minor_units=allowance.reserved_minor_units,
        earned_minor_units=allowance.earned_minor_units,
        remaining_minor_units=allowance.remaining_minor_units,
    )


@router.get(
    "/{client_id}/offers/budget",
    response_model=AllowanceResponse,
    response_model_exclude_none=True,
)
async def get_offer_budget(
    staff: StaffOfPath,
    database: CurrentDatabase,
    currency: Annotated[str | None, Query(min_length=3, max_length=3)] = None,
) -> AllowanceResponse:
    """Return the client's current issuance allowance."""
    allowance = await read_allowance(
        database, client_id=staff.client_id, currency=currency
    )
    return AllowanceResponse(
        currency=allowance.currency,
        authorized_minor_units=allowance.authorized_minor_units,
        reserved_minor_units=allowance.reserved_minor_units,
        earned_minor_units=allowance.earned_minor_units,
        remaining_minor_units=allowance.remaining_minor_units,
    )


@router.get(
    "/{client_id}/offers/dispositions",
    response_model=OffersViewResponse,
    response_model_exclude_none=True,
)
async def list_offer_dispositions(
    staff: StaffOfPath,
    database: CurrentDatabase,
    currency: Annotated[str | None, Query(min_length=3, max_length=3)] = None,
) -> OffersViewResponse:
    """Return the allowance and every offer's disposition and issuance status."""
    view = await load_offers_view(
        database, client_id=staff.client_id, currency=currency
    )
    return OffersViewResponse(
        currency=view.currency,
        authorized_minor_units=view.authorized_minor_units,
        reserved_minor_units=view.reserved_minor_units,
        earned_minor_units=view.earned_minor_units,
        remaining_minor_units=view.remaining_minor_units,
        offers=list(view.offers),
    )


@router.get(
    "/{client_id}/offers/{outreach_offer_id}",
    response_model=OfferDispositionResponse,
    response_model_exclude_none=True,
)
async def get_offer_disposition(
    staff: StaffOfPath,
    database: CurrentDatabase,
    outreach_offer_id: Annotated[UUID, Path()],
) -> OfferDispositionResponse:
    """Return one offer's disposition, scoped to its client."""
    offer = await load_offer(
        database, client_id=staff.client_id, outreach_offer_id=outreach_offer_id
    )
    if offer is None:
        raise AggregateNotFoundError(
            table="outreach_offers", row_id=outreach_offer_id
        )
    return OfferDispositionResponse(offer=offer)
