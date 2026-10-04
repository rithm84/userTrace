"""The audited, deny-only client case stop.

An owner or admin withdraws further work authority from one nonterminal case.
The denial takes precedence immediately: it blocks new dispatch, revokes pending
invitations, fences leases, moves already-dispatched effects into reconciliation,
and schedules cleanup. It cannot mark evidence or validation successful, alter a
Case File, expand permissions, resume stopped work, or revoke a published output.

A stop before participation produces a ``canceled`` record and no Case File. A
stop after participation withdraws authority now and schedules the blocker Case
File; the Product Signal closes only once that Case File is published. Either
way, the stop schedules the private notice that tells the originating user.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    CommandState,
    EnvironmentLeaseState,
    InterviewPhase,
    InterviewSessionState,
    JobState,
    OutreachAttemptState,
    TechnicalCategory,
)

from app.auth.principals import StaffPrincipal
from app.db.kernel import append_fact, apply_transition_on_connection
from app.db.pool import Database
from app.db.rows import as_bool, as_int, as_str, as_uuid
from app.envelope import (
    Actor,
    AuditIntent,
    EventAppend,
    JobIntent,
    JsonValue,
    StreamRef,
)
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.extension.bindings import revoke_binding
from app.extension.commands import cancel_pending_case_visual_commands
from app.ids import uuid7
from app.intake.jobs import send_stop_notice_job
from app.interview.projection import publish_projection_on_connection
from app.lifecycle.interview import INTERVIEW_SESSION_MACHINE, InterviewSessionFacts
from app.lifecycle.machine import plan_transition
from app.lifecycle.product_signal import (
    NONTERMINAL_STATES,
    PRODUCT_SIGNAL_MACHINE,
    ProductSignalFacts,
)
from app.logging import get_logger
from app.offers.entitlement import CaseStopReason
from app.outputs.closure_jobs import entitlement_job

__all__ = [
    "CLOSE_CASE_AFTER_STOP",
    "CaseStopOutcome",
    "close_open_sessions",
    "participation_occurred",
    "stop_case",
]

_log = get_logger(__name__)

CLOSE_CASE_AFTER_STOP = "control_plane.close_case_after_stop"

_OPEN_OUTREACH: tuple[str, ...] = (
    OutreachAttemptState.SCHEDULED.value,
    OutreachAttemptState.DISPATCHING.value,
    OutreachAttemptState.AWAITING_RESPONSE.value,
)


@dataclass(frozen=True, slots=True)
class CaseStopOutcome:
    """What the stop withdrew, and whether the case is already terminal."""

    product_signal_id: UUID
    participation_occurred: bool
    terminal_state: str | None
    revoked_outreach_attempts: int
    fenced_leases: int
    canceled_jobs: int
    commands_in_reconciliation: int


async def participation_occurred(
    conn: AsyncConnection[DictRow], product_signal_id: UUID
) -> bool:
    """Participation begins when a session reached conversational readiness."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select exists (
                select 1 from public.interview_sessions as sessions
                join public.session_readiness as readiness
                  on readiness.interview_session_id = sessions.id
                where sessions.product_signal_id = %s
                  and readiness.conversational_ready
            ) as participated
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    return as_bool(row, "participated") if row is not None else False


async def _withdraw_authority(
    conn: AsyncConnection[DictRow], product_signal_id: UUID, cancellation_scope_id: UUID
) -> tuple[int, int, int, int]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.outreach_attempts
            set state = %s, reason = 'client_case_stop', revision = revision + 1,
                updated_at = now()
            where product_signal_id = %s and state = any(%s)
            """,
            (
                OutreachAttemptState.REVOKED.value,
                product_signal_id,
                list(_OPEN_OUTREACH),
            ),
        )
        revoked = cur.rowcount
        await cur.execute(
            """
            update public.invitations
            set revoked_at = now(), updated_at = now()
            where product_signal_id = %s and revoked_at is null and claimed_at is null
            """,
            (product_signal_id,),
        )
        await cur.execute(
            """
            update public.environment_leases
            set state = %s, revision = revision + 1, updated_at = now()
            where state = %s
              and implementation_attempt_id in (
                select id from public.implementation_attempts
                where product_signal_id = %s
              )
            """,
            (
                EnvironmentLeaseState.FENCED.value,
                EnvironmentLeaseState.ACTIVE.value,
                product_signal_id,
            ),
        )
        fenced = cur.rowcount
        await cur.execute(
            """
            update public.jobs
            set state = %s, revision = revision + 1, updated_at = now()
            where cancellation_scope_id = %s and state in (%s, %s)
            """,
            (
                JobState.CANCELED.value,
                cancellation_scope_id,
                JobState.PENDING.value,
                JobState.READY.value,
            ),
        )
        canceled = cur.rowcount
        await cur.execute(
            """
            update public.commands
            set state = %s, reconciling_at = now(), revision = revision + 1,
                updated_at = now()
            where cancellation_scope_id = %s
              and state in (%s, %s, %s)
            """,
            (
                CommandState.RECONCILING.value,
                cancellation_scope_id,
                CommandState.DISPATCHING.value,
                CommandState.DISPATCHED.value,
                CommandState.ACKNOWLEDGED.value,
            ),
        )
        reconciling = cur.rowcount
    return revoked, fenced, canceled, reconciling


async def close_open_sessions(
    conn: AsyncConnection[DictRow],
    *,
    product_signal_id: UUID,
    client_id: UUID,
    actor: Actor,
    now: datetime,
    closure_reason: str,
    event_type: str,
    audit_action: str,
) -> int:
    """Close the case's open Interview Sessions, so the participant's tab shows why.

    A terminal session immediately revokes its extension binding, in the same
    transaction as the close, so the extension loses command authority the
    moment the close commits.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select sessions.id, sessions.state, sessions.revision,
                   sessions.phase <> %s as participant_setup_passed,
                   coalesce(readiness.conversational_ready, false) as conversational_ready,
                   coalesce(readiness.observation_ready, false) as observation_ready
            from public.interview_sessions as sessions
            left join public.session_readiness as readiness
              on readiness.interview_session_id = sessions.id
            where sessions.product_signal_id = %s and sessions.state <> %s
            for update of sessions
            """,
            (
                InterviewPhase.PARTICIPANT_SETUP.value,
                product_signal_id,
                InterviewSessionState.CLOSED.value,
            ),
        )
        sessions = list(await cur.fetchall())
    for session in sessions:
        session_id = as_uuid(session, "id")
        stream = StreamRef(kind=AggregateKind.INTERVIEW_SESSION, id=session_id)
        await apply_transition_on_connection(
            conn,
            plan_transition(
                INTERVIEW_SESSION_MACHINE,
                InterviewSessionFacts(
                    identity_matches=True,
                    participant_setup_passed=as_bool(session, "participant_setup_passed"),
                    conversational_ready=as_bool(session, "conversational_ready"),
                    observation_ready=as_bool(session, "observation_ready"),
                ),
                trigger="close",
                current_state=as_str(session, "state"),
                client_id=client_id,
                aggregate_kind=AggregateKind.INTERVIEW_SESSION,
                aggregate_id=session_id,
                expected_revision=as_int(session, "revision"),
                state_column="state",
                mutation={
                    "closure_reason": closure_reason,
                    "closed_at": now,
                    "phase": InterviewPhase.CLOSING.value,
                    "paused": False,
                },
                event=EventAppend(
                    message_id=uuid7(),
                    stream=stream,
                    event_type=event_type,
                    payload={},
                    actor=actor,
                    causation_id=None,
                    correlation_id=product_signal_id,
                    occurred_at=now,
                ),
                jobs=(),
                commands=(),
                audit=AuditIntent(
                    audit_id=uuid7(),
                    subject=stream,
                    action=audit_action,
                    outcome="session_closed",
                    reason_code=closure_reason,
                    actor=actor,
                    pinned_configuration_release_id=None,
                    detail={},
                ),
                additional_locks=(),
            ),
        )
        await revoke_binding(conn, interview_session_id=session_id, reason=closure_reason)
        await publish_projection_on_connection(conn, interview_session_id=session_id)
    return len(sessions)


async def stop_case(
    database: Database,
    *,
    staff: StaffPrincipal,
    product_signal_id: UUID,
    reason: str,
    closure_deadline_seconds: int,
) -> CaseStopOutcome:
    """Withdraw further work authority from one nonterminal case."""
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select id, client_id, state, revision, cancellation_scope_id,
                       pinned_configuration_release_id
                from public.product_signals
                where id = %s and client_id = %s
                for update
                """,
                (product_signal_id, staff.client_id),
            )
            signal = await cur.fetchone()
        if signal is None:
            raise AggregateNotFoundError(
                table="product_signals", row_id=product_signal_id
            )

        state = as_str(signal, "state")
        if state not in NONTERMINAL_STATES:
            raise GuardRejectedError(
                machine="product_signal",
                trigger="client_stop",
                reason=f"the case is already terminal in state '{state}'",
            )

        participated = await participation_occurred(conn, product_signal_id)
        scope_id = as_uuid(signal, "cancellation_scope_id")
        revoked, fenced, canceled, reconciling = await _withdraw_authority(
            conn, product_signal_id, scope_id
        )

        actor = Actor(kind=ActorKind.CLIENT_MEMBER, ref=str(staff.client_member_id))
        now = datetime.now(tz=UTC)
        closed_sessions = await close_open_sessions(
            conn,
            product_signal_id=product_signal_id,
            client_id=staff.client_id,
            actor=actor,
            now=now,
            closure_reason="client_stopped",
            event_type="interview_session_closed_by_client_stop",
            audit_action="client_case_stop",
        )
        stream = StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=product_signal_id)
        detail: Mapping[str, JsonValue] = {
            "reason": reason,
            "participation_occurred": participated,
            "closed_interview_sessions": closed_sessions,
            "revoked_outreach_attempts": revoked,
            "fenced_leases": fenced,
            "canceled_jobs": canceled,
            "commands_in_reconciliation": reconciling,
            "staff_role": staff.role.value,
        }

        if participated:
            # Authority is withdrawn now; the blocker Case File closes the case.
            await append_fact(
                conn,
                client_id=staff.client_id,
                event=EventAppend(
                    message_id=uuid7(),
                    stream=stream,
                    event_type="client_case_stop_recorded",
                    payload=detail,
                    actor=actor,
                    causation_id=None,
                    correlation_id=product_signal_id,
                    occurred_at=now,
                ),
                audit=AuditIntent(
                    audit_id=uuid7(),
                    subject=stream,
                    action="client_case_stop",
                    outcome="authority_withdrawn",
                    reason_code="client_stop_after_participation",
                    actor=actor,
                    pinned_configuration_release_id=as_uuid(
                        signal, "pinned_configuration_release_id"
                    ),
                    detail=detail,
                ),
                jobs=(
                    JobIntent(
                        job_id=uuid7(),
                        job_type=CLOSE_CASE_AFTER_STOP,
                        input={
                            "product_signal_id": str(product_signal_id),
                            "reason": reason,
                        },
                        deadline_at=now + timedelta(seconds=closure_deadline_seconds),
                        timeout_seconds=300,
                        max_runs=5,
                        resource_budget={"kind": "closure"},
                        retryable_categories=[
                            TechnicalCategory.PROVIDER_UNAVAILABLE,
                            TechnicalCategory.RATE_LIMITED,
                            TechnicalCategory.TIMEOUT,
                        ],
                        backoff_base_seconds=30,
                        backoff_max_seconds=900,
                        concurrency_key=f"close_case:{product_signal_id}",
                        cancellation_scope_id=None,
                        not_before=now,
                    ),
                    send_stop_notice_job(
                        client_id=staff.client_id, product_signal_id=product_signal_id
                    ),
                ),
            )
            terminal_state = None
        else:
            request = plan_transition(
                PRODUCT_SIGNAL_MACHINE,
                ProductSignalFacts(
                    participation_occurred=False,
                    case_file_published=False,
                ),
                trigger="client_stop_before_participation",
                current_state=state,
                client_id=staff.client_id,
                aggregate_kind=AggregateKind.PRODUCT_SIGNAL,
                aggregate_id=product_signal_id,
                expected_revision=as_int(signal, "revision"),
                state_column="state",
                mutation={
                    "closure_reason": "client_stopped_before_participation",
                    "return_deadline_at": None,
                },
                event=EventAppend(
                    message_id=uuid7(),
                    stream=stream,
                    event_type="client_case_stopped_before_participation",
                    payload=detail,
                    actor=actor,
                    causation_id=None,
                    correlation_id=product_signal_id,
                    occurred_at=now,
                ),
                # No reward is owed before participation; evaluating it releases
                # the reservation back to the client's allowance.
                jobs=(
                    entitlement_job(
                        product_signal_id=product_signal_id,
                        stop_reason=CaseStopReason.CLIENT_STOP.value,
                        now=now,
                        deadline_seconds=closure_deadline_seconds,
                    ),
                    send_stop_notice_job(
                        client_id=staff.client_id, product_signal_id=product_signal_id
                    ),
                ),
                commands=(),
                audit=AuditIntent(
                    audit_id=uuid7(),
                    subject=stream,
                    action="client_case_stop",
                    outcome="canceled",
                    reason_code="client_stop_before_participation",
                    actor=actor,
                    pinned_configuration_release_id=as_uuid(
                        signal, "pinned_configuration_release_id"
                    ),
                    detail=detail,
                ),
                additional_locks=(),
            )
            outcome = await apply_transition_on_connection(conn, request)
            terminal_state = outcome.to_state

    _log.info(
        "client_case_stop",
        client_id=str(staff.client_id),
        product_signal_id=str(product_signal_id),
        participation_occurred=participated,
        terminal_state=terminal_state,
        revoked_outreach_attempts=revoked,
        fenced_leases=fenced,
        canceled_jobs=canceled,
        commands_in_reconciliation=reconciling,
    )
    # Visual commands are the extension's, outside the cancellation scope: the
    # closed sessions' pending ones are withdrawn once the stop is committed.
    await cancel_pending_case_visual_commands(
        database,
        client_id=staff.client_id,
        product_signal_id=product_signal_id,
        reason="client_stopped",
    )
    return CaseStopOutcome(
        product_signal_id=product_signal_id,
        participation_occurred=participated,
        terminal_state=terminal_state,
        revoked_outreach_attempts=revoked,
        fenced_leases=fenced,
        canceled_jobs=canceled,
        commands_in_reconciliation=reconciling,
    )
