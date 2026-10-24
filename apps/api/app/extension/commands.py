"""Visual-command dispatch and its single terminal result.

Every browser command carries its command id, client, session, binding, document
attachment, target ids and revisions, typed arguments, authority references,
expected lifecycle revision, and deadline. Commands address an exact registered
target revision: selectors, coordinates, and expressions are not representable
anywhere in the protocol.

Authorization is deny-wins and rechecked immediately before dispatch: session
state, the observation readiness boundary, the participant's own preferences,
the pinned Autonomy Policy's permitted command classes, the current binding, and
target freshness. A failure returns a specific reason instead of a guessed
element.

Dispatch records the authorized command durably and ends there. The control
socket that holds the bound installation's connection routes it, whichever
process that socket lives in, so the dispatching process never needs a socket
of its own; see `app.extension.delivery`. Dispatch success is separate from the
product's observed effect. The extension returns one canonical terminal outcome
per command id, which is recorded through the kernel's Command and Result
contract.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field
from utrace_contracts.domain.lifecycle import (
    AggregateKind,
    CommandDisposition,
    CommandState,
    EffectState,
    InterviewSessionState,
)

from app.db.pool import Database
from app.db.rows import as_bool, as_int, as_str, as_uuid
from app.errors import AggregateNotFoundError
from app.execution.commands import TerminalResult, record_terminal_result
from app.extension.protocol import CommandResultFrame
from app.ids import uuid7
from app.ingestion.target_registry import load_registered_targets
from app.logging import get_logger

__all__ = [
    "OUTCOME_UNKNOWN",
    "VISUAL_COMMAND_TYPE",
    "ClearSceneCommand",
    "DispatchOutcome",
    "Scene",
    "SceneCommand",
    "ScrollIntoViewCommand",
    "VisualCommand",
    "cancel_pending_case_visual_commands",
    "cancel_pending_visual_commands",
    "dispatch_visual_command",
    "record_visual_command_result",
    "settle_unknown_outcomes",
]

_log = get_logger(__name__)

VISUAL_COMMAND_TYPE = "extension.visual_command"
OUTCOME_UNKNOWN = "outcome_unknown"
"""What an interrupted command with no confirmed result is recorded as."""

# The pilot's permitted classes, used when a configuration release does not
# narrow them. A release that names classes is authoritative and can only
# narrow: installing the extension never broadens browser authority.
_PILOT_DEFAULT_CLASSES: frozenset[str] = frozenset(
    {"scene", "scroll_into_view", "clear_scene"}
)
_POLICY_KEY = "visual_command_classes"

_DISPOSITION_BY_OUTCOME: Mapping[str, tuple[CommandDisposition, EffectState]] = {
    "applied": (CommandDisposition.SUCCEEDED, EffectState.COMPLETED),
    "rejected": (CommandDisposition.REJECTED, EffectState.ABSENT),
    "failed": (CommandDisposition.FAILED, EffectState.INDETERMINATE),
    "stale_target": (CommandDisposition.REJECTED, EffectState.ABSENT),
    "canceled": (CommandDisposition.CANCELED, EffectState.ABSENT),
    "timed_out": (CommandDisposition.EXPIRED, EffectState.INDETERMINATE),
}

_OPAQUE_ID = r"^[A-Za-z0-9_.:-]{1,128}$"


class _Step(BaseModel):
    """Immutable, closed base for every scene step."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class MoveStep(_Step):
    """Move the agent cursor to a registered target."""

    step: Literal["move"]
    target_id: str = Field(pattern=_OPAQUE_ID)
    target_revision: Annotated[int, Field(ge=0)]


class PointStep(_Step):
    """Point at a registered target and dwell there."""

    step: Literal["point"]
    target_id: str = Field(pattern=_OPAQUE_ID)
    target_revision: Annotated[int, Field(ge=0)]
    dwell_ms: Annotated[int, Field(ge=0, le=5000)]


class CircleStep(_Step):
    """Circle a registered target, optionally retaining the mark."""

    step: Literal["circle"]
    target_id: str = Field(pattern=_OPAQUE_ID)
    target_revision: Annotated[int, Field(ge=0)]
    retain: bool


class TraceStep(_Step):
    """Trace one edge of a registered target."""

    step: Literal["trace"]
    target_id: str = Field(pattern=_OPAQUE_ID)
    target_revision: Annotated[int, Field(ge=0)]
    edge: Literal["top", "bottom", "leading", "trailing"]
    retain: bool


class LabelStep(_Step):
    """Label a registered target with short customer-language text."""

    step: Literal["label"]
    target_id: str = Field(pattern=_OPAQUE_ID)
    target_revision: Annotated[int, Field(ge=0)]
    text: str = Field(min_length=1, max_length=80)
    placement: Literal["above", "below", "leading", "trailing"]
    retain: bool


class ClearStep(_Step):
    """Clear the retained marks of the current scene."""

    step: Literal["clear"]


type SceneStep = Annotated[
    MoveStep | PointStep | CircleStep | TraceStep | LabelStep | ClearStep,
    Field(discriminator="step"),
]


class Scene(_Step):
    """One atomic scene: one agent cursor plus ordered gestures and marks."""

    scene_id: UUID
    steps: Annotated[Sequence[SceneStep], Field(min_length=1, max_length=12)]


class SceneCommand(_Step):
    """Play one scene."""

    type: Literal["scene"]
    scene: Scene


class ScrollIntoViewCommand(_Step):
    """Scrolling a registered target into view is its own command."""

    type: Literal["scroll_into_view"]
    target_id: str = Field(pattern=_OPAQUE_ID)
    target_revision: Annotated[int, Field(ge=0)]
    alignment: Literal["start", "center", "end", "nearest"]


class ClearSceneCommand(_Step):
    """Clear the current scene as a unit."""

    type: Literal["clear_scene"]


type VisualCommand = Annotated[
    SceneCommand | ScrollIntoViewCommand | ClearSceneCommand,
    Field(discriminator="type"),
]


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """Whether the command was dispatched, and why it was refused if not."""

    dispatched: bool
    command_id: UUID | None
    reason: str | None


async def dispatch_visual_command(
    database: Database,
    *,
    client_id: UUID,
    interview_session_id: UUID,
    command: VisualCommand,
    deadline_seconds: int,
) -> DispatchOutcome:
    """Authorize one visual command and record it for the bound document.

    The committed record is the whole dispatch: the socket holding the bound
    installation's connection is woken by it and routes the command.
    """
    async with database.connection() as conn:
        context = await _authorization_context(
            conn, interview_session_id=interview_session_id
        )
    if context.refusal is not None:
        return DispatchOutcome(dispatched=False, command_id=None, reason=context.refusal)
    if command.type not in context.permitted_classes:
        return DispatchOutcome(
            dispatched=False, command_id=None, reason="command_class_not_permitted"
        )
    if command.type == "scene" and not context.agent_cursor_enabled:
        return DispatchOutcome(
            dispatched=False, command_id=None, reason="agent_cursor_disabled"
        )

    targets = _targets(command)
    # Freshness is rechecked against the bound document's registry immediately
    # before dispatch: a target the runtime re-registered has moved on, and a
    # gesture for its old revision is refused rather than retargeted.
    registered = {
        target.target_id: target.target_revision
        for target in await load_registered_targets(
            database,
            client_id=client_id,
            interview_session_id=interview_session_id,
            document_attachment_id=context.document_attachment_id,
        )
    }
    for target_id, target_revision in targets:
        observed = registered.get(target_id)
        if observed is None:
            return DispatchOutcome(
                dispatched=False, command_id=None, reason="unknown_target"
            )
        if observed != target_revision:
            return DispatchOutcome(
                dispatched=False, command_id=None, reason="stale_target"
            )

    command_id = uuid7()
    deadline_at = datetime.now(tz=UTC) + timedelta(seconds=deadline_seconds)
    payload = command.model_dump(mode="json")
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
                VISUAL_COMMAND_TYPE,
                CommandState.QUEUED.value,
                AggregateKind.INTERVIEW_SESSION.value,
                interview_session_id,
                Jsonb(payload),
                Jsonb(
                    {
                        "lifecycle_revision": context.session_revision,
                        "readiness_revision": context.readiness_revision,
                        "binding_revision": context.binding_revision,
                        "preferences_revision": context.preferences_revision,
                        "targets": [
                            {"target_id": target, "target_revision": revision}
                            for target, revision in targets
                        ],
                    }
                ),
            ),
        )
        await cur.execute(
            """
            insert into public.visual_command_dispatches (
                command_id, client_id, interview_session_id, extension_binding_id,
                document_attachment_id, command_kind, targets,
                expected_lifecycle_revision, deadline_at, policy_decision
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                command_id,
                client_id,
                interview_session_id,
                context.extension_binding_id,
                context.document_attachment_id,
                command.type,
                Jsonb(
                    [
                        {"target_id": target, "target_revision": revision}
                        for target, revision in targets
                    ]
                ),
                context.session_revision,
                deadline_at,
                Jsonb(
                    {
                        "permitted_classes": sorted(context.permitted_classes),
                        "visual_guidance_enabled": True,
                        "agent_cursor_enabled": context.agent_cursor_enabled,
                        "observation_ready": True,
                    }
                ),
            ),
        )
    _log.info(
        "visual_command_recorded",
        client_id=str(client_id),
        interview_session_id=str(interview_session_id),
        command_id=str(command_id),
        command_kind=command.type,
        target_count=len(targets),
    )
    return DispatchOutcome(dispatched=True, command_id=command_id, reason=None)


async def record_visual_command_result(
    database: Database, *, client_id: UUID, frame: CommandResultFrame
) -> TerminalResult:
    """Record the extension's single canonical terminal outcome for one command."""
    payload = frame.payload
    disposition, effect_state = _DISPOSITION_BY_OUTCOME[payload.outcome]
    result = await record_terminal_result(
        database,
        message_id=frame.message_id,
        client_id=client_id,
        source="browser_extension",
        result=TerminalResult(
            result_id=uuid7(),
            command_id=payload.command_id,
            disposition=disposition,
            effect_state=effect_state,
            technical_category=payload.reason,
            retry_advice=None,
            payload={
                "outcome": payload.outcome,
                "reason": payload.reason,
                "extension_binding_id": str(payload.extension_binding_id),
                "document_attachment_id": str(payload.document_attachment_id),
                "observed_targets": [
                    {
                        "target_id": target.target_id,
                        "target_revision": target.target_revision,
                    }
                    for target in payload.observed_targets
                ],
                "completed_at": payload.completed_at.isoformat(),
            },
        ),
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.visual_command_dispatches
            set outcome = %s, outcome_reason = %s, observed_targets = %s,
                completed_at = %s
            where command_id = %s and outcome is null
            """,
            (
                payload.outcome,
                payload.reason,
                Jsonb(
                    [
                        {
                            "target_id": target.target_id,
                            "target_revision": target.target_revision,
                        }
                        for target in payload.observed_targets
                    ]
                ),
                payload.completed_at,
                payload.command_id,
            ),
        )
    _log.info(
        "visual_command_result_recorded",
        client_id=str(client_id),
        command_id=str(payload.command_id),
        outcome=payload.outcome,
        reason=payload.reason,
    )
    return result


async def settle_unknown_outcomes(
    database: Database, *, extension_installation_id: UUID
) -> tuple[UUID, ...]:
    """Settle the commands an interrupted connection routed without a result.

    Every command routed to this installation that has no terminal outcome was
    either lost with the connection or reached the page, which may have applied
    it. Its outcome is therefore unknown rather than canceled, and nothing is
    retried automatically. The pending commands a reconnecting extension names
    are among these; so is a command whose frame or result the drop lost.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select dispatches.command_id, dispatches.client_id
            from public.visual_command_dispatches as dispatches
            join public.extension_bindings as bindings
              on bindings.id = dispatches.extension_binding_id
            where bindings.extension_installation_id = %s
              and dispatches.routed_at is not null
              and dispatches.outcome is null
            order by dispatches.created_at
            """,
            (extension_installation_id,),
        )
        unsettled = [
            (as_uuid(row, "command_id"), as_uuid(row, "client_id"))
            for row in await cur.fetchall()
        ]
    settled: list[UUID] = []
    for command_id, client_id in unsettled:
        await record_terminal_result(
            database,
            message_id=uuid7(),
            client_id=client_id,
            source="browser_extension",
            result=TerminalResult(
                result_id=uuid7(),
                command_id=command_id,
                disposition=CommandDisposition.FAILED,
                effect_state=EffectState.INDETERMINATE,
                technical_category=OUTCOME_UNKNOWN,
                retry_advice=None,
                payload={"outcome": "failed", "reason": OUTCOME_UNKNOWN},
            ),
        )
        async with database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                update public.visual_command_dispatches
                set outcome = 'failed', outcome_reason = %s, completed_at = now()
                where command_id = %s and outcome is null
                """,
                (OUTCOME_UNKNOWN, command_id),
            )
        settled.append(command_id)
    if settled:
        _log.info(
            "visual_commands_settled_unknown",
            extension_installation_id=str(extension_installation_id),
            settled=len(settled),
        )
    return tuple(settled)


async def cancel_pending_visual_commands(
    database: Database,
    *,
    client_id: UUID,
    interview_session_id: UUID,
    reason: str,
) -> tuple[UUID, ...]:
    """Cancel every command of one session with no terminal result yet.

    Pause, rebind, replacement, and session closure all invalidate incompatible
    pending commands. The cancellation is durable even when no socket is
    connected, so a reconnecting extension cannot later apply a withdrawn
    command; the socket that routed one tells the extension from this record.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select command_id from public.visual_command_dispatches
            where interview_session_id = %s and outcome is null
            order by created_at
            """,
            (interview_session_id,),
        )
        command_ids = tuple(as_uuid(row, "command_id") for row in await cur.fetchall())
    canceled = await _cancel(database, client_id=client_id, command_ids=command_ids, reason=reason)
    if canceled:
        _log.info(
            "visual_commands_canceled",
            client_id=str(client_id),
            interview_session_id=str(interview_session_id),
            reason=reason,
            canceled=len(canceled),
        )
    return canceled


async def cancel_pending_case_visual_commands(
    database: Database, *, client_id: UUID, product_signal_id: UUID, reason: str
) -> tuple[UUID, ...]:
    """Cancel every pending command of every Interview Session in one case."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select dispatches.command_id
            from public.visual_command_dispatches as dispatches
            join public.interview_sessions as sessions
              on sessions.id = dispatches.interview_session_id
            where sessions.product_signal_id = %s and dispatches.outcome is null
            order by dispatches.created_at
            """,
            (product_signal_id,),
        )
        command_ids = tuple(as_uuid(row, "command_id") for row in await cur.fetchall())
    canceled = await _cancel(database, client_id=client_id, command_ids=command_ids, reason=reason)
    if canceled:
        _log.info(
            "visual_commands_canceled",
            client_id=str(client_id),
            product_signal_id=str(product_signal_id),
            reason=reason,
            canceled=len(canceled),
        )
    return canceled


async def _cancel(
    database: Database, *, client_id: UUID, command_ids: Sequence[UUID], reason: str
) -> tuple[UUID, ...]:
    """Cancel each command that still has no outcome, and record its result.

    The outcome is claimed first, so a socket can no longer route the command.
    A command already routed may have reached the page, so its effect is
    indeterminate; one never routed never left the control plane.
    """
    canceled: list[UUID] = []
    for command_id in command_ids:
        async with database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                update public.visual_command_dispatches
                set outcome = 'canceled', outcome_reason = %s, completed_at = now()
                where command_id = %s and outcome is null
                returning routed_at
                """,
                (reason, command_id),
            )
            claimed = await cur.fetchone()
        if claimed is None:
            continue
        await record_terminal_result(
            database,
            message_id=uuid7(),
            client_id=client_id,
            source="control_plane",
            result=TerminalResult(
                result_id=uuid7(),
                command_id=command_id,
                disposition=CommandDisposition.CANCELED,
                effect_state=(
                    EffectState.ABSENT
                    if claimed["routed_at"] is None
                    else EffectState.INDETERMINATE
                ),
                technical_category=reason,
                retry_advice=None,
                payload={"outcome": "canceled", "reason": reason},
            ),
        )
        canceled.append(command_id)
    return tuple(canceled)


@dataclass(frozen=True, slots=True)
class _AuthorizationContext:
    """Everything the deny-wins checks read, in one snapshot."""

    refusal: str | None
    session_revision: int
    readiness_revision: int
    binding_revision: int
    preferences_revision: int
    extension_binding_id: UUID
    document_attachment_id: UUID
    permitted_classes: frozenset[str]
    agent_cursor_enabled: bool


async def _authorization_context(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID
) -> _AuthorizationContext:
    """Read session, readiness, binding, preferences, and policy in one pass."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select sessions.revision, sessions.state, sessions.paused,
                   releases.policy,
                   coalesce(readiness.observation_ready, false) as observation_ready,
                   coalesce(readiness.readiness_revision, 0) as readiness_revision,
                   bindings.id as binding_id, bindings.binding_revision,
                   bindings.document_attachment_id,
                   coalesce(preferences.preferences_revision, 0) as preferences_revision,
                   coalesce(preferences.visual_guidance_enabled, true)
                     as visual_guidance_enabled,
                   coalesce(preferences.agent_cursor_enabled, true)
                     as agent_cursor_enabled
            from public.interview_sessions as sessions
            join public.product_signals as signals
              on signals.id = sessions.product_signal_id
            join public.configuration_releases as releases
              on releases.id = signals.pinned_configuration_release_id
            left join public.session_readiness as readiness
              on readiness.interview_session_id = sessions.id
            left join public.extension_bindings as bindings
              on bindings.interview_session_id = sessions.id and bindings.status = 'bound'
            left join public.extension_installations as installations
              on installations.id = bindings.extension_installation_id
            left join public.extension_preferences as preferences
              on preferences.utrace_user_id = installations.utrace_user_id
            where sessions.id = %s
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="interview_sessions", row_id=interview_session_id
        )
    if as_str(row, "state") != InterviewSessionState.LIVE.value:
        return _refusal("session_not_live")
    if as_bool(row, "paused"):
        return _refusal("session_paused")
    if not as_bool(row, "observation_ready"):
        return _refusal("observation_readiness_incomplete")
    if row["binding_id"] is None:
        return _refusal("no_current_binding")
    if not as_bool(row, "visual_guidance_enabled"):
        return _refusal("visual_guidance_disabled")
    policy = row["policy"]
    if not isinstance(policy, Mapping):
        raise TypeError("configuration_releases.policy is not an object")
    return _AuthorizationContext(
        refusal=None,
        session_revision=as_int(row, "revision"),
        readiness_revision=as_int(row, "readiness_revision"),
        binding_revision=as_int(row, "binding_revision"),
        preferences_revision=as_int(row, "preferences_revision"),
        extension_binding_id=as_uuid(row, "binding_id"),
        document_attachment_id=as_uuid(row, "document_attachment_id"),
        permitted_classes=_permitted_classes(policy),
        agent_cursor_enabled=as_bool(row, "agent_cursor_enabled"),
    )


_NO_IDENTITY = UUID(int=0)


def _refusal(reason: str) -> _AuthorizationContext:
    """Build a refusing context carrying only its reason code.

    Its identity fields are the nil UUID because a refusal dispatches nothing;
    the caller reads ``refusal`` first and never reaches them.
    """
    return _AuthorizationContext(
        refusal=reason,
        session_revision=0,
        readiness_revision=0,
        binding_revision=0,
        preferences_revision=0,
        extension_binding_id=_NO_IDENTITY,
        document_attachment_id=_NO_IDENTITY,
        permitted_classes=frozenset(),
        agent_cursor_enabled=False,
    )


def _permitted_classes(policy: Mapping[str, object]) -> frozenset[str]:
    """Return the command classes the pinned configuration release permits.

    A release that names classes is authoritative and can only narrow the pilot
    set; a release that names none leaves the pilot default in place.
    """
    configured = policy.get(_POLICY_KEY)
    if configured is None:
        return _PILOT_DEFAULT_CLASSES
    if not isinstance(configured, list):
        raise TypeError(f"configuration release policy {_POLICY_KEY!r} is not an array")
    return frozenset(str(item) for item in configured) & _PILOT_DEFAULT_CLASSES


def _targets(command: VisualCommand) -> tuple[tuple[str, int], ...]:
    """Return every registered target the command addresses, in order."""
    if command.type == "scroll_into_view":
        return ((command.target_id, command.target_revision),)
    if command.type == "clear_scene":
        return ()
    return tuple(
        (step.target_id, step.target_revision)
        for step in command.scene.steps
        if step.step != "clear"
    )
