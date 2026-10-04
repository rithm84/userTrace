"""Typed domain records shared across uTrace components.

These are read projections of the durable control-plane tables. They carry no
authority: knowledge of an identifier grants nothing. Identifiers are UUIDv7,
timestamps are UTC, and amounts are integer minor units.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from utrace_contracts.base import reject_explicit_null
from utrace_contracts.domain.lifecycle import (
    AgentCharacterState,
    CaseFileStage,
    CaseFileVariant,
    ClientMemberRole,
    CriterionObservability,
    FlowType,
    GateDecisionKind,
    ImplementationAttemptState,
    ImplementationProgress,
    InterviewPhase,
    InterviewSessionState,
    MembershipStatus,
    OfferIssuanceState,
    OfferState,
    ProductSignalState,
    ValidationAttemptState,
    ValidationOutcome,
)

__all__ = [
    "CaseFileRecord",
    "ClientMemberRecord",
    "ClientRecord",
    "GateDecisionRecord",
    "ImplementationAttemptRecord",
    "InterviewSessionRecord",
    "OutreachOfferRecord",
    "ProductSignalRecord",
    "SessionControlsState",
    "SessionReadiness",
    "ValidationAttemptRecord",
    "ValidationCriterionRecord",
]


class DomainRecord(BaseModel):
    """Base for every domain record: frozen, closed to extra fields, and never null.

    ``strict`` stays off because a record is built from a database row, where a
    UUID or a timestamp arrives as the driver's own type. An optional column is
    carried by omitting the field, not by sending ``null``, so the projection
    that reads a row drops its null columns before validating.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=False)

    @model_validator(mode="before")
    @classmethod
    def _reject_explicit_null(cls, data: object) -> object:
        return reject_explicit_null(data)


class ClientRecord(DomainRecord):
    """One uTrace Client: one company, one product."""

    id: UUID
    name: str
    product_name: str
    revision: int
    created_at: datetime


class ClientMemberRecord(DomainRecord):
    """A staff relationship with one client. Exactly one active owner per client."""

    id: UUID
    client_id: UUID
    role: ClientMemberRole
    status: MembershipStatus
    created_at: datetime


class ProductSignalRecord(DomainRecord):
    """One independently gateable and validatable product outcome."""

    id: UUID
    client_id: UUID
    state: ProductSignalState
    revision: int
    machine_version: int
    initial_flow_type: FlowType
    established_flow_type: FlowType | None = None
    classification_revision: int
    concern_revision: int
    acceptance_criteria_revision: int
    originating_client_user_id: UUID
    originating_customer_account_id: UUID
    pinned_configuration_release_id: UUID
    return_deadline_at: datetime | None = None
    closure_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class GateDecisionRecord(DomainRecord):
    """An immutable Implementation Gate decision and its frozen approved scope."""

    id: UUID
    client_id: UUID
    product_signal_id: UUID
    decision: GateDecisionKind
    blocker_reason: str | None = None
    classification_revision: int
    acceptance_criteria_revision: int
    pinned_configuration_release_id: UUID
    decided_at: datetime


class ImplementationAttemptRecord(DomainRecord):
    """The one authorized attempt a Product Signal may have."""

    id: UUID
    client_id: UUID
    product_signal_id: UUID
    gate_decision_id: UUID
    state: ImplementationAttemptState
    revision: int
    repository_ref: str
    base_revision: str
    outcome_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class SessionReadiness(DomainRecord):
    """The two readiness boundaries. Connected sockets alone establish neither."""

    conversational: bool
    observation: bool


class SessionControlsState(DomainRecord):
    """Durable state of the participant's session controls."""

    muted: bool
    paused: bool


class InterviewSessionRecord(DomainRecord):
    """The durable Interview Session projection the session tab reads."""

    id: UUID
    client_id: UUID
    product_signal_id: UUID
    state: InterviewSessionState
    revision: int
    phase: InterviewPhase
    readiness: SessionReadiness
    character_state: AgentCharacterState
    character_revision: int
    implementation_progress: ImplementationProgress
    controls: SessionControlsState
    closure_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class ValidationCriterionRecord(DomainRecord):
    """One frozen acceptance criterion at a criteria revision."""

    id: UUID
    product_signal_id: UUID
    criteria_revision: int
    ordinal: int
    statement: str
    observability: CriterionObservability


class ValidationAttemptRecord(DomainRecord):
    """Binds one originating user to one exact candidate and dataset."""

    id: UUID
    client_id: UUID
    product_signal_id: UUID
    implementation_attempt_id: UUID
    preview_version_id: UUID
    interview_session_id: UUID
    client_user_id: UUID
    state: ValidationAttemptState
    revision: int
    dataset_manifest_digest: str
    acceptance_criteria_revision: int
    outcome: ValidationOutcome | None = None
    completed_at: datetime | None = None
    created_at: datetime


class CaseFileRecord(DomainRecord):
    """The client-facing output record. Its published core is immutable."""

    id: UUID
    client_id: UUID
    product_signal_id: UUID
    variant: CaseFileVariant
    stage: CaseFileStage
    revision: int
    core_digest: str | None = None
    delivery_id: UUID | None = None
    sealed_at: datetime | None = None
    published_at: datetime | None = None
    created_at: datetime


class OutreachOfferRecord(DomainRecord):
    """One fixed invoice-credit offer for one case, in integer minor units."""

    id: UUID
    client_id: UUID
    product_signal_id: UUID
    customer_account_id: UUID
    state: OfferState
    revision: int
    currency: str
    amount_minor_units: int
    issuance_state: OfferIssuanceState | None = None
    created_at: datetime
