"""Staff routes.

Every route resolves an active ``client_members`` row for the requested client
before it reads anything. Staff may read authorized projections and invoke the
audited, deny-only Stop control; they cannot edit, approve, or resume a case.
"""

from collections.abc import Mapping
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from utrace_contracts.domain.records import ClientMemberRecord, ProductSignalRecord

from app.api.responses import (
    CaseDetailResponse,
    CaseFileDetailResponse,
    CaseFileListResponse,
    CaseListResponse,
    CaseOutreachResponse,
    CaseOverview,
    CaseStopResponse,
    ClientMemberView,
    ClientResponse,
    MemberListResponse,
    OfferListResponse,
    PreviewRuntimeAllocationListResponse,
    ReferenceResolutionResponse,
)
from app.auth.dependencies import CurrentDatabase, StaffOfPath
from app.dashboard import (
    REFERENCE_KIND_CLIENT_USER,
    REFERENCE_KIND_CUSTOMER_ACCOUNT,
    CaseConcern,
    load_case_concerns,
    load_case_stop,
    load_deliveries,
    load_member_emails,
    resolve_references,
)
from app.dashboard_case import (
    load_case_cleanup,
    load_case_outreach,
    load_case_sessions,
    load_preview_runtime_allocations,
    load_preview_versions,
)
from app.errors import AuthorizationError
from app.lifecycle.case_stop import stop_case
from app.lifecycle.product_signal import NONTERMINAL_STATES
from app.offers.read_model import load_offers_view
from app.outputs.sections import CaseFileCore
from app.queries import (
    load_attempts,
    load_case,
    load_case_file,
    load_case_file_for_case,
    load_case_files,
    load_cases,
    load_client,
    load_gate_decisions,
    load_members,
    load_offer_allowance,
    load_offers,
    load_validation_attempts,
)

__all__ = ["router"]

router = APIRouter(prefix="/v1/clients", tags=["staff"])

CLOSURE_DEADLINE_SECONDS = 7 * 24 * 60 * 60
MAXIMUM_REFERENCES = 200
"""One page of a dashboard list never holds more references than this."""


class StopCaseRequest(BaseModel):
    """The audited reason an owner or admin gives for stopping a case."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


@router.get("/{client_id}", response_model=ClientResponse, response_model_exclude_none=True)
async def get_client(
    staff: StaffOfPath,
    database: CurrentDatabase,
    client_id: Annotated[UUID, Path()],
) -> ClientResponse:
    """Return the client and the caller's own membership."""
    client = await load_client(database, client_id=client_id)
    members = await load_members(database, client_id=client_id)
    mine = [member for member in members if member.id == staff.client_member_id]
    if not mine:
        raise AuthorizationError(
            subject=f"client {client_id}", reason="the resolved membership disappeared"
        )
    return ClientResponse(client=client, membership=mine[0])


@router.get(
    "/{client_id}/references",
    response_model=ReferenceResolutionResponse,
    response_model_exclude_none=True,
)
async def resolve_client_references(
    staff: StaffOfPath,
    database: CurrentDatabase,
    ref: Annotated[list[str], Query(min_length=1, max_length=MAXIMUM_REFERENCES)],
) -> ReferenceResolutionResponse:
    """Resolve opaque client-scoped references to the labels staff may see.

    A published Case File core, a case and an Outreach Offer all name the
    originating user and their customer organization by client-scoped reference
    rather than by name. This is the authorized dashboard resolving them; a
    reference uTrace holds no recorded name for comes back without a label, and
    the dashboard then shows the reference itself.
    """
    return ReferenceResolutionResponse(
        references=await resolve_references(
            database, client_id=staff.client_id, refs=ref
        )
    )


@router.get("/{client_id}/cases", response_model=CaseListResponse, response_model_exclude_none=True)
async def list_cases(staff: StaffOfPath, database: CurrentDatabase) -> CaseListResponse:
    """Return every case for the client, newest first."""
    cases = await load_cases(database, client_id=staff.client_id)
    concerns = await load_case_concerns(database, client_id=staff.client_id)
    return CaseListResponse(
        cases=cases,
        overviews=[_overview(case, concerns=concerns) for case in cases],
    )


def _overview(
    case: ProductSignalRecord,
    *,
    concerns: Mapping[UUID, CaseConcern],
) -> CaseOverview:
    """Compose the list view of one case from its established concern."""
    return CaseOverview(
        product_signal_id=case.id,
        concern=concerns.get(case.id),
        originating_user_ref=(
            f"{REFERENCE_KIND_CLIENT_USER}:{case.originating_client_user_id}"
        ),
        customer_account_ref=(
            f"{REFERENCE_KIND_CUSTOMER_ACCOUNT}:{case.originating_customer_account_id}"
        ),
    )


@router.get(
    "/{client_id}/cases/{product_signal_id}",
    response_model=CaseDetailResponse,
    response_model_exclude_none=True,
)
async def get_case(
    staff: StaffOfPath,
    database: CurrentDatabase,
    product_signal_id: Annotated[UUID, Path()],
) -> CaseDetailResponse:
    """Return one case with its gate decisions, attempts, and validation record."""
    case = await load_case(database, client_id=staff.client_id, product_signal_id=product_signal_id)
    case_file = await load_case_file_for_case(database, product_signal_id=product_signal_id)
    deliveries = await load_deliveries(database, client_id=staff.client_id)
    offers = await load_offers_view(database, client_id=staff.client_id, currency=None)
    return CaseDetailResponse(
        case=case,
        overview=_overview(
            case,
            concerns=await load_case_concerns(database, client_id=staff.client_id),
        ),
        gate_decisions=await load_gate_decisions(database, product_signal_id=product_signal_id),
        implementation_attempts=await load_attempts(database, product_signal_id=product_signal_id),
        validation_attempts=await load_validation_attempts(
            database, product_signal_id=product_signal_id
        ),
        interview_sessions=await load_case_sessions(
            database, client_id=staff.client_id, product_signal_id=product_signal_id
        ),
        preview_versions=await load_preview_versions(
            database, client_id=staff.client_id, product_signal_id=product_signal_id
        ),
        cleanup=await load_case_cleanup(
            database, client_id=staff.client_id, product_signal_id=product_signal_id
        ),
        offer=next(
            (item for item in offers.offers if item.product_signal_id == product_signal_id),
            None,
        ),
        stop=await load_case_stop(
            database, client_id=staff.client_id, product_signal_id=product_signal_id
        ),
        stoppable=case.state.value in NONTERMINAL_STATES,
        case_file=case_file,
        delivery=None if case_file is None else deliveries.get(case_file.id),
    )


@router.get(
    "/{client_id}/cases/{product_signal_id}/outreach-attempts",
    response_model=CaseOutreachResponse,
    response_model_exclude_none=True,
)
async def get_case_outreach(
    staff: StaffOfPath,
    database: CurrentDatabase,
    product_signal_id: Annotated[UUID, Path()],
) -> CaseOutreachResponse:
    """Return how uTrace reached the case's originating user.

    Reading the case first is what scopes this to the caller's tenant: an
    Outreach Attempt of another client is not a case this membership can name.
    """
    case = await load_case(
        database, client_id=staff.client_id, product_signal_id=product_signal_id
    )
    return CaseOutreachResponse(
        product_signal_id=case.id,
        attempts=await load_case_outreach(
            database, client_id=staff.client_id, product_signal_id=case.id
        ),
    )


@router.get(
    "/{client_id}/cases/{product_signal_id}/preview-runtime-allocations",
    response_model=PreviewRuntimeAllocationListResponse,
    response_model_exclude_none=True,
)
async def get_case_preview_runtime_allocations(
    staff: StaffOfPath,
    database: CurrentDatabase,
    product_signal_id: Annotated[UUID, Path()],
) -> PreviewRuntimeAllocationListResponse:
    """Return the Preview Runtime Allocations the case claimed and their state."""
    case = await load_case(
        database, client_id=staff.client_id, product_signal_id=product_signal_id
    )
    return PreviewRuntimeAllocationListResponse(
        product_signal_id=case.id,
        allocations=await load_preview_runtime_allocations(
            database, client_id=staff.client_id, product_signal_id=case.id
        ),
    )


@router.post(
    "/{client_id}/cases/{product_signal_id}/stop",
    response_model=CaseStopResponse,
    response_model_exclude_none=True,
)
async def stop_case_route(
    staff: StaffOfPath,
    database: CurrentDatabase,
    product_signal_id: Annotated[UUID, Path()],
    body: StopCaseRequest,
) -> CaseStopResponse:
    """Withdraw further work authority from one nonterminal case."""
    outcome = await stop_case(
        database,
        staff=staff,
        product_signal_id=product_signal_id,
        reason=body.reason,
        closure_deadline_seconds=CLOSURE_DEADLINE_SECONDS,
    )
    return CaseStopResponse(
        product_signal_id=outcome.product_signal_id,
        participation_occurred=outcome.participation_occurred,
        terminal_state=outcome.terminal_state,
        revoked_outreach_attempts=outcome.revoked_outreach_attempts,
        fenced_leases=outcome.fenced_leases,
        canceled_jobs=outcome.canceled_jobs,
        commands_in_reconciliation=outcome.commands_in_reconciliation,
    )


@router.get(
    "/{client_id}/case-files", response_model=CaseFileListResponse, response_model_exclude_none=True
)
async def list_case_files(staff: StaffOfPath, database: CurrentDatabase) -> CaseFileListResponse:
    """Return published Case Files for the client."""
    case_files = await load_case_files(database, client_id=staff.client_id)
    deliveries = await load_deliveries(database, client_id=staff.client_id)
    return CaseFileListResponse(
        case_files=case_files,
        deliveries=[
            delivery
            for delivery in (deliveries.get(case_file.id) for case_file in case_files)
            if delivery is not None
        ],
    )


@router.get(
    "/{client_id}/case-files/{case_file_id}",
    response_model=CaseFileDetailResponse,
    response_model_exclude_none=True,
)
async def get_case_file(
    staff: StaffOfPath,
    database: CurrentDatabase,
    case_file_id: Annotated[UUID, Path()],
) -> CaseFileDetailResponse:
    """Return one published Case File and its immutable core."""
    record, core = await load_case_file(
        database, client_id=staff.client_id, case_file_id=case_file_id
    )
    return CaseFileDetailResponse(
        case_file=record,
        core=None if core is None else CaseFileCore.model_validate(dict(core)),
    )


@router.get(
    "/{client_id}/members", response_model=MemberListResponse, response_model_exclude_none=True
)
async def list_members(staff: StaffOfPath, database: CurrentDatabase) -> MemberListResponse:
    """Return the client's staff membership."""
    members = await load_members(database, client_id=staff.client_id)
    emails = await load_member_emails(database, client_id=staff.client_id)
    return MemberListResponse(
        members=[_member_view(member, emails.get(member.id)) for member in members]
    )


def _member_view(member: ClientMemberRecord, email: str | None) -> ClientMemberView:
    """Compose one membership with the sign-in email, omitted when there is none."""
    return ClientMemberView(
        id=member.id,
        client_id=member.client_id,
        role=member.role,
        status=member.status,
        created_at=member.created_at,
        **({} if email is None else {"email": email}),
    )


@router.get(
    "/{client_id}/offers", response_model=OfferListResponse, response_model_exclude_none=True
)
async def list_offers(staff: StaffOfPath, database: CurrentDatabase) -> OfferListResponse:
    """Return Outreach Offers and the client's remaining issuance allowance."""
    offers = await load_offers(database, client_id=staff.client_id)
    allowance = await load_offer_allowance(database, client_id=staff.client_id)
    return OfferListResponse(
        offers=offers,
        currency=allowance.currency,
        authorized_minor_units=allowance.authorized,
        reserved_minor_units=allowance.reserved,
        earned_minor_units=allowance.earned,
        remaining_minor_units=allowance.remaining,
    )
