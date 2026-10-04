"""What the control socket delivers when durable state changes elsewhere.

The extension's socket lives in one API process, but its binding, readiness,
character state, and visual commands change in any control-plane process. Those
processes only commit durable state: triggers on the owning tables notify
`CONTROL_CHANNEL` with the Interview Session that changed, and the socket that
serves that session re-projects its current state from the database and routes
the durable pending commands. A notification carries no state and grants no
authority, and a missed one is repaired by the next re-projection or reconnect.

Delivery stays inside the control stream's own rules: every frame the socket
sends is sequenced, persisted, and acknowledged through `ControlStream`, and
resumption still truncates rather than replays.

The reads here are that re-projection's durable inputs: the session's newest
binding, the commands waiting to be routed to one installation, and the outcomes
of commands already routed.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import TypeAdapter
from utrace_contracts.domain.lifecycle import CommandState

from app.db.pool import Database
from app.db.rows import as_datetime, as_int, as_optional_str, as_str, as_uuid
from app.envelope import JsonValue
from app.extension.bindings import BoundDocument
from app.extension.commands import VisualCommand

__all__ = [
    "CONTROL_CHANNEL",
    "RoutableCommand",
    "RoutedOutcome",
    "claim_routable_commands",
    "latest_binding",
    "routed_outcomes",
]

CONTROL_CHANNEL = "extension_control"
"""The channel the `utrace.notify_extension_control` triggers notify."""

_VISUAL_COMMAND = TypeAdapter[VisualCommand](VisualCommand)


@dataclass(frozen=True, slots=True)
class RoutableCommand:
    """One authorized command this socket has claimed and now routes."""

    command_id: UUID
    client_id: UUID
    interview_session_id: UUID
    extension_binding_id: UUID
    document_attachment_id: UUID
    expected_lifecycle_revision: int
    deadline_at: datetime
    command: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class RoutedOutcome:
    """The terminal outcome of one routed command, once it has one."""

    outcome: str
    reason: str | None


async def latest_binding(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID
) -> BoundDocument | None:
    """Return the session's newest binding, bound or revoked, if it has one.

    Binding revisions are monotonic within a session, so the newest one is the
    state the extension must hold: a revocation supersedes the binding it ends,
    and a replacement supersedes the revocation of the binding it replaced.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, client_id, status, binding_revision, document_attachment_id,
                   preview_runtime_allocation_id, reason
            from public.extension_bindings
            where interview_session_id = %s and status in ('bound', 'revoked')
            order by binding_revision desc
            limit 1
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return BoundDocument(
        status=as_str(row, "status"),
        binding_revision=as_int(row, "binding_revision"),
        interview_session_id=interview_session_id,
        client_id=as_uuid(row, "client_id"),
        extension_binding_id=as_uuid(row, "id"),
        document_attachment_id=as_uuid(row, "document_attachment_id"),
        preview_runtime_allocation_id=as_uuid(row, "preview_runtime_allocation_id"),
        reason=as_optional_str(row, "reason"),
    )


async def claim_routable_commands(
    database: Database, *, extension_installation_id: UUID
) -> tuple[RoutableCommand, ...]:
    """Claim every unrouted pending command on this installation's current binding.

    The claim is single use, so a second connection for the same installation
    can never route a command twice. It moves the Command from `queued` to
    `dispatched` in the same transaction, before the frame is written, so a
    cancellation that lands afterwards finds a dispatched Command whose effect
    is indeterminate. A command whose binding was revoked or replaced before it
    was routed is never routed: the withdrawal that revoked the binding cancels
    it.
    """
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            with claimed as (
              update public.visual_command_dispatches as dispatches
              set routed_at = now()
              from public.extension_bindings as bindings
              where bindings.id = dispatches.extension_binding_id
                and bindings.extension_installation_id = %(installation)s
                and bindings.status = 'bound'
                and dispatches.routed_at is null
                and dispatches.outcome is null
              returning dispatches.command_id, dispatches.client_id,
                        dispatches.interview_session_id, dispatches.extension_binding_id,
                        dispatches.document_attachment_id,
                        dispatches.expected_lifecycle_revision, dispatches.deadline_at,
                        dispatches.created_at
            )
            update public.commands
            set state = %(dispatched)s, dispatching_at = now(), dispatched_at = now(),
                provider_ref = claimed.extension_binding_id::text,
                revision = commands.revision + 1, updated_at = now()
            from claimed
            where commands.id = claimed.command_id and commands.state = %(queued)s
            returning claimed.command_id, claimed.client_id, claimed.interview_session_id,
                      claimed.extension_binding_id, claimed.document_attachment_id,
                      claimed.expected_lifecycle_revision, claimed.deadline_at,
                      claimed.created_at, commands.payload
            """,
            {
                "installation": extension_installation_id,
                "dispatched": CommandState.DISPATCHED.value,
                "queued": CommandState.QUEUED.value,
            },
        )
        rows = sorted(await cur.fetchall(), key=lambda row: as_datetime(row, "created_at"))
    return tuple(
        RoutableCommand(
            command_id=as_uuid(row, "command_id"),
            client_id=as_uuid(row, "client_id"),
            interview_session_id=as_uuid(row, "interview_session_id"),
            extension_binding_id=as_uuid(row, "extension_binding_id"),
            document_attachment_id=as_uuid(row, "document_attachment_id"),
            expected_lifecycle_revision=as_int(row, "expected_lifecycle_revision"),
            deadline_at=as_datetime(row, "deadline_at"),
            command=_VISUAL_COMMAND.validate_python(row["payload"]).model_dump(mode="json"),
        )
        for row in rows
    )


async def routed_outcomes(
    database: Database, *, command_ids: Sequence[UUID]
) -> Mapping[UUID, RoutedOutcome]:
    """Return the terminal outcome of each routed command that has one."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select command_id, outcome, outcome_reason
            from public.visual_command_dispatches
            where command_id = any(%s) and outcome is not null
            """,
            (list(command_ids),),
        )
        rows = await cur.fetchall()
    return {
        as_uuid(row, "command_id"): RoutedOutcome(
            outcome=as_str(row, "outcome"), reason=as_optional_str(row, "outcome_reason")
        )
        for row in rows
    }
