"""Bounded recovery of a session preview whose preparation failed.

A failed preparation must not leave the participant waiting on a preview that
never arrives. The first failure pins a recovery deadline and commits a recovery
Job. Each recovery run retries readiness on the session's committed route, or
finishes preparing its runtime when no route was committed; a run that fails
commits the next one under the same deadline, so retrying never extends it.

The run at or after the deadline takes the technical-blocker path: the case's
open Interview Sessions close as blocked, and the case closes with its blocker
Case File after participation, or is canceled with no Case File before it. The
preview teardown scan destroys the runtime once the case is terminal.

Contract: docs/arch-components/failure-recovery.md#blockers-and-recovery
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

import httpx
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    CaseFileVariant,
    InterviewSessionState,
)

from app.connectors.failures import ConnectorFailure
from app.db.kernel import append_fact, apply_transition_on_connection
from app.db.pool import Database
from app.db.rows import as_int, as_str, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, JobIntent, JsonValue, StreamRef
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.ids import uuid7
from app.lifecycle.case_stop import close_open_sessions, participation_occurred
from app.lifecycle.machine import plan_transition
from app.lifecycle.product_signal import (
    NONTERMINAL_STATES,
    PRODUCT_SIGNAL_MACHINE,
    ProductSignalFacts,
)
from app.logging import get_logger
from app.offers.entitlement import CaseStopReason
from app.outputs.closure_jobs import (
    CASE_FILE_DEADLINE_SECONDS,
    case_file_assembly_job,
    entitlement_job,
)
from app.preview.readiness import DoorEntryError
from app.validation.return_expiry import withdraw_case_authority

__all__ = [
    "PREPARATION_FAILURES",
    "PREVIEW_UNRECOVERABLE",
    "RECOVERY_WINDOW",
    "RECOVER_SESSION_PREVIEW",
    "close_unrecoverable_session",
    "recovery_open",
    "schedule_recovery",
]

_log = get_logger(__name__)

RECOVER_SESSION_PREVIEW: Final[str] = "preview.recover_session_preview"
RECOVERY_WINDOW: Final[timedelta] = timedelta(minutes=15)
"""How long after the first failure a session preview may keep recovering."""
PREPARATION_FAILURES: Final[tuple[type[Exception], ...]] = (
    GuardRejectedError,
    ConnectorFailure,
    DoorEntryError,
    httpx.HTTPError,
)
"""The typed ways a preparation or recovery run fails that a later run may overcome."""
PREVIEW_UNRECOVERABLE: Final[str] = "session_preview_unrecoverable"

_RETRY_DELAY: Final[timedelta] = timedelta(seconds=60)
_CLAIM_GRACE: Final[timedelta] = timedelta(hours=1)
"""How long after the deadline the run that closes the session may still be claimed."""
_BLOCKER_REASON: Final[str] = (
    "the session preview could not be made ready before its recovery deadline"
)
_ACTOR = Actor(kind=ActorKind.SYSTEM, ref="preview.session_recovery")


def _recovery_job(
    *,
    product_signal_id: UUID,
    interview_session_id: UUID,
    cancellation_scope_id: UUID,
    recover_by: datetime,
    now: datetime,
) -> JobIntent:
    """Build the next recovery run, which belongs to the case's cancellation scope.

    It shares the preparation Job's concurrency key, so no two runs prepare the
    same case at once. A failed run commits its successor instead of retrying
    itself; the second run only covers a worker that died mid-run.
    """
    return JobIntent(
        job_id=uuid7(),
        job_type=RECOVER_SESSION_PREVIEW,
        input={
            "product_signal_id": str(product_signal_id),
            "interview_session_id": str(interview_session_id),
            "recover_by": recover_by.isoformat(),
        },
        deadline_at=recover_by + _CLAIM_GRACE,
        timeout_seconds=900,
        max_runs=2,
        resource_budget={"kind": RECOVER_SESSION_PREVIEW},
        retryable_categories=(),
        backoff_base_seconds=int(_RETRY_DELAY.total_seconds()),
        backoff_max_seconds=int(_RETRY_DELAY.total_seconds()),
        concurrency_key=f"preview_allocation:{product_signal_id}",
        cancellation_scope_id=cancellation_scope_id,
        not_before=min(now + _RETRY_DELAY, recover_by),
    )


async def schedule_recovery(
    database: Database,
    *,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
    recover_by: datetime,
    failure: str,
) -> None:
    """Record why the session preview is not ready and commit its next recovery run."""
    now = datetime.now(tz=UTC)
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select cancellation_scope_id from public.product_signals where id = %s",
                (product_signal_id,),
            )
            signal = await cur.fetchone()
        if signal is None:
            raise AggregateNotFoundError(table="product_signals", row_id=product_signal_id)
        job = _recovery_job(
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
            cancellation_scope_id=as_uuid(signal, "cancellation_scope_id"),
            recover_by=recover_by,
            now=now,
        )
        stream = StreamRef(kind=AggregateKind.INTERVIEW_SESSION, id=interview_session_id)
        detail: Mapping[str, JsonValue] = {
            "failure": failure,
            "recover_by": recover_by.isoformat(),
            "job_id": str(job.job_id),
        }
        await append_fact(
            conn,
            client_id=client_id,
            event=EventAppend(
                message_id=uuid7(),
                stream=stream,
                event_type="session_preview_recovery_scheduled",
                payload=detail,
                actor=_ACTOR,
                causation_id=None,
                correlation_id=product_signal_id,
                occurred_at=now,
            ),
            audit=AuditIntent(
                audit_id=uuid7(),
                subject=stream,
                action="schedule_session_preview_recovery",
                outcome="scheduled",
                reason_code="session_preview_not_ready",
                actor=_ACTOR,
                pinned_configuration_release_id=None,
                detail=detail,
            ),
            jobs=(job,),
        )
    _log.warning(
        "session_preview_recovery_scheduled",
        client_id=str(client_id),
        interview_session_id=str(interview_session_id),
        recover_by=recover_by.isoformat(),
        not_before=job.not_before.isoformat(),
        failure=failure,
    )


async def recovery_open(database: Database, *, interview_session_id: UUID) -> bool:
    """True while the session is open and its case nonterminal, so recovery still matters."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select sessions.state as session_state, signals.state as case_state
            from public.interview_sessions as sessions
            join public.product_signals as signals on signals.id = sessions.product_signal_id
            where sessions.id = %s
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="interview_sessions", row_id=interview_session_id)
    return (
        as_str(row, "session_state") != InterviewSessionState.CLOSED.value
        and as_str(row, "case_state") in NONTERMINAL_STATES
    )


async def close_unrecoverable_session(
    database: Database, *, product_signal_id: UUID, interview_session_id: UUID
) -> str | None:
    """Take the technical-blocker path for a session preview that never became ready.

    Returns what the case owes, or ``None`` when the session or the case already
    ended and recovery no longer applies.
    """
    now = datetime.now(tz=UTC)
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select client_id, state, revision, cancellation_scope_id,
                       pinned_configuration_release_id
                from public.product_signals where id = %s for update
                """,
                (product_signal_id,),
            )
            signal = await cur.fetchone()
            await cur.execute(
                "select state from public.interview_sessions where id = %s",
                (interview_session_id,),
            )
            session = await cur.fetchone()
        if signal is None:
            raise AggregateNotFoundError(table="product_signals", row_id=product_signal_id)
        if session is None:
            raise AggregateNotFoundError(
                table="interview_sessions", row_id=interview_session_id
            )
        state = as_str(signal, "state")
        if (
            state not in NONTERMINAL_STATES
            or as_str(session, "state") == InterviewSessionState.CLOSED.value
        ):
            return None

        client_id = as_uuid(signal, "client_id")
        participated = await participation_occurred(conn, product_signal_id)
        withdrawn = await withdraw_case_authority(
            conn,
            product_signal_id=product_signal_id,
            cancellation_scope_id=as_uuid(signal, "cancellation_scope_id"),
            reason=PREVIEW_UNRECOVERABLE,
        )
        closed_sessions = await close_open_sessions(
            conn,
            product_signal_id=product_signal_id,
            client_id=client_id,
            actor=_ACTOR,
            now=now,
            closure_reason="blocked",
            event_type="interview_session_closed_by_blocker",
            audit_action="close_unrecoverable_session_preview",
        )
        stream = StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=product_signal_id)
        detail: Mapping[str, JsonValue] = {
            "reason": PREVIEW_UNRECOVERABLE,
            "interview_session_id": str(interview_session_id),
            "participation_occurred": participated,
            "closed_interview_sessions": closed_sessions,
            "revoked_outreach_attempts": withdrawn.revoked_outreach_attempts,
            "fenced_leases": withdrawn.fenced_leases,
            "canceled_jobs": withdrawn.canceled_jobs,
            "commands_in_reconciliation": withdrawn.commands_in_reconciliation,
        }
        outcome = "blocker_case_file" if participated else "canceled"
        event = EventAppend(
            message_id=uuid7(),
            stream=stream,
            event_type="session_preview_unrecoverable",
            payload=detail,
            actor=_ACTOR,
            causation_id=None,
            correlation_id=product_signal_id,
            occurred_at=now,
        )
        audit = AuditIntent(
            audit_id=uuid7(),
            subject=stream,
            action="close_unrecoverable_session_preview",
            outcome=outcome,
            reason_code=PREVIEW_UNRECOVERABLE,
            actor=_ACTOR,
            pinned_configuration_release_id=as_uuid(signal, "pinned_configuration_release_id"),
            detail=detail,
        )
        entitlement = entitlement_job(
            product_signal_id=product_signal_id,
            stop_reason=CaseStopReason.TECHNICAL_BLOCKER.value,
            now=now,
            deadline_seconds=CASE_FILE_DEADLINE_SECONDS,
        )
        if participated:
            # Authority is withdrawn now; the blocker Case File closes the case.
            await append_fact(
                conn,
                client_id=client_id,
                event=event,
                audit=audit,
                jobs=(
                    case_file_assembly_job(
                        product_signal_id=product_signal_id,
                        validation_attempt_id=None,
                        variant=CaseFileVariant.BLOCKED,
                        blocker_reason=_BLOCKER_REASON,
                        closure_trigger="close_blocked",
                        closure_reason=PREVIEW_UNRECOVERABLE,
                        now=now,
                        deadline_seconds=CASE_FILE_DEADLINE_SECONDS,
                    ),
                    entitlement,
                ),
            )
        else:
            await apply_transition_on_connection(
                conn,
                plan_transition(
                    PRODUCT_SIGNAL_MACHINE,
                    ProductSignalFacts(
                        participation_occurred=False, case_file_published=False
                    ),
                    trigger="cancel",
                    current_state=state,
                    client_id=client_id,
                    aggregate_kind=AggregateKind.PRODUCT_SIGNAL,
                    aggregate_id=product_signal_id,
                    expected_revision=as_int(signal, "revision"),
                    state_column="state",
                    mutation={"closure_reason": PREVIEW_UNRECOVERABLE},
                    event=event,
                    jobs=(entitlement,),
                    commands=(),
                    audit=audit,
                    additional_locks=(),
                ),
            )
    return outcome
