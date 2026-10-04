"""Typed API responses.

Responses compose the shared domain records so the HTTP surface and the contract
package cannot drift. Every model is closed to extra fields.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from utrace_contracts.domain.records import (
    CaseFileRecord,
    ClientMemberRecord,
    ClientRecord,
    GateDecisionRecord,
    ImplementationAttemptRecord,
    InterviewSessionRecord,
    OutreachOfferRecord,
    ProductSignalRecord,
    ValidationAttemptRecord,
)

from app.dashboard import (
    CaseConcern,
    CaseStopRecord,
    DeliverySummary,
    ResolvedReference,
)
from app.dashboard_case import (
    CaseCleanupSummary,
    CaseSessionSummary,
    OutreachAttemptSummary,
    PreviewRuntimeAllocationSummary,
    PreviewVersionSummary,
)
from app.offers.read_model import OfferDisposition
from app.outputs.sections import CaseFileCore
from app.queries import ParticipantInterview

__all__ = [
    "CaseDetailResponse",
    "CaseFileDetailResponse",
    "CaseFileListResponse",
    "CaseListResponse",
    "CaseOutreachResponse",
    "CaseStopResponse",
    "ClientMemberView",
    "ClientResponse",
    "ErrorResponse",
    "MemberListResponse",
    "OfferListResponse",
    "PreviewHandoffResponse",
    "PreviewRuntimeAllocationListResponse",
    "ReferenceResolutionResponse",
    "SessionProjectionResponse",
]


class ApiModel(BaseModel):
    """Base for every response: closed to extra fields.

    An optional field is omitted from the response rather than sent as ``null``,
    which is the contract convention; every route sets
    ``response_model_exclude_none`` so the rendered body matches.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class ErrorResponse(ApiModel):
    """The single error shape every refusal uses."""

    error: str
    detail: str
    subject: str | None = None


class ClientResponse(ApiModel):
    """One client and the caller's own membership in it."""

    client: ClientRecord
    membership: ClientMemberRecord


class CaseOverview(ApiModel):
    """What a case list shows beside the durable Product Signal record."""

    product_signal_id: UUID
    concern: CaseConcern | None = None
    originating_user_ref: str
    customer_account_ref: str


class CaseListResponse(ApiModel):
    """In-progress and closed cases for one client."""

    cases: list[ProductSignalRecord]
    overviews: list[CaseOverview]


class CaseDetailResponse(ApiModel):
    """One case with the durable records a staff view may read."""

    case: ProductSignalRecord
    overview: CaseOverview
    gate_decisions: list[GateDecisionRecord]
    implementation_attempts: list[ImplementationAttemptRecord]
    validation_attempts: list[ValidationAttemptRecord]
    interview_sessions: list[CaseSessionSummary]
    preview_versions: list[PreviewVersionSummary]
    cleanup: CaseCleanupSummary
    offer: OfferDisposition | None = None
    stop: CaseStopRecord | None = None
    stoppable: bool
    case_file: CaseFileRecord | None = None
    delivery: DeliverySummary | None = None


class CaseOutreachResponse(ApiModel):
    """How uTrace reached the originating user of one case.

    The invitations are listed under the attempt that sent them, and none of
    them carries a token: an invitation is observed by what became of it.
    """

    product_signal_id: UUID
    attempts: list[OutreachAttemptSummary]


class PreviewRuntimeAllocationListResponse(ApiModel):
    """The Preview Runtime Allocations one case claimed."""

    product_signal_id: UUID
    allocations: list[PreviewRuntimeAllocationSummary]


class CaseStopResponse(ApiModel):
    """What the deny-only stop withdrew."""

    product_signal_id: UUID
    participation_occurred: bool
    terminal_state: str | None = None
    revoked_outreach_attempts: int
    fenced_leases: int
    canceled_jobs: int
    commands_in_reconciliation: int


class CaseFileListResponse(ApiModel):
    """Published Case Files for one client."""

    case_files: list[CaseFileRecord]
    deliveries: list[DeliverySummary]


class CaseFileDetailResponse(ApiModel):
    """One published Case File and its immutable core.

    The core is the sealed record. Frequency and linked reports are deliberately
    not in it: they are the separate projection at
    ``/case-files/{id}/projection``, which keeps updating after closure.
    """

    case_file: CaseFileRecord
    core: CaseFileCore | None = None


class ReferenceResolutionResponse(ApiModel):
    """The labels behind the opaque client-scoped references a caller holds."""

    references: list[ResolvedReference]


class ClientMemberView(ClientMemberRecord):
    """One staff membership with the sign-in email the dashboard shows.

    uTrace records no separate display name for client staff, so the email is
    the only label there is; it is omitted when the identity carries none.
    """

    email: str | None = None


class MemberListResponse(ApiModel):
    """The client's staff membership."""

    members: list[ClientMemberView]


class OfferListResponse(ApiModel):
    """Outreach Offers and the client's remaining issuance allowance."""

    offers: list[OutreachOfferRecord]
    currency: str | None = None
    authorized_minor_units: int
    reserved_minor_units: int
    earned_minor_units: int
    remaining_minor_units: int


class SessionProjectionResponse(ApiModel):
    """The durable session projection the session tab renders."""

    session: InterviewSessionRecord


class PreviewHandoffResponse(ApiModel):
    """The one-time front-door URL for this session's preview origin."""

    front_door_url: str
    expires_at: str


class ParticipantPreferencesView(ApiModel):
    """The participant's global action preferences at one revision."""

    revision: int
    visual_guidance_enabled: bool
    agent_cursor_enabled: bool


class ParticipantAccountResponse(ApiModel):
    """`GET /v1/participants/me`: the signed-in participant's own account."""

    participation_count: int
    interviews: tuple[ParticipantInterview, ...]
    preferences: ParticipantPreferencesView
