"""Participant control semantics on top of the kernel's session machine.

The kernel owns the durable flags and the close transition. This module owns
what each control *means* for the rest of the runtime:

- **Mute / Unmute** changes caller input on the existing voice runtime. The
  extension never captures audio, so the control is a durable Command the
  sideband worker applies.
- **Pause** withdraws authority for voice, observation, and new live
  implementation steps. Observation readiness is withdrawn immediately, pending
  visual commands are canceled, the session's server SDKs are paused over their
  control connections, and already-dispatched operations settle or checkpoint
  with their outcomes recorded. It is not a spoken request for a quiet moment,
  which suppresses speech without touching authority.
- **Resume** rechecks readiness rather than restoring the readiness a previous
  interval left behind, resumes the same server streams, and old targets and
  scenes are not reused.
- **Finish later** checkpoints the encounter and starts the case-level return
  deadline, which is recorded once and never extended.
- **End** is the separate cancellation action: it revokes the binding, closes
  the voice connections, and cancels the Product Signal's nonterminal
  descendants.

Every consequence is committed durable state. The extension learns of it from
the control socket that serves the session, whichever process applied the
control; see `app.extension.delivery`.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    CommandState,
    JobState,
    VoiceConnectionState,
)

from app.auth.principals import ParticipantPrincipal
from app.db.kernel import append_fact, apply_transition_on_connection
from app.db.pool import Database
from app.db.rows import as_bool, as_int, as_str, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, StreamRef
from app.errors import AggregateNotFoundError
from app.extension.bindings import revoke_binding
from app.extension.commands import cancel_pending_visual_commands
from app.ids import uuid7
from app.ingestion.control_commands import ControlCommandKind, send_control_command
from app.ingestion.control_hub import ServerControlHub
from app.interview.character import CharacterState, set_character
from app.interview.projection import SessionProjection, publish_projection
from app.interview.readiness import (
    refresh_observation_readiness,
    withdraw_observation_readiness,
)
from app.interview.voice.connections import close_voice_connection
from app.lifecycle.machine import plan_transition
from app.lifecycle.product_signal import PRODUCT_SIGNAL_MACHINE, ProductSignalFacts
from app.lifecycle.session_controls import ControlAction, apply_control
from app.logging import get_logger
from app.offers.entitlement import CaseStopReason
from app.outputs.closure_jobs import CASE_FILE_DEADLINE_SECONDS, entitlement_job
from app.preview.ports import ObservationReadinessPort

__all__ = [
    "SET_INPUT_MUTED",
    "ControlResult",
    "apply_participant_control",
    "session_paused",
]

_log = get_logger(__name__)

SET_INPUT_MUTED = "voice.set_input_muted"

_PAUSE_REASON = "paused_by_participant"
_CLOSE_REASON = "session_closed"

#: The reasons the server control contract names for a participant's pause and
#: the resume that ends it.
_SERVER_PAUSE_REASON = "session_paused"
_SERVER_RESUME_REASON = "session_resumed"

_CHARACTER_BY_ACTION: dict[ControlAction, CharacterState] = {
    ControlAction.PAUSE: CharacterState.PAUSED,
    ControlAction.RESUME: CharacterState.LISTENING,
    ControlAction.FINISH_LATER: CharacterState.COMPLETION,
    ControlAction.END_CONFIRM: CharacterState.COMPLETION,
}

_CLOSING = frozenset({ControlAction.FINISH_LATER, ControlAction.END_CONFIRM})


@dataclass(frozen=True, slots=True)
class ControlResult:
    """The projection revision the surfaces reflect after one control."""

    projection: SessionProjection
    muted: bool
    paused: bool
    session_state: str


async def apply_participant_control(
    database: Database,
    *,
    participant: ParticipantPrincipal,
    interview_session_id: UUID,
    action: ControlAction,
    return_window_seconds: int,
    observation: ObservationReadinessPort | None,
    control_hub: ServerControlHub,
) -> ControlResult:
    """Apply one control and every consequence the contract attaches to it."""
    outcome = await apply_control(
        database,
        participant=participant,
        session_id=interview_session_id,
        action=action,
        return_window_seconds=return_window_seconds,
    )

    if action in (ControlAction.MUTE, ControlAction.UNMUTE):
        await _queue_input_mute(
            database,
            client_id=participant.client_id,
            interview_session_id=interview_session_id,
            muted=action is ControlAction.MUTE,
        )
    elif action is ControlAction.PAUSE:
        await _queue_input_mute(
            database,
            client_id=participant.client_id,
            interview_session_id=interview_session_id,
            muted=True,
        )
        await withdraw_observation_readiness(
            database,
            interview_session_id=interview_session_id,
            reason=_PAUSE_REASON,
        )
        await cancel_pending_visual_commands(
            database,
            client_id=participant.client_id,
            interview_session_id=interview_session_id,
            reason=_PAUSE_REASON,
        )
        await _command_server_streams(
            database,
            hub=control_hub,
            interview_session_id=interview_session_id,
            kind="pause",
            reason=_SERVER_PAUSE_REASON,
        )
        await _record_pause_fact(
            database,
            participant=participant,
            interview_session_id=interview_session_id,
            paused=True,
        )
    elif action is ControlAction.RESUME:
        await _queue_input_mute(
            database,
            client_id=participant.client_id,
            interview_session_id=interview_session_id,
            muted=False,
        )
        if observation is not None:
            await refresh_observation_readiness(
                database,
                client_id=participant.client_id,
                interview_session_id=interview_session_id,
                observation=observation,
            )
        await _command_server_streams(
            database,
            hub=control_hub,
            interview_session_id=interview_session_id,
            kind="resume",
            reason=_SERVER_RESUME_REASON,
        )
        await _record_pause_fact(
            database,
            participant=participant,
            interview_session_id=interview_session_id,
            paused=False,
        )
    elif action in _CLOSING:
        await _close_encounter(
            database,
            participant=participant,
            interview_session_id=interview_session_id,
            action=action,
        )

    async with database.transaction() as conn:
        await set_character(
            conn,
            interview_session_id=interview_session_id,
            state=_CHARACTER_BY_ACTION.get(action, CharacterState.LISTENING),
        )
    projection = await publish_projection(
        database, interview_session_id=interview_session_id
    )
    _log.info(
        "participant_control_applied",
        client_id=str(participant.client_id),
        interview_session_id=str(interview_session_id),
        control=action.value,
        projection_revision=projection.revision,
        state=projection.state,
    )
    return ControlResult(
        projection=projection,
        muted=outcome.muted,
        paused=outcome.paused,
        session_state=outcome.state,
    )


async def session_paused(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID
) -> bool:
    """True while Pause withholds authority for new live implementation steps."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select paused from public.interview_sessions where id = %s",
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="interview_sessions", row_id=interview_session_id
        )
    return as_bool(row, "paused")


async def _queue_input_mute(
    database: Database, *, client_id: UUID, interview_session_id: UUID, muted: bool
) -> UUID:
    """Queue the durable Command that changes caller input on the voice runtime.

    The sideband worker owns the provider connection, so the control reaches it
    as committed state rather than as an in-process call that a restart loses.
    """
    command_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.commands (
                id, client_id, command_type, state, target_kind, target_id, payload,
                declared_revisions, queued_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, now())
            """,
            (
                command_id,
                client_id,
                SET_INPUT_MUTED,
                CommandState.QUEUED.value,
                AggregateKind.INTERVIEW_SESSION.value,
                interview_session_id,
                Jsonb({"muted": muted}),
                Jsonb({}),
            ),
        )
    return command_id


async def _command_server_streams(
    database: Database,
    *,
    hub: ServerControlHub,
    interview_session_id: UUID,
    kind: ControlCommandKind,
    reason: str,
) -> None:
    """Put one control command on every server stream this process holds.

    Pause withdraws authority from the server SDKs that observe the session, not
    only from the browser. A stream whose control socket this process does not
    hold has no live connection to command here; the durable withdrawal of
    observation readiness is what governs it.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, server_stream_id from public.telemetry_activations
            where interview_session_id = %s and kind = 'server'
            """,
            (interview_session_id,),
        )
        activations = [
            (as_uuid(row, "id"), as_uuid(row, "server_stream_id"))
            for row in await cur.fetchall()
        ]
    for activation_id, server_stream_id in activations:
        if not hub.holds(server_stream_id):
            _log.info(
                "server_control_command_skipped",
                interview_session_id=str(interview_session_id),
                activation_id=str(activation_id),
                server_stream_id=str(server_stream_id),
                kind=kind,
            )
            continue
        await send_control_command(
            database,
            hub=hub,
            activation_id=activation_id,
            kind=kind,
            reason=reason,
        )


async def _record_pause_fact(
    database: Database,
    *,
    participant: ParticipantPrincipal,
    interview_session_id: UUID,
    paused: bool,
) -> None:
    """Append the fact the implementation controller reads to hold new steps."""
    actor = Actor(
        kind=ActorKind.CUSTOMER_PARTICIPANT, ref=str(participant.client_user_id)
    )
    stream = StreamRef(kind=AggregateKind.INTERVIEW_SESSION, id=interview_session_id)
    event_type = (
        "session_paused_live_implementation_held"
        if paused
        else "session_resumed_live_implementation_released"
    )
    async with database.transaction() as conn:
        await append_fact(
            conn,
            client_id=participant.client_id,
            event=EventAppend(
                message_id=uuid7(),
                stream=stream,
                event_type=event_type,
                payload={"blocks_new_live_implementation_steps": paused},
                actor=actor,
                causation_id=None,
                correlation_id=None,
                occurred_at=datetime.now(tz=UTC),
            ),
            audit=AuditIntent(
                audit_id=uuid7(),
                subject=stream,
                action=event_type,
                outcome="applied",
                reason_code=_PAUSE_REASON if paused else None,
                actor=actor,
                pinned_configuration_release_id=None,
                detail={},
            ),
            jobs=(),
        )


async def _close_encounter(
    database: Database,
    *,
    participant: ParticipantPrincipal,
    interview_session_id: UUID,
    action: ControlAction,
) -> None:
    """Revoke the binding, stop voice and observation, and clean up the encounter."""
    await cancel_pending_visual_commands(
        database,
        client_id=participant.client_id,
        interview_session_id=interview_session_id,
        reason=_CLOSE_REASON,
    )
    await withdraw_observation_readiness(
        database, interview_session_id=interview_session_id, reason=_CLOSE_REASON
    )
    async with database.transaction() as conn:
        await revoke_binding(
            conn, interview_session_id=interview_session_id, reason=action.value
        )
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.voice_connections
            where interview_session_id = %s and state <> %s
            """,
            (interview_session_id, VoiceConnectionState.CLOSED.value),
        )
        open_connections = tuple(as_uuid(row, "id") for row in await cur.fetchall())
    for connection_id in open_connections:
        await close_voice_connection(
            database,
            client_id=participant.client_id,
            connection_id=connection_id,
            reason=action.value,
            close_provider_session=True,
        )
    if action is ControlAction.END_CONFIRM:
        await _cancel_scope(
            database,
            client_id=participant.client_id,
            interview_session_id=interview_session_id,
        )


async def _cancel_scope(
    database: Database, *, client_id: UUID, interview_session_id: UUID
) -> None:
    """Cancel the case on End confirmation.

    End is the participant's cancellation action: the Product Signal becomes
    `canceled` with no Case File, undispatched work stops, dispatched effects
    enter reconciliation rather than being repeated, and the Outreach Offer is
    settled as a voluntary cancellation, which earns no reward. The preview
    teardown scan destroys the case's runtimes once it is terminal.
    """
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select signals.id, signals.cancellation_scope_id, signals.state,
                       signals.revision
                from public.interview_sessions as sessions
                join public.product_signals as signals
                  on signals.id = sessions.product_signal_id
                where sessions.id = %s
                """,
                (interview_session_id,),
            )
            row = await cur.fetchone()
            if row is None:
                raise AggregateNotFoundError(
                    table="interview_sessions", row_id=interview_session_id
                )
            scope_id = as_uuid(row, "cancellation_scope_id")
            product_signal_id = as_uuid(row, "id")
            await cur.execute(
                """
                update public.jobs
                set state = %s, revision = revision + 1, updated_at = now()
                where cancellation_scope_id = %s and state in (%s, %s)
                """,
                (
                    JobState.CANCELED.value,
                    scope_id,
                    JobState.PENDING.value,
                    JobState.READY.value,
                ),
            )
            canceled_jobs = cur.rowcount
            await cur.execute(
                """
                update public.commands
                set state = %s, reconciling_at = now(), revision = revision + 1,
                    updated_at = now()
                where cancellation_scope_id = %s and state in (%s, %s, %s)
                """,
                (
                    CommandState.RECONCILING.value,
                    scope_id,
                    CommandState.DISPATCHING.value,
                    CommandState.DISPATCHED.value,
                    CommandState.ACKNOWLEDGED.value,
                ),
            )
            reconciling = cur.rowcount
        actor = Actor(kind=ActorKind.CUSTOMER_PARTICIPANT, ref=None)
        now = datetime.now(tz=UTC)
        await apply_transition_on_connection(
            conn,
            plan_transition(
                PRODUCT_SIGNAL_MACHINE,
                ProductSignalFacts(
                    participation_occurred=False, case_file_published=False
                ),
                trigger="cancel",
                current_state=as_str(row, "state"),
                client_id=client_id,
                aggregate_kind=AggregateKind.PRODUCT_SIGNAL,
                aggregate_id=product_signal_id,
                expected_revision=as_int(row, "revision"),
                state_column="state",
                mutation={"closure_reason": "ended_by_participant"},
                event=EventAppend(
                    message_id=uuid7(),
                    stream=StreamRef(
                        kind=AggregateKind.PRODUCT_SIGNAL, id=product_signal_id
                    ),
                    event_type="participant_ended_interview",
                    payload={
                        "canceled_jobs": canceled_jobs,
                        "commands_in_reconciliation": reconciling,
                    },
                    actor=actor,
                    causation_id=None,
                    correlation_id=product_signal_id,
                    occurred_at=now,
                ),
                jobs=(
                    entitlement_job(
                        product_signal_id=product_signal_id,
                        stop_reason=CaseStopReason.PARTICIPANT_CANCELED.value,
                        now=now,
                        deadline_seconds=CASE_FILE_DEADLINE_SECONDS,
                    ),
                ),
                commands=(),
                audit=AuditIntent(
                    audit_id=uuid7(),
                    subject=StreamRef(
                        kind=AggregateKind.PRODUCT_SIGNAL, id=product_signal_id
                    ),
                    action="participant_end_confirmation",
                    outcome="canceled",
                    reason_code="ended_by_participant",
                    actor=actor,
                    pinned_configuration_release_id=None,
                    detail={
                        "canceled_jobs": canceled_jobs,
                        "commands_in_reconciliation": reconciling,
                    },
                ),
                additional_locks=(),
            ),
        )
    _log.info(
        "participant_cancellation_scope_withdrawn",
        client_id=str(client_id),
        interview_session_id=str(interview_session_id),
        canceled_jobs=canceled_jobs,
        commands_in_reconciliation=reconciling,
    )
