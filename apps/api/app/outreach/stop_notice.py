"""Telling the originating user, privately, that the client stopped their case.

The same three phases as the initial invitation, because a provider send cannot
be undone and Discord offers no idempotency key for one:

1. One transaction locks the Product Signal and commits the case's one notice
   Command. An existing terminal Command means the notice already concluded; a
   non-terminal one means a send may have happened, so it is reconciled rather
   than sent again.
2. The direct message is sent once, outside any transaction.
3. One transaction records the durable Result and closes the Command.

A participant who asked not to be contacted, or whom uTrace never reached, is
not messaged; that decision is recorded as the Command's terminal Result, so it
is made once per case.

Contract: docs/PRD.md#user-facing-implementation-progress
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    AggregateKind,
    CommandDisposition,
    CommandState,
    EffectState,
)

from app.connectors.failures import ConnectorFailure
from app.connectors.ports import CustomerSurfacePort
from app.db.pool import Database
from app.db.rows import as_bool, as_str, as_uuid
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.ids import uuid7
from app.logging import get_logger
from app.outreach.delivery import record_send_result
from app.outreach.message import stop_notice_text
from app.outreach.reasons import DO_NOT_CONTACT
from app.outreach.recipient import client_name, load_recipient, product_name

__all__ = [
    "NEVER_REACHED",
    "STOP_NOTICE_COMMAND",
    "StopNoticeOutcome",
    "UncertainNoticeError",
    "send_stop_notice",
]

_log = get_logger(__name__)

STOP_NOTICE_COMMAND: Final[str] = "outreach.send_stop_notice"
NEVER_REACHED: Final[str] = "participant_never_reached"


class UncertainNoticeError(GuardRejectedError):
    """A notice Command exists with no recorded Result; sending again is refused."""

    def __init__(self, product_signal_id: UUID, command_id: UUID) -> None:
        super().__init__(
            machine="outreach_notice",
            trigger="send_stop_notice",
            reason=(
                f"Product Signal {product_signal_id} has notice command {command_id} "
                "with no recorded result; reconcile the direct-message channel "
                "before any resend"
            ),
        )
        self.product_signal_id = product_signal_id
        self.command_id = command_id


@dataclass(frozen=True, slots=True)
class StopNoticeOutcome:
    """What one run of the client-stop notice committed."""

    command_id: UUID
    sent: bool
    reason: str


@dataclass(frozen=True, slots=True)
class _Prepared:
    """The committed notice Command, carried into the provider send."""

    command_id: UUID
    recipient_provider_identity: str
    content: str


async def _insert_command(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    product_signal_id: UUID,
    recipient_provider_identity: str | None,
) -> UUID:
    """Commit the case's one notice Command; its id is the send's idempotency key."""
    command_id = uuid7()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.commands (
                id, client_id, command_type, state, target_kind, target_id,
                payload, declared_revisions, queued_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, now())
            """,
            (
                command_id,
                client_id,
                STOP_NOTICE_COMMAND,
                CommandState.QUEUED.value,
                AggregateKind.PRODUCT_SIGNAL.value,
                product_signal_id,
                Jsonb({"recipient_provider_identity": recipient_provider_identity}),
                Jsonb({}),
            ),
        )
    return command_id


async def _reached(conn: AsyncConnection[DictRow], *, product_signal_id: UUID) -> bool:
    """True once any private message for this case was delivered."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select exists (
                select 1 from public.outreach_deliveries as deliveries
                join public.outreach_attempts as attempts
                  on attempts.id = deliveries.outreach_attempt_id
                where attempts.product_signal_id = %s
                  and deliveries.effect_state = %s
            ) as reached
            """,
            (product_signal_id, EffectState.COMPLETED.value),
        )
        row = await cur.fetchone()
    return row is not None and as_bool(row, "reached")


async def _skip(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    product_signal_id: UUID,
    reason: str,
) -> StopNoticeOutcome:
    """Record the decision not to message the participant as the Command's Result."""
    command_id = await _insert_command(
        conn,
        client_id=client_id,
        product_signal_id=product_signal_id,
        recipient_provider_identity=None,
    )
    await record_send_result(
        conn,
        client_id=client_id,
        command_id=command_id,
        provider_message_ref=None,
        effect_state=EffectState.ABSENT,
        disposition=CommandDisposition.CANCELED,
        failure_category=None,
        detail={"skipped": reason},
    )
    return StopNoticeOutcome(command_id=command_id, sent=False, reason=reason)


async def _prepare(
    database: Database, *, client_id: UUID, product_signal_id: UUID
) -> _Prepared | StopNoticeOutcome:
    """Commit the notice Command, or conclude without a send."""
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select id from public.product_signals
                where id = %s and client_id = %s for update
                """,
                (product_signal_id, client_id),
            )
            if await cur.fetchone() is None:
                raise AggregateNotFoundError(
                    table="product_signals", row_id=product_signal_id
                )
            await cur.execute(
                """
                select id, state from public.commands
                where command_type = %s and target_kind = %s and target_id = %s
                order by created_at desc limit 1
                """,
                (
                    STOP_NOTICE_COMMAND,
                    AggregateKind.PRODUCT_SIGNAL.value,
                    product_signal_id,
                ),
            )
            existing = await cur.fetchone()
        if existing is not None:
            existing_id = as_uuid(existing, "id")
            if as_str(existing, "state") != CommandState.TERMINAL.value:
                raise UncertainNoticeError(product_signal_id, existing_id)
            return StopNoticeOutcome(
                command_id=existing_id, sent=False, reason="already_concluded"
            )

        if not await _reached(conn, product_signal_id=product_signal_id):
            return await _skip(
                conn,
                client_id=client_id,
                product_signal_id=product_signal_id,
                reason=NEVER_REACHED,
            )
        recipient = await load_recipient(conn, product_signal_id=product_signal_id)
        if recipient.do_not_contact:
            return await _skip(
                conn,
                client_id=client_id,
                product_signal_id=product_signal_id,
                reason=DO_NOT_CONTACT,
            )
        content = stop_notice_text(
            client_name=await client_name(conn, client_id=client_id),
            product_name=await product_name(conn, client_id=client_id),
        )
        command_id = await _insert_command(
            conn,
            client_id=client_id,
            product_signal_id=product_signal_id,
            recipient_provider_identity=recipient.provider_user_id,
        )
    return _Prepared(
        command_id=command_id,
        recipient_provider_identity=recipient.provider_user_id,
        content=content,
    )


async def send_stop_notice(
    database: Database,
    surface: CustomerSurfacePort,
    *,
    client_id: UUID,
    product_signal_id: UUID,
) -> StopNoticeOutcome:
    """Send the originating user the client-stop notice at most once."""
    prepared = await _prepare(
        database, client_id=client_id, product_signal_id=product_signal_id
    )
    if isinstance(prepared, StopNoticeOutcome):
        _log.info(
            "stop_notice_not_sent",
            client_id=str(client_id),
            product_signal_id=str(product_signal_id),
            command_id=str(prepared.command_id),
            reason=prepared.reason,
        )
        return prepared

    failure: ConnectorFailure | None = None
    provider_ref: str | None = None
    try:
        receipt = await surface.send_direct_message(
            user_id=prepared.recipient_provider_identity, content=prepared.content
        )
        provider_ref = receipt.message_id
    except ConnectorFailure as error:
        failure = error

    async with database.transaction() as conn:
        await record_send_result(
            conn,
            client_id=client_id,
            command_id=prepared.command_id,
            provider_message_ref=provider_ref,
            effect_state=(
                EffectState.COMPLETED if failure is None else failure.detail.effect_state
            ),
            disposition=(
                CommandDisposition.SUCCEEDED
                if failure is None
                else CommandDisposition.FAILED
            ),
            failure_category=None if failure is None else failure.category.value,
            detail=(
                {"provider_message_ref": provider_ref}
                if failure is None
                else {"message": failure.detail.message}
            ),
        )
    if failure is not None:
        _log.warning(
            "stop_notice_delivery_failed",
            client_id=str(client_id),
            product_signal_id=str(product_signal_id),
            command_id=str(prepared.command_id),
            failure_category=failure.category.value,
            detail=failure.detail.message,
        )
        return StopNoticeOutcome(
            command_id=prepared.command_id, sent=False, reason="delivery_failed"
        )
    _log.info(
        "stop_notice_sent",
        client_id=str(client_id),
        product_signal_id=str(product_signal_id),
        command_id=str(prepared.command_id),
        provider_message_ref=provider_ref,
    )
    return StopNoticeOutcome(command_id=prepared.command_id, sent=True, reason="delivered")
