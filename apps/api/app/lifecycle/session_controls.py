"""Participant session controls and the preflight boundary.

Mute and Pause are durable control flags on a live session, not lifecycle states.
Pause withdraws authority for voice, observation, and new live implementation
steps; already-dispatched work settles or checkpoints. Finish later and End close
the encounter. Every control writes its own sequenced fact and audit record.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    InterviewPhase,
    InterviewSessionState,
    ProductSignalState,
)

from app.auth.principals import ParticipantPrincipal
from app.db.kernel import ColumnValue, append_fact, apply_transition_on_connection
from app.db.pool import Database
from app.db.rows import as_bool, as_int, as_str, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, StreamRef
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.ids import uuid7
from app.lifecycle.interview import INTERVIEW_SESSION_MACHINE, InterviewSessionFacts
from app.lifecycle.machine import plan_transition
from app.lifecycle.product_signal import ProductSignalFacts
from app.lifecycle.signal_transitions import advance_product_signal_on_connection
from app.logging import get_logger
from app.validation.controller import record_departure

__all__ = [
    "ControlAction",
    "ControlOutcome",
    "PreflightReport",
    "apply_control",
    "run_preflight",
]

_log = get_logger(__name__)


class ControlAction(StrEnum):
    """The controls the session tab and the extension expose."""

    MUTE = "mute"
    UNMUTE = "unmute"
    PAUSE = "pause"
    RESUME = "resume"
    FINISH_LATER = "finish_later"
    END_CONFIRM = "end_confirm"


@dataclass(frozen=True, slots=True)
class ControlOutcome:
    """Durable control state after one action."""

    session_id: UUID
    state: str
    muted: bool
    paused: bool


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """What the participant compatibility preflight observed."""

    browser_supported: bool
    extension_installed: bool
    permissions_granted: bool
    contract_version_supported: bool

    @property
    def passed(self) -> bool:
        """Participant setup passes only when every required check passes."""
        return (
            self.browser_supported
            and self.extension_installed
            and self.permissions_granted
            and self.contract_version_supported
        )


_FLAG_BY_ACTION: dict[ControlAction, tuple[str, bool]] = {
    ControlAction.MUTE: ("muted", True),
    ControlAction.UNMUTE: ("muted", False),
    ControlAction.PAUSE: ("paused", True),
    ControlAction.RESUME: ("paused", False),
}

_CLOSING_ACTIONS = frozenset({ControlAction.FINISH_LATER, ControlAction.END_CONFIRM})


async def run_preflight(
    database: Database,
    *,
    participant: ParticipantPrincipal,
    session_id: UUID,
    report: PreflightReport,
) -> ControlOutcome:
    """Record the preflight result and admit the session to preparation."""
    async with database.transaction() as conn:
        session = await _lock_session(conn, session_id)
        if not report.passed:
            raise GuardRejectedError(
                machine="interview_session",
                trigger="begin_preparation",
                reason=(
                    "participant setup failed: "
                    f"browser_supported={report.browser_supported}, "
                    f"extension_installed={report.extension_installed}, "
                    f"permissions_granted={report.permissions_granted}, "
                    f"contract_version_supported={report.contract_version_supported}"
                ),
            )
        actor = Actor(
            kind=ActorKind.CUSTOMER_PARTICIPANT, ref=str(participant.client_user_id)
        )
        now = datetime.now(tz=UTC)
        await apply_transition_on_connection(
            conn,
            plan_transition(
                INTERVIEW_SESSION_MACHINE,
                InterviewSessionFacts(
                    identity_matches=True,
                    participant_setup_passed=True,
                    conversational_ready=False,
                    observation_ready=False,
                ),
                trigger="begin_preparation",
                current_state=as_str(session, "state"),
                client_id=participant.client_id,
                aggregate_kind=AggregateKind.INTERVIEW_SESSION,
                aggregate_id=session_id,
                expected_revision=as_int(session, "revision"),
                state_column="state",
                mutation={"phase": InterviewPhase.GREETING.value},
                event=EventAppend(
                    message_id=uuid7(),
                    stream=StreamRef(
                        kind=AggregateKind.INTERVIEW_SESSION, id=session_id
                    ),
                    event_type="participant_preflight_passed",
                    payload={
                        "browser_supported": report.browser_supported,
                        "extension_installed": report.extension_installed,
                        "permissions_granted": report.permissions_granted,
                        "contract_version_supported": report.contract_version_supported,
                    },
                    actor=actor,
                    causation_id=None,
                    correlation_id=as_uuid(session, "product_signal_id"),
                    occurred_at=now,
                ),
                jobs=(),
                commands=(),
                audit=AuditIntent(
                    audit_id=uuid7(),
                    subject=StreamRef(
                        kind=AggregateKind.INTERVIEW_SESSION, id=session_id
                    ),
                    action="participant_preflight",
                    outcome="passed",
                    reason_code=None,
                    actor=actor,
                    pinned_configuration_release_id=None,
                    detail={},
                ),
                additional_locks=(),
            ),
        )
        # Participant setup passing is what lets the interview begin, so the Product
        # Signal starts interviewing in the same transaction. A reconnect that
        # repeats preflight finds it already interviewing and moves nothing.
        product_signal_id = as_uuid(session, "product_signal_id")
        async with conn.cursor() as cur:
            await cur.execute(
                "select state from public.product_signals where id = %s", (product_signal_id,)
            )
            signal = await cur.fetchone()
        if signal is not None and as_str(signal, "state") == ProductSignalState.AWAITING_INTERVIEW.value:
            await advance_product_signal_on_connection(
                conn,
                client_id=participant.client_id,
                product_signal_id=product_signal_id,
                trigger="setup_ready",
                facts=ProductSignalFacts(participation_occurred=False, case_file_published=False),
                actor=actor,
                payload={"interview_session_id": str(session_id)},
                jobs=(),
                reason_code=None,
            )
        session = await _lock_session(conn, session_id)
    return _outcome(session_id, session)


async def apply_control(
    database: Database,
    *,
    participant: ParticipantPrincipal,
    session_id: UUID,
    action: ControlAction,
    return_window_seconds: int,
) -> ControlOutcome:
    """Apply one participant control to the durable session projection."""
    async with database.transaction() as conn:
        session = await _lock_session(conn, session_id)
        state = as_str(session, "state")
        actor = Actor(
            kind=ActorKind.CUSTOMER_PARTICIPANT, ref=str(participant.client_user_id)
        )
        now = datetime.now(tz=UTC)
        stream = StreamRef(kind=AggregateKind.INTERVIEW_SESSION, id=session_id)

        if action in _CLOSING_ACTIONS:
            await _close_session(
                conn,
                participant=participant,
                session=session,
                session_id=session_id,
                action=action,
                actor=actor,
                now=now,
                return_window_seconds=return_window_seconds,
            )
        else:
            if state != InterviewSessionState.LIVE.value:
                raise GuardRejectedError(
                    machine="interview_session",
                    trigger=action.value,
                    reason=f"controls apply only to a live session, found '{state}'",
                )
            column, value = _FLAG_BY_ACTION[action]
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    update public.interview_sessions
                    set {column} = %s, revision = revision + 1, updated_at = now()
                    where id = %s
                    """,  # the column name comes from a fixed internal map
                    (value, session_id),
                )
            await append_fact(
                conn,
                client_id=participant.client_id,
                event=EventAppend(
                    message_id=uuid7(),
                    stream=stream,
                    event_type=f"session_control_{action.value}",
                    payload={"control": action.value, column: value},
                    actor=actor,
                    causation_id=None,
                    correlation_id=as_uuid(session, "product_signal_id"),
                    occurred_at=now,
                ),
                audit=AuditIntent(
                    audit_id=uuid7(),
                    subject=stream,
                    action=f"session_control_{action.value}",
                    outcome="applied",
                    reason_code=None,
                    actor=actor,
                    pinned_configuration_release_id=None,
                    detail={column: value},
                ),
                jobs=(),
            )
        session = await _lock_session(conn, session_id)
    _log.info(
        "session_control_applied",
        client_id=str(participant.client_id),
        interview_session_id=str(session_id),
        control=action.value,
    )
    return _outcome(session_id, session)


async def _close_session(
    conn: AsyncConnection[DictRow],
    *,
    participant: ParticipantPrincipal,
    session: DictRow,
    session_id: UUID,
    action: ControlAction,
    actor: Actor,
    now: datetime,
    return_window_seconds: int,
) -> None:
    """Close the encounter and, for finish later, open the bounded return window."""
    mutation: dict[str, ColumnValue] = {
        "closure_reason": action.value,
        "closed_at": now,
        "phase": InterviewPhase.CLOSING.value,
        "paused": False,
    }
    await apply_transition_on_connection(
        conn,
        plan_transition(
            INTERVIEW_SESSION_MACHINE,
            InterviewSessionFacts(
                identity_matches=True,
                participant_setup_passed=True,
                conversational_ready=as_bool(session, "conversational_ready"),
                observation_ready=as_bool(session, "observation_ready"),
            ),
            trigger="close",
            current_state=as_str(session, "state"),
            client_id=participant.client_id,
            aggregate_kind=AggregateKind.INTERVIEW_SESSION,
            aggregate_id=session_id,
            expected_revision=as_int(session, "revision"),
            state_column="state",
            mutation=mutation,
            event=EventAppend(
                message_id=uuid7(),
                stream=StreamRef(kind=AggregateKind.INTERVIEW_SESSION, id=session_id),
                event_type=f"session_control_{action.value}",
                payload={"control": action.value},
                actor=actor,
                causation_id=None,
                correlation_id=as_uuid(session, "product_signal_id"),
                occurred_at=now,
            ),
            jobs=(),
            commands=(),
            audit=AuditIntent(
                audit_id=uuid7(),
                subject=StreamRef(kind=AggregateKind.INTERVIEW_SESSION, id=session_id),
                action=f"session_control_{action.value}",
                outcome="session_closed",
                reason_code=action.value,
                actor=actor,
                pinned_configuration_release_id=None,
                detail={"return_window_seconds": return_window_seconds},
            ),
            additional_locks=(),
        ),
    )
    if action is ControlAction.FINISH_LATER:
        await record_departure(
            conn,
            product_signal_id=as_uuid(session, "product_signal_id"),
            reason=action.value,
            return_window_seconds=return_window_seconds,
        )


async def _lock_session(
    conn: AsyncConnection[DictRow], session_id: UUID
) -> DictRow:
    """Lock one session row and return its current control projection."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select sessions.id, sessions.client_id, sessions.product_signal_id,
                   sessions.state, sessions.revision, sessions.muted, sessions.paused,
                   coalesce(readiness.conversational_ready, false)
                     as conversational_ready,
                   coalesce(readiness.observation_ready, false) as observation_ready
            from public.interview_sessions as sessions
            left join public.session_readiness as readiness
              on readiness.interview_session_id = sessions.id
            where sessions.id = %s
            for update of sessions
            """,
            (session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="interview_sessions", row_id=session_id)
    return row


def _outcome(session_id: UUID, session: DictRow) -> ControlOutcome:
    """Project the locked session row into the control response."""
    return ControlOutcome(
        session_id=session_id,
        state=as_str(session, "state"),
        muted=as_bool(session, "muted"),
        paused=as_bool(session, "paused"),
    )
