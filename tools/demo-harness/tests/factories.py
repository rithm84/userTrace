"""Typed journey records built by hand, for the rule tests.

These are the contract's own record models with scenario-shaped values. Nothing
is mocked: the rules under test are pure functions over these records, and the
live test drives the real control plane.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from utrace_contracts.domain.lifecycle import (
    AgentCharacterState,
    CaseFileStage,
    CaseFileVariant,
    DeliveryState,
    FlowType,
    GateDecisionKind,
    ImplementationAttemptState,
    InterviewPhase,
    InterviewSessionState,
    OfferIssuanceState,
    OfferState,
    OutreachAttemptState,
    OutreachPurpose,
    PreviewAllocationKind,
    PreviewRuntimeAllocationState,
    ProductSignalState,
    ValidationAttemptState,
    ValidationOutcome,
)
from utrace_contracts.domain.records import (
    CaseFileRecord,
    GateDecisionRecord,
    ImplementationAttemptRecord,
    ProductSignalRecord,
)

from demo_harness.journey import JourneySnapshot
from demo_harness.models import (
    CaseCleanup,
    CaseDetail,
    CaseSessionView,
    DeliveryView,
    InvitationDisposition,
    InvitationView,
    OfferDisposition,
    OffersView,
    OutreachAttemptView,
    PreviewReadinessView,
    PreviewRuntimeAllocationView,
    SessionReadinessView,
    ValidationRecord,
)

CLIENT_ID = UUID("01a0c378-f5e3-7581-a5e7-d487e7d36772")
SIGNAL_ID = UUID("01a0c379-0e36-7c4d-bd60-eae7887cd06a")
USER_ID = UUID("01a0c378-f76b-70e4-8a9b-7413cd51389f")
ACCOUNT_ID = UUID("01a0c378-f6a8-7695-956b-4c9fd19e7218")
ATTEMPT_ID = UUID("01a0c380-0000-7000-8000-000000000001")
PREVIEW_ID = UUID("01a0c380-0000-7000-8000-000000000002")
OUTREACH_ID = UUID("01a0c380-0000-7000-8000-000000000003")
INVITATION_ID = UUID("01a0c380-0000-7000-8000-000000000004")
SESSION_ID = UUID("01a0c380-0000-7000-8000-000000000005")
ALLOCATION_ID = UUID("01a0c380-0000-7000-8000-000000000006")
NOW = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
SOURCE_COMMIT = "b" * 40
DATASET = "sha256:dataset-shopalytics"


def signal(
    *, state: ProductSignalState, flow: FlowType, criteria_revision: int
) -> ProductSignalRecord:
    """One Product Signal in a chosen state."""
    return ProductSignalRecord(
        id=SIGNAL_ID,
        client_id=CLIENT_ID,
        state=state,
        revision=4,
        machine_version=1,
        initial_flow_type=flow,
        classification_revision=1,
        concern_revision=1,
        acceptance_criteria_revision=criteria_revision,
        originating_client_user_id=USER_ID,
        originating_customer_account_id=ACCOUNT_ID,
        pinned_configuration_release_id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )


def gate(*, decision: GateDecisionKind) -> GateDecisionRecord:
    """One immutable gate decision."""
    return GateDecisionRecord(
        id=uuid4(),
        client_id=CLIENT_ID,
        product_signal_id=SIGNAL_ID,
        decision=decision,
        classification_revision=1,
        acceptance_criteria_revision=1,
        pinned_configuration_release_id=uuid4(),
        decided_at=NOW,
    )


def attempt() -> ImplementationAttemptRecord:
    """One Implementation Attempt that reached candidate_ready."""
    return ImplementationAttemptRecord(
        id=ATTEMPT_ID,
        client_id=CLIENT_ID,
        product_signal_id=SIGNAL_ID,
        gate_decision_id=uuid4(),
        state=ImplementationAttemptState.CANDIDATE_READY,
        revision=6,
        repository_ref="ShopalyticsStore/shopalytics",
        base_revision="a" * 40,
        created_at=NOW,
        updated_at=NOW,
    )


def case_file(*, stage: CaseFileStage, variant: CaseFileVariant) -> CaseFileRecord:
    """One Case File at a chosen stage."""
    return CaseFileRecord(
        id=uuid4(),
        client_id=CLIENT_ID,
        product_signal_id=SIGNAL_ID,
        variant=variant,
        stage=stage,
        revision=4,
        core_digest="sha256:" + "c" * 64,
        created_at=NOW,
    )


def validation(*, outcome: ValidationOutcome | None, client_user_id: UUID) -> ValidationRecord:
    """One Validation Attempt with a single met criterion."""
    return ValidationRecord.model_validate(
        {
            "validation_attempt_id": uuid4(),
            "product_signal_id": SIGNAL_ID,
            "state": ValidationAttemptState.COMPLETED,
            "outcome": outcome,
            "preview_version_id": PREVIEW_ID,
            "implementation_attempt_id": ATTEMPT_ID,
            "interview_session_id": uuid4(),
            "client_user_id": client_user_id,
            "dataset_manifest_digest": DATASET,
            "acceptance_criteria_revision": 1,
            "workflow_ref": "conversion-dashboard/saved-views",
            "criterion_proofs": [],
        }
    )


def delivery(*, state: DeliveryState, commit: str) -> DeliveryView:
    """One delivery record for the validated source."""
    return DeliveryView.model_validate(
        {
            "delivery_id": uuid4(),
            "state": state,
            "repository_ref": "ShopalyticsStore/shopalytics",
            "branch_ref": "utrace/saved-conversion-views",
            "base_branch": "main",
            "base_revision": "a" * 40,
            "validated_source_commit": commit,
            "pull_request_ref": "ShopalyticsStore/shopalytics#41",
            "pull_request_number": 41,
            "case_file_url": "https://utrace.dev/case-files/1",
        }
    )


def offer(
    *, state: OfferState, issuance: OfferIssuanceState | None, amount: int
) -> OfferDisposition:
    """One offer disposition for this case."""
    return OfferDisposition.model_validate(
        {
            "outreach_offer_id": uuid4(),
            "product_signal_id": SIGNAL_ID,
            "customer_account_id": ACCOUNT_ID,
            "currency": "USD",
            "amount_minor_units": amount,
            "state": state,
            "issuance_state": issuance,
            "provider_transaction_ref": (
                "cbtxn_demo" if issuance is OfferIssuanceState.ISSUED else None
            ),
        }
    )


def invitation(*, disposition: InvitationDisposition) -> InvitationView:
    """One invitation in a chosen disposition."""
    return InvitationView(
        id=INVITATION_ID,
        disposition=disposition,
        expires_at=NOW,
        claimed_at=NOW if disposition is InvitationDisposition.CLAIMED else None,
        revoked_at=NOW if disposition is InvitationDisposition.REVOKED else None,
        interview_session_id=(
            SESSION_ID if disposition is InvitationDisposition.CLAIMED else None
        ),
    )


def outreach(
    *, state: OutreachAttemptState, invitations: list[InvitationView]
) -> OutreachAttemptView:
    """The initial private invitation attempt, in a chosen state."""
    return OutreachAttemptView(
        id=OUTREACH_ID,
        purpose=OutreachPurpose.INITIAL_INVITATION,
        state=state,
        outreach_offer_id=None,
        dispatched_at=NOW,
        invitations=invitations,
    )


def session(*, state: InterviewSessionState) -> CaseSessionView:
    """One Interview Session of the case, in a chosen state."""
    return CaseSessionView(
        id=SESSION_ID,
        purpose="interview",
        state=state,
        phase=InterviewPhase.DISCOVERY,
        readiness=SessionReadinessView(conversational=True, observation=True),
        character_state=AgentCharacterState.LISTENING,
        paused=False,
        pauses=[],
        closure_reason=None,
    )


def runtime(*, state: PreviewRuntimeAllocationState) -> PreviewRuntimeAllocationView:
    """The base interview runtime, in a chosen state."""
    return PreviewRuntimeAllocationView(
        id=ALLOCATION_ID,
        allocation_kind=PreviewAllocationKind.BASE,
        state=state,
        origin_host="opaque.preview.utrace.dev",
        interview_session_id=SESSION_ID,
        preview_version_id=None,
        readiness=PreviewReadinessView(probe="application_health", outcome="passed"),
        released_at=None,
        terminated_at=NOW,
    )


def cleanup(*, complete: bool) -> CaseCleanup:
    """The case's resource cleanup summary, finished or still holding."""
    return CaseCleanup(
        preview_runtime_allocations_retained=0 if complete else 1,
        preview_runtime_allocations_released=0,
        preview_runtime_allocations_terminated=1 if complete else 0,
        environment_leases_open=0,
        invitations_outstanding=0,
        workspace_cleanup_state="workspace destroyed" if complete else None,
        complete=complete,
    )


def snapshot(
    *,
    case: ProductSignalRecord,
    gates: list[GateDecisionRecord],
    attempts: list[ImplementationAttemptRecord],
    sessions: list[CaseSessionView],
    outreach_attempts: list[OutreachAttemptView],
    runtimes: list[PreviewRuntimeAllocationView],
    cleanup_summary: CaseCleanup,
    record: CaseFileRecord | None,
    validation_record: ValidationRecord | None,
    delivery_record: DeliveryView | None,
    dispositions: list[OfferDisposition],
    extra_cases: list[ProductSignalRecord],
) -> JourneySnapshot:
    """Assemble one journey snapshot from the records above."""
    return JourneySnapshot(
        client_id=CLIENT_ID,
        cases=[case, *extra_cases],
        case=CaseDetail(
            case=case,
            gate_decisions=gates,
            implementation_attempts=attempts,
            validation_attempts=[],
            interview_sessions=sessions,
            cleanup=cleanup_summary,
            case_file=record,
        ),
        outreach=outreach_attempts,
        runtimes=runtimes,
        validation=validation_record,
        delivery=delivery_record,
        offers=OffersView.model_validate(
            {
                "currency": "USD",
                "authorized_minor_units": 250_000,
                "reserved_minor_units": 0,
                "earned_minor_units": 25_000,
                "remaining_minor_units": 225_000,
                "offers": dispositions,
            }
        ),
    )
