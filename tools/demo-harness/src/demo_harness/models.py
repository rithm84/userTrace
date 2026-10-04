"""Typed bodies for the control-plane responses the harness reads.

The shared contract package owns the domain records, so those are imported
rather than restated. What is declared here is the route-level envelope around
them, plus the subset of the client configuration policy that
``docs/SCENARIO.md`` pins. The policy models deliberately ignore unrelated
fields: the harness asserts the scenario's facts, and the control plane owns the
rest of the contract.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from utrace_contracts.domain.lifecycle import (
    AgentCharacterState,
    CaseFileStage,
    CaseFileVariant,
    CriterionObservability,
    CriterionResult,
    DeliveryState,
    GateDecisionKind,
    InterviewPhase,
    InterviewSessionState,
    OfferIssuanceState,
    OfferState,
    OutreachAttemptState,
    OutreachPurpose,
    PreviewAllocationKind,
    PreviewRuntimeAllocationState,
    ValidationAttemptState,
    ValidationOutcome,
)
from utrace_contracts.domain.records import (
    CaseFileRecord,
    ClientMemberRecord,
    ClientRecord,
    GateDecisionRecord,
    ImplementationAttemptRecord,
    ProductSignalRecord,
    ValidationAttemptRecord,
)

__all__ = [
    "AllowanceView",
    "CaseCleanup",
    "CaseDetail",
    "CaseFileDetail",
    "CaseFileList",
    "CaseList",
    "CaseOutreach",
    "CaseSessionView",
    "ConfigurationRelease",
    "CustomerAccount",
    "CustomerAccountList",
    "DeliveryView",
    "IngestedIntakeMessage",
    "IntakeItemList",
    "IntakeItemSummary",
    "InvitationDisposition",
    "InvitationView",
    "OfferDisposition",
    "OffersView",
    "OutreachAttemptView",
    "OutreachOfferPolicy",
    "PreviewReadinessView",
    "PreviewRuntimeAllocationList",
    "PreviewRuntimeAllocationView",
    "PreviewVersionDetail",
    "SessionPause",
    "SessionReadinessView",
    "StaffClient",
    "StaffClientList",
    "ValidationRecord",
]


class Body(BaseModel):
    """Base for a response the harness fully owns: closed and immutable."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Partial(BaseModel):
    """Base for a contract the harness reads a pinned subset of."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class StaffClient(Body):
    """One client the demo staff identity may act for."""

    client: ClientRecord
    membership: ClientMemberRecord


class StaffClientList(Body):
    """Every client the demo staff identity may act for."""

    clients: list[StaffClient]


class WatchedChannel(Partial):
    """One configured intake surface."""

    channel_ref: str
    name: str


class CustomerSurface(Partial):
    """The Discord server and the channels uTrace watches in it."""

    provider: str
    installation_ref: str
    watched_channels: tuple[WatchedChannel, ...]


class RequiredCheck(Partial):
    """One command the workspace runs on the base and again on the candidate."""

    name: str
    command: str


class ImplementationPolicy(Partial):
    """The Autonomy Policy the Implementation Gate narrows, as the scenario pins it."""

    live_implementation_allowed: bool
    repository_ref: str
    base_revision: str
    workspace_snapshot_id: str
    permitted_paths: tuple[str, ...]
    blocked_paths: tuple[str, ...]
    required_checks: tuple[RequiredCheck, ...]


class DataSeedingContract(Partial):
    """The approved Data Seeding Contract this release pins, by identity."""

    manifest_hash: str
    fixture_clock: str
    environment_template_id: str
    environment_template_revision: int


class OutreachOfferPolicy(Partial):
    """The fixed offer this client authorizes."""

    enabled: bool
    currency: str
    fixed_amount_minor_units: int
    authorized_ceiling_minor_units: int
    billing_account_ref: str
    billing_use: str
    completion_terms: tuple[str, ...]


class ConfigurationPolicy(Partial):
    """The pinned policy the scenario's starting state is checked against."""

    customer_surface: CustomerSurface
    implementation: ImplementationPolicy
    data_seeding: DataSeedingContract
    outreach_offers: OutreachOfferPolicy


class ConfigurationRelease(Partial):
    """One immutable configuration release."""

    id: UUID
    client_id: UUID
    release_number: int
    digest: str
    policy: ConfigurationPolicy


class CustomerAccount(Partial):
    """One customer organization and its billing mapping."""

    id: UUID
    client_id: UUID
    external_account_key: str
    name: str
    stripe_customer_ref: str | None = None


class CustomerAccountList(Body):
    """The client's customer accounts."""

    customer_accounts: list[CustomerAccount]


class AllowanceView(Partial):
    """The client's authorized, reserved, earned, and remaining allowance."""

    currency: str | None = None
    authorized_minor_units: int
    reserved_minor_units: int
    earned_minor_units: int
    remaining_minor_units: int


class OfferDisposition(Partial):
    """One offer's disposition, issuance status, and reconciled application."""

    outreach_offer_id: UUID
    product_signal_id: UUID
    customer_account_id: UUID
    currency: str
    amount_minor_units: int
    state: OfferState
    issuance_state: OfferIssuanceState | None = None
    provider_transaction_ref: str | None = None
    issuance_blocked_reason: str | None = None
    invoice_application_minor_units: int | None = None


class OffersView(Partial):
    """The allowance and every offer written against it."""

    currency: str | None = None
    authorized_minor_units: int
    reserved_minor_units: int
    earned_minor_units: int
    remaining_minor_units: int
    offers: tuple[OfferDisposition, ...]


class CaseList(Partial):
    """Every case for one client."""

    cases: list[ProductSignalRecord]


class CaseCleanup(Partial):
    """What the case still holds, and what it has already given back."""

    preview_runtime_allocations_retained: int
    preview_runtime_allocations_released: int
    preview_runtime_allocations_terminated: int
    environment_leases_open: int
    invitations_outstanding: int
    workspace_cleanup_state: str | None = None
    complete: bool


class SessionPause(Partial):
    """One Pause or Resume the participant invoked during a session."""

    paused: bool
    occurred_at: datetime


class SessionReadinessView(Partial):
    """The two readiness boundaries, as the staff read model reports them."""

    conversational: bool
    observation: bool


class CaseSessionView(Partial):
    """One Interview Session of the case, with the state the demo watches."""

    id: UUID
    purpose: str
    state: InterviewSessionState
    phase: InterviewPhase
    readiness: SessionReadinessView
    character_state: AgentCharacterState
    paused: bool
    pauses: list[SessionPause]
    closure_reason: str | None = None


class CaseDetail(Partial):
    """One case with the durable records a staff view may read."""

    case: ProductSignalRecord
    gate_decisions: list[GateDecisionRecord]
    implementation_attempts: list[ImplementationAttemptRecord]
    validation_attempts: list[ValidationAttemptRecord]
    interview_sessions: list[CaseSessionView]
    cleanup: CaseCleanup
    case_file: CaseFileRecord | None = None

    def approved_live(self) -> GateDecisionRecord | None:
        """Return the gate decision that authorized live implementation."""
        for decision in self.gate_decisions:
            if decision.decision is GateDecisionKind.APPROVED_LIVE:
                return decision
        return None

    def live_session(self) -> CaseSessionView | None:
        """Return the encounter that is open right now, when one is."""
        for session in self.interview_sessions:
            if session.state is InterviewSessionState.LIVE:
                return session
        return None


class InvitationDisposition(StrEnum):
    """What became of one invitation.

    This mirrors the staff read model's own enum. It lives in the control
    plane's API layer rather than in the shared contract package, so the harness
    restates it and a value it does not know is rejected instead of ignored.
    """

    SENT = "sent"
    CLAIMED = "claimed"
    EXPIRED = "expired"
    REVOKED = "revoked"


class InvitationView(Partial):
    """One invitation of an Outreach Attempt. It carries no token."""

    id: UUID
    disposition: InvitationDisposition
    expires_at: datetime
    claimed_at: datetime | None = None
    revoked_at: datetime | None = None
    interview_session_id: UUID | None = None


class OutreachAttemptView(Partial):
    """One attempt to reach the originating user, and what it carried."""

    id: UUID
    purpose: OutreachPurpose
    state: OutreachAttemptState
    outreach_offer_id: UUID | None = None
    dispatched_at: datetime | None = None
    invitations: list[InvitationView]


class CaseOutreach(Partial):
    """How uTrace reached the originating user of one case."""

    product_signal_id: UUID
    attempts: list[OutreachAttemptView]


class PreviewReadinessView(Partial):
    """The newest readiness probe recorded against one runtime."""

    probe: str
    outcome: str


class PreviewRuntimeAllocationView(Partial):
    """One Preview Runtime Allocation and the runtime behind it."""

    id: UUID
    allocation_kind: PreviewAllocationKind
    state: PreviewRuntimeAllocationState
    origin_host: str | None = None
    interview_session_id: UUID | None = None
    preview_version_id: UUID | None = None
    readiness: PreviewReadinessView | None = None
    released_at: datetime | None = None
    terminated_at: datetime | None = None


class PreviewRuntimeAllocationList(Partial):
    """The Preview Runtime Allocations one case claimed."""

    product_signal_id: UUID
    allocations: list[PreviewRuntimeAllocationView]


class IntakeItemSummary(Partial):
    """One observed batch and the outcome routing committed for it."""

    id: UUID
    state: str
    source_channel_ref: str
    author_provider_identity: str
    routing_outcome: str | None = None
    product_signal_id: UUID | None = None
    blocked_reason: str | None = None
    created_at: str


class IntakeItemList(Body):
    """Recent Intake Items for one client, newest first."""

    intake_items: list[IntakeItemSummary]


class IngestedIntakeMessage(Body):
    """The Intake Item identity the staff re-ingestion route answered with."""

    intake_item_id: UUID
    intake_batch_id: UUID
    provider_message_id: str
    duplicate: bool


class CriterionProof(Partial):
    """One acceptance criterion and the evidence that settled it."""

    criterion_id: UUID
    ordinal: int
    statement: str
    observability: CriterionObservability
    result: CriterionResult
    user_confirmed: bool
    supporting_evidence_record_ids: list[UUID]
    contradicting_evidence_record_ids: list[UUID]
    reason: str


class ValidationRecord(Partial):
    """The most recent Validation Attempt and its criterion-level proofs."""

    validation_attempt_id: UUID
    product_signal_id: UUID
    state: ValidationAttemptState
    outcome: ValidationOutcome | None = None
    preview_version_id: UUID
    implementation_attempt_id: UUID
    interview_session_id: UUID
    client_user_id: UUID
    dataset_manifest_digest: str
    acceptance_criteria_revision: int
    workflow_ref: str
    criterion_proofs: list[CriterionProof]


class CaseFileList(Partial):
    """Published Case Files for one client."""

    case_files: list[CaseFileRecord]


class CaseFileDetail(Partial):
    """One published Case File and its immutable core."""

    case_file: CaseFileRecord
    core: dict[str, object] | None = None


class DeliveryView(Partial):
    """The one draft PR a validated implementation delivers."""

    delivery_id: UUID
    state: DeliveryState
    repository_ref: str
    branch_ref: str
    base_branch: str
    base_revision: str
    validated_source_commit: str
    pull_request_ref: str | None = None
    pull_request_number: int | None = None
    verified_head_sha: str | None = None
    blocker_reason: str | None = None
    case_file_url: str


class PreviewVersionDetail(Partial):
    """One immutable candidate build."""

    preview_version_id: UUID
    implementation_attempt_id: UUID
    state: str
    source_commit: str
    source_digest: str
    build_digest: str
    artifact_manifest_digest: str | None = None
    superseded_by_preview_version_id: UUID | None = None


def published(case_files: list[CaseFileRecord]) -> list[CaseFileRecord]:
    """Return only the Case Files that reached ``published``."""
    return [record for record in case_files if record.stage is CaseFileStage.PUBLISHED]


def validated_implementation(record: CaseFileRecord) -> bool:
    """Return whether one Case File is the validated-implementation variant."""
    return record.variant is CaseFileVariant.VALIDATED_IMPLEMENTATION
