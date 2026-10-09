"""Lifecycle state enumerations for the uTrace domain area.

The states and their legal transitions are owned by the architecture diagrams
(``docs/diagrams/d2``). This module carries the exact snake_case values so
Python and SQL agree; the transition tables live with the control-plane machines.
"""

from enum import StrEnum

# The failure vocabularies are the envelope contract's; they are re-exported here
# so application code finds every lifecycle enumeration in one place.
from utrace_contracts.envelope.failures import (
    CommandDisposition,
    EffectState,
    RetryAdvice,
    TechnicalCategory,
)

__all__ = [
    "ActorKind",
    "AgentCharacterState",
    "AggregateKind",
    "CaseFileStage",
    "CaseFileVariant",
    "ClientMemberRole",
    "CommandDisposition",
    "CommandState",
    "CriterionObservability",
    "CriterionResult",
    "DeliveryState",
    "EffectState",
    "EnvironmentLeaseState",
    "EvidenceClaimState",
    "EvidenceClass",
    "FlowType",
    "GateDecisionKind",
    "ImplementationAttemptState",
    "ImplementationProgress",
    "IntakeItemState",
    "InterviewPhase",
    "InterviewSessionState",
    "JobRunState",
    "JobState",
    "MembershipStatus",
    "OfferIssuanceState",
    "OfferLedgerEntryKind",
    "OfferState",
    "OutboxState",
    "OutreachAttemptState",
    "OutreachPurpose",
    "PreviewAllocationKind",
    "PreviewRuntimeAllocationState",
    "PreviewVersionState",
    "ProductSignalState",
    "ResultKind",
    "RetryAdvice",
    "RoutingOutcome",
    "TechnicalCategory",
    "ValidationAttemptState",
    "ValidationOutcome",
    "VoiceConnectionState",
]


class AggregateKind(StrEnum):
    """Primary lifecycle stream kinds. Every Event names exactly one."""

    INTAKE_ITEM = "intake_item"
    PRODUCT_SIGNAL = "product_signal"
    OUTREACH_ATTEMPT = "outreach_attempt"
    INVITATION = "invitation"
    INTERVIEW_SESSION = "interview_session"
    VOICE_CONNECTION = "voice_connection"
    PREVIEW_RUNTIME_ALLOCATION = "preview_runtime_allocation"
    ENVIRONMENT_LEASE = "environment_lease"
    IMPLEMENTATION_ATTEMPT = "implementation_attempt"
    PREVIEW_VERSION = "preview_version"
    VALIDATION_ATTEMPT = "validation_attempt"
    CASE_FILE = "case_file"
    DELIVERY = "delivery"
    OUTREACH_OFFER = "outreach_offer"
    JOB = "job"
    JOB_RUN = "job_run"
    COMMAND = "command"


class ActorKind(StrEnum):
    """Who a fact is attributed to. Model roles never impersonate a person."""

    CUSTOMER_PARTICIPANT = "customer_participant"
    CLIENT_MEMBER = "client_member"
    UTRACE_AGENT_ROLE = "utrace_agent_role"
    PROVIDER_IDENTITY = "provider_identity"
    SERVICE = "service"
    SYSTEM = "system"


class ClientMemberRole(StrEnum):
    """Staff relationship with one client."""

    OWNER = "owner"
    ADMIN = "admin"


class MembershipStatus(StrEnum):
    """Whether a membership row currently grants access."""

    ACTIVE = "active"
    REVOKED = "revoked"


class IntakeItemState(StrEnum):
    """Routing lifecycle of one sealed intake batch."""

    RECEIVED = "received"
    ROUTING = "routing"
    ROUTED = "routed"
    ROUTING_BLOCKED = "routing_blocked"
    CANCELED = "canceled"


class RoutingOutcome(StrEnum):
    """The single outcome a routed batch commits."""

    CLOSE_AS_NOISE = "close_as_noise"
    CREATE_PRODUCT_SIGNAL = "create_product_signal"


class FlowType(StrEnum):
    """Which core flow a report belongs to. Only Feature Request Flow runs in the demo."""

    BUGFIX_FLOW = "bugfix_flow"
    FEATURE_REQUEST_FLOW = "feature_request_flow"


class ProductSignalState(StrEnum):
    """Product Signal lifecycle, including the three closed outcomes."""

    AWAITING_INTERVIEW = "awaiting_interview"
    INTERVIEWING = "interviewing"
    GATE_PENDING = "gate_pending"
    IMPLEMENTING = "implementing"
    AWAITING_VALIDATION = "awaiting_validation"
    VALIDATING = "validating"
    CLOSED_VALIDATED = "closed_validated"
    CLOSED_NO_IMPLEMENTATION = "closed_no_implementation"
    CLOSED_BLOCKED = "closed_blocked"
    CANCELED = "canceled"


class OutreachPurpose(StrEnum):
    """Why a private Discord contact is being made."""

    INITIAL_INVITATION = "initial_invitation"
    RETURN_REMINDER = "return_reminder"


class OutreachAttemptState(StrEnum):
    """Delivery lifecycle of one logical outreach attempt."""

    SCHEDULED = "scheduled"
    DISPATCHING = "dispatching"
    AWAITING_RESPONSE = "awaiting_response"
    CLAIMED = "claimed"
    DECLINED = "declined"
    EXPIRED = "expired"
    UNREACHED = "unreached"
    REVOKED = "revoked"


class InterviewSessionState(StrEnum):
    """One live encounter for interviewing, validation, or both."""

    INVITED = "invited"
    CLAIMED = "claimed"
    PREPARING = "preparing"
    LIVE = "live"
    CLOSED = "closed"


class InterviewPhase(StrEnum):
    """Durable interview phase projection shown to the participant."""

    PARTICIPANT_SETUP = "participant_setup"
    GREETING = "greeting"
    DISCOVERY = "discovery"
    IMPLEMENTATION = "implementation"
    VALIDATION = "validation"
    CLOSING = "closing"


class AgentCharacterState(StrEnum):
    """Rive character state projected from durable interview state.

    The values match `public.agent_character_state` and the extension protocol's
    `character_state` frame; `paused` and `reconnecting` are the two states the
    session controls and connection recovery add to the seven the PRD names.
    """

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ACKNOWLEDGEMENT = "acknowledgement"
    CONCERN = "concern"
    PAUSED = "paused"
    RECONNECTING = "reconnecting"
    COMPLETION = "completion"


class ImplementationProgress(StrEnum):
    """User-facing implementation progress projection."""

    NOT_STARTED = "not_started"
    REPRODUCTION_CAPTURED = "reproduction_captured"
    MAKING_CHANGES = "making_changes"
    PREPARING_PREVIEW = "preparing_preview"
    READY_FOR_VALIDATION = "ready_for_validation"


class VoiceConnectionState(StrEnum):
    """Lifecycle of one provider voice connection."""

    CONNECTING = "connecting"
    ACTIVE = "active"
    CLOSED = "closed"


class GateDecisionKind(StrEnum):
    """What the Implementation Gate decided."""

    APPROVED_LIVE = "approved_live"
    NO_IMPLEMENTATION = "no_implementation"


class ImplementationAttemptState(StrEnum):
    """Lifecycle and terminal outcomes of one Implementation Attempt."""

    AUTHORIZED = "authorized"
    QUEUED = "queued"
    ACQUIRING_ENVIRONMENT = "acquiring_environment"
    EXECUTING = "executing"
    CHECKING = "checking"
    PUBLISHING_CANDIDATE = "publishing_candidate"
    CANDIDATE_READY = "candidate_ready"
    SCOPE_CHANGE_REQUIRED = "scope_change_required"
    CHECKS_FAILED = "checks_failed"
    UNSAFE = "unsafe"
    INFRASTRUCTURE_BLOCKED = "infrastructure_blocked"
    PAUSED = "paused"
    CANCELED = "canceled"
    BUDGET_EXHAUSTED = "budget_exhausted"


class EnvironmentLeaseState(StrEnum):
    """Fenced workspace lease state."""

    ACTIVE = "active"
    FENCED = "fenced"
    RELEASED = "released"
    EXPIRED = "expired"


class PreviewAllocationKind(StrEnum):
    """Whether an allocation serves the base interview or a candidate."""

    BASE = "base"
    CANDIDATE = "candidate"


class PreviewRuntimeAllocationState(StrEnum):
    """Lifecycle of one full preview runtime allocation."""

    PENDING = "pending"
    SEEDING = "seeding"
    READY = "ready"
    BOUND = "bound"
    RELEASED = "released"
    TERMINATED = "terminated"


class PreviewVersionState(StrEnum):
    """Lifecycle of one immutable candidate build."""

    BUILDING = "building"
    READY = "ready"
    RETIRED = "retired"
    FAILED = "failed"


class ValidationAttemptState(StrEnum):
    """Lifecycle of one same-user validation encounter."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class ValidationOutcome(StrEnum):
    """The structured validation result recorded for a candidate."""

    VALIDATED = "validated"
    PARTIALLY_VALIDATED = "partially_validated"
    NOT_VALIDATED = "not_validated"
    UNCLEAR = "unclear"


class CriterionObservability(StrEnum):
    """Whether a criterion needs observed evidence or user judgment."""

    OBSERVED = "observed"
    SUBJECTIVE = "subjective"


class CriterionResult(StrEnum):
    """Criterion-level validation result."""

    MET = "met"
    UNMET = "unmet"
    UNCONFIRMED = "unconfirmed"
    CONTRADICTED = "contradicted"


class CaseFileVariant(StrEnum):
    """Which Case File variant an outcome produces."""

    VALIDATED_IMPLEMENTATION = "validated_implementation"
    NO_IMPLEMENTATION = "no_implementation"
    BLOCKED = "blocked"


class CaseFileStage(StrEnum):
    """Assembly stage of a Case File."""

    DRAFT = "draft"
    VERIFIED = "verified"
    DELIVERY_RESOLVED = "delivery_resolved"
    READY = "ready"
    PUBLISHED = "published"


class DeliveryState(StrEnum):
    """Lifecycle of the one authorized draft PR delivery."""

    RESERVED = "reserved"
    PREFLIGHTING = "preflighting"
    CREATING = "creating"
    RECONCILING = "reconciling"
    DELIVERED = "delivered"
    BLOCKED = "blocked"


class EvidenceClass(StrEnum):
    """The admissible evidence slice a claim cites."""

    INTAKE_SOURCE = "intake_source"
    TRANSCRIPT_FRAGMENT = "transcript_fragment"
    BROWSER_OBSERVATION = "browser_observation"
    SERVER_SPAN = "server_span"
    IMPLEMENTATION_ARTIFACT = "implementation_artifact"
    CHECK_RESULT = "check_result"
    VALIDATION_OBSERVATION = "validation_observation"


class EvidenceClaimState(StrEnum):
    """Whether a claim is proposed, confirmed, contradicted, or withdrawn."""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"
    WITHDRAWN = "withdrawn"


class OfferState(StrEnum):
    """Whether an Outreach Offer is reserved, earned, or released."""

    RESERVED = "reserved"
    EARNED = "earned"
    RELEASED = "released"


class OfferIssuanceState(StrEnum):
    """Issuance progress of one earned invoice credit."""

    PENDING = "pending"
    DISPATCHED = "dispatched"
    ISSUED = "issued"
    BLOCKED = "blocked"


class OfferLedgerEntryKind(StrEnum):
    """Movement recorded on the append-only offer ledger."""

    BUDGET_AUTHORIZED = "budget_authorized"
    RESERVATION_CREATED = "reservation_created"
    RESERVATION_RELEASED = "reservation_released"
    CREDIT_EARNED = "credit_earned"
    CREDIT_ISSUED = "credit_issued"
    ISSUANCE_BLOCKED = "issuance_blocked"


class JobState(StrEnum):
    """Scheduling state of one durable Job."""

    PENDING = "pending"
    READY = "ready"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    EXPIRED = "expired"


class JobRunState(StrEnum):
    """Outcome of one leased Job execution."""

    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"
    TIMED_OUT = "timed_out"


class CommandState(StrEnum):
    """Dispatch lifecycle of one Command."""

    CREATED = "created"
    QUEUED = "queued"
    DISPATCHING = "dispatching"
    DISPATCHED = "dispatched"
    ACKNOWLEDGED = "acknowledged"
    RECONCILING = "reconciling"
    TERMINAL = "terminal"


class ResultKind(StrEnum):
    """Whether a Result is an acceptance, progress, or the terminal outcome."""

    ACCEPTED = "accepted"
    PROGRESS = "progress"
    TERMINAL = "terminal"


class OutboxState(StrEnum):
    """Dispatch state of one transactional outbox row."""

    PENDING = "pending"
    ENQUEUED = "enqueued"
    DISPATCHED = "dispatched"
    FAILED = "failed"
