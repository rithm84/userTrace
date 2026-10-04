"""Completing an invitation claim.

The claim proves control of the invited identity, opens the Interview Session
through the control-plane kernel, and commits the Job that prepares the session's
base preview. Another person is rejected by the kernel before any session exists,
so nothing here relaxes that check.

Claiming is idempotent: a reconnect returns the same Interview Session, and only
the claim that opened the session commits the preparation Job, so repeating it
does not prepare twice.

Contract: docs/arch-components/identity-and-access.md#participant-invitation-and-sign-in
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from utrace_contracts.domain.lifecycle import ActorKind, AggregateKind

from app.db.kernel import append_fact
from app.db.pool import Database
from app.db.rows import as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, StreamRef
from app.errors import AggregateNotFoundError
from app.ids import uuid7
from app.implementation.job_types import investigate_code_job, prepare_workspace_job
from app.intake.jobs import prepare_base_allocation_job
from app.lifecycle.claims import claim_invitation
from app.logging import get_logger

__all__ = ["ClaimResult", "claim_and_prepare"]

_log = get_logger(__name__)
_ACTOR = Actor(kind=ActorKind.SERVICE, ref="outreach.claim")


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """The Interview Session the claim opened, and its preview preparation Job."""

    interview_session_id: UUID
    product_signal_id: UUID
    already_claimed: bool
    preparation_job_id: UUID | None
    """The Job this claim committed; ``None`` for a reconnect to a claimed session."""


async def claim_and_prepare(
    database: Database,
    *,
    token: str,
    auth_user_id: UUID,
) -> ClaimResult:
    """Claim one invitation, open its session, and commit its preview preparation."""
    outcome = await claim_invitation(database, token=token, auth_user_id=auth_user_id)
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select sessions.client_id, sessions.product_signal_id,
                   signals.cancellation_scope_id
            from public.interview_sessions as sessions
            join public.product_signals as signals on signals.id = sessions.product_signal_id
            where sessions.id = %s
            """,
            (outcome.interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="interview_sessions", row_id=outcome.interview_session_id
        )
    client_id = as_uuid(row, "client_id")
    product_signal_id = as_uuid(row, "product_signal_id")
    job_id = (
        None
        if outcome.already_claimed
        else await _commit_preparation(
            database,
            client_id=client_id,
            product_signal_id=product_signal_id,
            interview_session_id=outcome.interview_session_id,
            cancellation_scope_id=as_uuid(row, "cancellation_scope_id"),
        )
    )
    _log.info(
        "invitation_claim_completed",
        client_id=str(client_id),
        product_signal_id=str(product_signal_id),
        interview_session_id=str(outcome.interview_session_id),
        already_claimed=outcome.already_claimed,
        preparation_job_id=None if job_id is None else str(job_id),
    )
    return ClaimResult(
        interview_session_id=outcome.interview_session_id,
        product_signal_id=product_signal_id,
        already_claimed=outcome.already_claimed,
        preparation_job_id=job_id,
    )


async def _commit_preparation(
    database: Database,
    *,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
    cancellation_scope_id: UUID,
) -> UUID:
    """Commit the Jobs that get the case ready while the interview runs, with the audit fact.

    Beside the session's base preview, they investigate the code for the concern
    intake recorded and prepare the workspace an Implementation Attempt would claim,
    so neither waits for the participant's agreement.
    """
    now = datetime.now(tz=UTC)
    job = prepare_base_allocation_job(
        client_id=client_id,
        product_signal_id=product_signal_id,
        interview_session_id=interview_session_id,
        cancellation_scope_id=cancellation_scope_id,
    )
    ahead = (
        investigate_code_job(
            product_signal_id=product_signal_id,
            cancellation_scope_id=cancellation_scope_id,
            now=now,
        ),
        prepare_workspace_job(
            product_signal_id=product_signal_id,
            cancellation_scope_id=cancellation_scope_id,
            now=now,
        ),
    )
    stream = StreamRef(kind=AggregateKind.INTERVIEW_SESSION, id=interview_session_id)
    async with database.transaction() as conn:
        await append_fact(
            conn,
            client_id=client_id,
            event=EventAppend(
                message_id=uuid7(),
                stream=stream,
                event_type="preview_preparation_requested",
                payload={"product_signal_id": str(product_signal_id), "job_id": str(job.job_id)},
                actor=_ACTOR,
                causation_id=product_signal_id,
                correlation_id=interview_session_id,
                occurred_at=now,
            ),
            audit=AuditIntent(
                audit_id=uuid7(),
                subject=stream,
                action="request_preview_preparation",
                outcome="requested",
                reason_code=None,
                actor=_ACTOR,
                pinned_configuration_release_id=None,
                detail={"job_id": str(job.job_id)},
            ),
            jobs=(job, *ahead),
        )
    return job.job_id
