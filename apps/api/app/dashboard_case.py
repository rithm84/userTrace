"""One case's run, and the resources it is holding or has given back.

[Client outputs](../../../docs/arch-components/client-outputs.md#full-client-dashboard)
gives the dashboard authorized projections, never model reasoning or raw
telemetry. These are the per-case reads: how uTrace reached the originating
user, the encounters it ran with them, the preview runtimes it claimed, the
candidates it built, and what the case still holds.

Everything here is derived from records the controllers already commit. Nothing
is recomputed from provider state, and no read invents a state a durable row
does not carry.

Every statement is tenant-scoped in SQL as well as by the caller's resolved
membership, so a missing predicate cannot leak another client's rows.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict
from utrace_contracts.domain.lifecycle import (
    AgentCharacterState,
    EnvironmentLeaseState,
    ImplementationProgress,
    InterviewPhase,
    InterviewSessionState,
    OutreachAttemptState,
    OutreachPurpose,
    PreviewAllocationKind,
    PreviewRuntimeAllocationState,
    PreviewVersionState,
)
from utrace_contracts.domain.records import SessionReadiness

from app.db.pool import Database
from app.db.rows import (
    as_bool,
    as_datetime,
    as_int,
    as_optional_str,
    as_optional_uuid,
    as_str,
    as_uuid,
)
from app.queries import fetch_all, fetch_one

__all__ = [
    "CaseCleanupSummary",
    "CaseSessionSummary",
    "InvitationDisposition",
    "InvitationSummary",
    "OutreachAttemptSummary",
    "PreviewReadinessObservation",
    "PreviewRuntimeAllocationSummary",
    "PreviewVersionSummary",
    "SessionPauseRecord",
    "load_case_cleanup",
    "load_case_outreach",
    "load_case_sessions",
    "load_preview_runtime_allocations",
    "load_preview_versions",
]

#: The allocation states that still hold a runtime, so cleanup has not run.
_RETAINED_ALLOCATION_STATES = (
    PreviewRuntimeAllocationState.PENDING.value,
    PreviewRuntimeAllocationState.SEEDING.value,
    PreviewRuntimeAllocationState.READY.value,
    PreviewRuntimeAllocationState.BOUND.value,
)

#: The lease states that still grant, or still fence, workspace mutation.
_OPEN_LEASE_STATES = (
    EnvironmentLeaseState.ACTIVE.value,
    EnvironmentLeaseState.FENCED.value,
)

_PAUSED_EVENT = "session_paused_live_implementation_held"
_RESUMED_EVENT = "session_resumed_live_implementation_released"


class _ReadModel(BaseModel):
    """Frozen, closed shape for a projection the dashboard reads."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class InvitationDisposition(StrEnum):
    """What became of one invitation, derived from its durable timestamps."""

    SENT = "sent"
    CLAIMED = "claimed"
    EXPIRED = "expired"
    REVOKED = "revoked"


class InvitationSummary(_ReadModel):
    """One invitation, without the token it was sent with.

    Only the token's digest is ever stored, and not even that is read here: an
    invitation is observed by what happened to it, never by what would let a
    reader claim it.
    """

    id: UUID
    outreach_attempt_id: UUID
    disposition: InvitationDisposition
    bound_provider: str
    expires_at: datetime
    claimed_at: datetime | None
    revoked_at: datetime | None
    interview_session_id: UUID | None


class OutreachAttemptSummary(_ReadModel):
    """One attempt to reach the originating user, and what it carried."""

    id: UUID
    purpose: OutreachPurpose
    state: OutreachAttemptState
    outreach_offer_id: UUID | None
    scheduled_at: datetime
    dispatched_at: datetime | None
    responded_at: datetime | None
    expires_at: datetime
    reason: str | None
    invitations: list[InvitationSummary]


class SessionPauseRecord(_ReadModel):
    """One Pause or Resume the participant invoked during a session."""

    paused: bool
    occurred_at: datetime


class CaseSessionSummary(_ReadModel):
    """One Interview Session of a case, as the staff view may read it.

    The character state and the two readiness booleans are the same durable
    facts the participant's own projection is derived from; staff read the
    committed record rather than the encounter's vocabulary.
    """

    id: UUID
    purpose: str
    state: InterviewSessionState
    phase: InterviewPhase
    readiness: SessionReadiness
    character_state: AgentCharacterState
    character_revision: int
    implementation_progress: ImplementationProgress
    muted: bool
    paused: bool
    pauses: list[SessionPauseRecord]
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    closure_reason: str | None


class PreviewVersionSummary(_ReadModel):
    """One candidate Preview Version and the artifact identities it binds."""

    id: UUID
    implementation_attempt_id: UUID
    state: PreviewVersionState
    source_commit: str
    source_digest: str
    build_digest: str
    base_revision: str
    ready_at: datetime | None


class PreviewReadinessObservation(_ReadModel):
    """The newest readiness probe recorded against one runtime."""

    probe: str
    outcome: str
    observed_at: datetime


class PreviewRuntimeAllocationSummary(_ReadModel):
    """One Preview Runtime Allocation and the state of the runtime behind it.

    ``origin_host`` is the opaque per-session host the front door serves; it
    names no client product domain and grants no access by itself.
    """

    id: UUID
    allocation_kind: PreviewAllocationKind
    state: PreviewRuntimeAllocationState
    origin_host: str | None
    interview_session_id: UUID | None
    preview_version_id: UUID | None
    seed_manifest_digest: str | None
    readiness: PreviewReadinessObservation | None
    claimed_at: datetime | None
    released_at: datetime | None
    terminated_at: datetime | None


class CaseCleanupSummary(_ReadModel):
    """What one case still holds, and what it has already given back.

    ``complete`` is the conjunction below rather than a stored flag: no runtime
    is still allocated, no Environment Lease is still open, and no invitation
    can still be claimed. ``workspace_cleanup_state`` is reported beside it
    rather than folded into it, because a case that closes with no
    implementation has no workspace to destroy and is still clean.
    """

    preview_runtime_allocations_retained: int
    preview_runtime_allocations_released: int
    preview_runtime_allocations_terminated: int
    environment_leases_open: int
    invitations_outstanding: int
    workspace_cleanup_state: str | None
    complete: bool


async def load_case_outreach(
    database: Database, *, client_id: UUID, product_signal_id: UUID
) -> list[OutreachAttemptSummary]:
    """Return the case's Outreach Attempts and their invitations, oldest first."""
    attempts = await fetch_all(
        database,
        """
        select id, purpose, state, outreach_offer_id, scheduled_at, dispatched_at,
               responded_at, expires_at, reason
        from public.outreach_attempts
        where client_id = %s and product_signal_id = %s
        order by scheduled_at, id
        """,
        (client_id, product_signal_id),
    )
    invitations = await fetch_all(
        database,
        """
        select id, outreach_attempt_id, bound_provider, expires_at, claimed_at,
               revoked_at, interview_session_id
        from public.invitations
        where client_id = %s and product_signal_id = %s
        order by created_at, id
        """,
        (client_id, product_signal_id),
    )
    now = datetime.now(tz=UTC)
    by_attempt: dict[UUID, list[InvitationSummary]] = {}
    for row in invitations:
        summary = _invitation(row, now=now)
        by_attempt.setdefault(summary.outreach_attempt_id, []).append(summary)
    return [
        OutreachAttemptSummary(
            id=as_uuid(row, "id"),
            purpose=OutreachPurpose(as_str(row, "purpose")),
            state=OutreachAttemptState(as_str(row, "state")),
            outreach_offer_id=as_optional_uuid(row, "outreach_offer_id"),
            scheduled_at=as_datetime(row, "scheduled_at"),
            dispatched_at=_at(row, "dispatched_at"),
            responded_at=_at(row, "responded_at"),
            expires_at=as_datetime(row, "expires_at"),
            reason=as_optional_str(row, "reason"),
            invitations=by_attempt.get(as_uuid(row, "id"), []),
        )
        for row in attempts
    ]


def _invitation(row: DictRow, *, now: datetime) -> InvitationSummary:
    """Compose one invitation, deriving what became of it from its timestamps."""
    claimed_at = _at(row, "claimed_at")
    revoked_at = _at(row, "revoked_at")
    expires_at = as_datetime(row, "expires_at")
    return InvitationSummary(
        id=as_uuid(row, "id"),
        outreach_attempt_id=as_uuid(row, "outreach_attempt_id"),
        disposition=_disposition(
            claimed_at=claimed_at, revoked_at=revoked_at, expires_at=expires_at, now=now
        ),
        bound_provider=as_str(row, "bound_provider"),
        expires_at=expires_at,
        claimed_at=claimed_at,
        revoked_at=revoked_at,
        interview_session_id=as_optional_uuid(row, "interview_session_id"),
    )


def _disposition(
    *,
    claimed_at: datetime | None,
    revoked_at: datetime | None,
    expires_at: datetime,
    now: datetime,
) -> InvitationDisposition:
    """Order the four dispositions: a claim outlives a later revocation."""
    if claimed_at is not None:
        return InvitationDisposition.CLAIMED
    if revoked_at is not None:
        return InvitationDisposition.REVOKED
    if expires_at <= now:
        return InvitationDisposition.EXPIRED
    return InvitationDisposition.SENT


async def load_case_sessions(
    database: Database, *, client_id: UUID, product_signal_id: UUID
) -> list[CaseSessionSummary]:
    """Return the Interview Sessions one case ran, oldest first."""
    rows = await fetch_all(
        database,
        """
        select sessions.id, sessions.state, sessions.phase, sessions.created_at,
               sessions.updated_at, sessions.closed_at, sessions.closure_reason,
               coalesce(readiness.conversational_ready, false) as conversational_ready,
               coalesce(readiness.observation_ready, false) as observation_ready,
               sessions.character_state, sessions.character_revision,
               sessions.implementation_progress, sessions.muted, sessions.paused,
               exists (
                 select 1 from public.validation_attempts as attempts
                 where attempts.interview_session_id = sessions.id
               ) as is_validation
        from public.interview_sessions as sessions
        left join public.session_readiness as readiness
          on readiness.interview_session_id = sessions.id
        where sessions.client_id = %s and sessions.product_signal_id = %s
        order by sessions.created_at
        """,
        (client_id, product_signal_id),
    )
    pauses = await _pause_history(
        database,
        client_id=client_id,
        session_ids=[as_uuid(row, "id") for row in rows],
    )
    return [
        CaseSessionSummary(
            id=as_uuid(row, "id"),
            purpose="validation" if as_bool(row, "is_validation") else "interview",
            state=InterviewSessionState(as_str(row, "state")),
            phase=InterviewPhase(as_str(row, "phase")),
            readiness=SessionReadiness(
                conversational=as_bool(row, "conversational_ready"),
                observation=as_bool(row, "observation_ready"),
            ),
            character_state=AgentCharacterState(as_str(row, "character_state")),
            character_revision=as_int(row, "character_revision"),
            implementation_progress=ImplementationProgress(
                as_str(row, "implementation_progress")
            ),
            muted=as_bool(row, "muted"),
            paused=as_bool(row, "paused"),
            pauses=pauses.get(as_uuid(row, "id"), []),
            created_at=as_datetime(row, "created_at"),
            updated_at=as_datetime(row, "updated_at"),
            closed_at=_at(row, "closed_at"),
            closure_reason=as_optional_str(row, "closure_reason"),
        )
        for row in rows
    ]


async def _pause_history(
    database: Database, *, client_id: UUID, session_ids: list[UUID]
) -> dict[UUID, list[SessionPauseRecord]]:
    """Return each session's Pause and Resume facts, oldest first.

    The controller appends one immutable fact per Pause and per Resume, so the
    history is read from the event stream rather than from the current flag,
    which only says where the session ended up.
    """
    if not session_ids:
        return {}
    rows = await fetch_all(
        database,
        """
        select stream_id, event_type, occurred_at
        from public.events
        where client_id = %s and stream_kind = 'interview_session'
          and stream_id = any(%s) and event_type = any(%s)
        order by stream_id, sequence
        """,
        (client_id, session_ids, [_PAUSED_EVENT, _RESUMED_EVENT]),
    )
    history: dict[UUID, list[SessionPauseRecord]] = {}
    for row in rows:
        history.setdefault(as_uuid(row, "stream_id"), []).append(
            SessionPauseRecord(
                paused=as_str(row, "event_type") == _PAUSED_EVENT,
                occurred_at=as_datetime(row, "occurred_at"),
            )
        )
    return history


async def load_preview_versions(
    database: Database, *, client_id: UUID, product_signal_id: UUID
) -> list[PreviewVersionSummary]:
    """Return the candidate Preview Versions one case built, oldest first."""
    rows = await fetch_all(
        database,
        """
        select id, implementation_attempt_id, state, source_commit, source_digest,
               build_digest, base_revision, ready_at
        from public.preview_versions
        where client_id = %s and product_signal_id = %s
        order by created_at
        """,
        (client_id, product_signal_id),
    )
    return [
        PreviewVersionSummary(
            id=as_uuid(row, "id"),
            implementation_attempt_id=as_uuid(row, "implementation_attempt_id"),
            state=PreviewVersionState(as_str(row, "state")),
            source_commit=as_str(row, "source_commit"),
            source_digest=as_str(row, "source_digest"),
            build_digest=as_str(row, "build_digest"),
            base_revision=as_str(row, "base_revision"),
            ready_at=_at(row, "ready_at"),
        )
        for row in rows
    ]


async def load_preview_runtime_allocations(
    database: Database, *, client_id: UUID, product_signal_id: UUID
) -> list[PreviewRuntimeAllocationSummary]:
    """Return the case's Preview Runtime Allocations with their newest probe."""
    rows = await fetch_all(
        database,
        """
        select allocations.id, allocations.allocation_kind, allocations.state,
               allocations.origin_host, allocations.interview_session_id,
               allocations.preview_version_id, allocations.seed_manifest_digest,
               allocations.claimed_at, allocations.released_at,
               allocations.terminated_at,
               newest.probe, newest.outcome, newest.observed_at
        from public.preview_runtime_allocations as allocations
        left join lateral (
          select results.probe, results.outcome, results.observed_at
          from public.preview_readiness_results as results
          where results.preview_runtime_allocation_id = allocations.id
          order by results.observed_at desc
          limit 1
        ) as newest on true
        where allocations.client_id = %s and allocations.product_signal_id = %s
        order by allocations.created_at
        """,
        (client_id, product_signal_id),
    )
    return [
        PreviewRuntimeAllocationSummary(
            id=as_uuid(row, "id"),
            allocation_kind=PreviewAllocationKind(as_str(row, "allocation_kind")),
            state=PreviewRuntimeAllocationState(as_str(row, "state")),
            origin_host=as_optional_str(row, "origin_host"),
            interview_session_id=as_optional_uuid(row, "interview_session_id"),
            preview_version_id=as_optional_uuid(row, "preview_version_id"),
            seed_manifest_digest=as_optional_str(row, "seed_manifest_digest"),
            readiness=_readiness(row),
            claimed_at=_at(row, "claimed_at"),
            released_at=_at(row, "released_at"),
            terminated_at=_at(row, "terminated_at"),
        )
        for row in rows
    ]


def _readiness(row: DictRow) -> PreviewReadinessObservation | None:
    """Return the joined readiness probe, or nothing when none was recorded."""
    if row["probe"] is None:
        return None
    return PreviewReadinessObservation(
        probe=as_str(row, "probe"),
        outcome=as_str(row, "outcome"),
        observed_at=as_datetime(row, "observed_at"),
    )


async def load_case_cleanup(
    database: Database, *, client_id: UUID, product_signal_id: UUID
) -> CaseCleanupSummary:
    """Return what one case still holds and what it has already given back."""
    runtimes = await fetch_one(
        database,
        """
        select count(*) filter (where state = any(%s)) as retained,
               count(*) filter (where state = 'released') as released,
               count(*) filter (where state = 'terminated') as terminated
        from public.preview_runtime_allocations
        where client_id = %s and product_signal_id = %s
        """,
        (list(_RETAINED_ALLOCATION_STATES), client_id, product_signal_id),
    )
    if runtimes is None:
        raise TypeError("the preview runtime cleanup aggregate returned no row")
    leases = await fetch_one(
        database,
        """
        select count(*) as open
        from public.environment_leases as leases
        join public.implementation_attempts as attempts
          on attempts.id = leases.implementation_attempt_id
        where leases.client_id = %s and attempts.product_signal_id = %s
          and leases.state = any(%s)
        """,
        (client_id, product_signal_id, list(_OPEN_LEASE_STATES)),
    )
    if leases is None:
        raise TypeError("the Environment Lease cleanup aggregate returned no row")
    invitations = await fetch_one(
        database,
        """
        select count(*) as outstanding
        from public.invitations
        where client_id = %s and product_signal_id = %s
          and claimed_at is null and revoked_at is null and expires_at > now()
        """,
        (client_id, product_signal_id),
    )
    if invitations is None:
        raise TypeError("the invitation cleanup aggregate returned no row")
    workspace = await _workspace_cleanup_state(
        database, client_id=client_id, product_signal_id=product_signal_id
    )
    retained = as_int(runtimes, "retained")
    open_leases = as_int(leases, "open")
    outstanding = as_int(invitations, "outstanding")
    return CaseCleanupSummary(
        preview_runtime_allocations_retained=retained,
        preview_runtime_allocations_released=as_int(runtimes, "released"),
        preview_runtime_allocations_terminated=as_int(runtimes, "terminated"),
        environment_leases_open=open_leases,
        invitations_outstanding=outstanding,
        workspace_cleanup_state=workspace,
        complete=retained == 0 and open_leases == 0 and outstanding == 0,
    )


async def _workspace_cleanup_state(
    database: Database, *, client_id: UUID, product_signal_id: UUID
) -> str | None:
    """Return the cleanup the implementation workspace recorded for itself.

    Artifact extraction happens before the workspace is destroyed and records
    the outcome on the same immutable evidence record the Case File's
    implementation section cites, so the dashboard reads that record rather than
    asking the sandbox provider what is still running.
    """
    rows = await fetch_all(
        database,
        """
        select body from public.evidence_records
        where client_id = %s and product_signal_id = %s
          and evidence_class = 'implementation_artifact'
        order by recorded_at desc
        """,
        (client_id, product_signal_id),
    )
    for row in rows:
        body = row["body"]
        if not isinstance(body, Mapping):
            raise TypeError("evidence_records.body is not an object")
        state = body.get("cleanup_state")
        if isinstance(state, str):
            return state
    return None


def _at(row: DictRow, column: str) -> datetime | None:
    """Return a nullable timestamp column."""
    if row[column] is None:
        return None
    return as_datetime(row, column)
