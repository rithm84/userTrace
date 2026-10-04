"""Watched-channel message ingestion, deduplication, and debouncing.

The authenticated provider message id is the idempotency key: a duplicate
gateway delivery collides on the unique index and changes nothing. Messages from
one author in one watched channel collapse into one open batch, which seals into
an ordered immutable Intake Item once the author stops typing or the batch
reaches its maximum wait.

A bot-authored message is never a customer report. It is accepted only when an
explicit client-scoped attribution names the reporting client user, which is how
a test database reuses a bot-posted fixture message as a reporter's message.

Contract: docs/arch-components/signal-and-outreach.md#discord-ingestion-and-identity
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import ActorKind, AggregateKind, IntakeItemState

from app.clients.configuration import ActiveRelease, load_active_release_on_connection
from app.clients.provider_map import (
    PROVIDER_DISCORD,
    ResolvedAuthor,
    resolve_author_on_connection,
)
from app.connectors.ports import MessageReceipt, TypingReceipt
from app.db.kernel import append_fact
from app.db.pool import Database
from app.db.rows import as_datetime, as_int, as_str, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, JsonValue, StreamRef
from app.errors import AggregateNotFoundError
from app.ids import uuid7
from app.intake.errors import UnattributedAuthorError, UnwatchedSurfaceError
from app.intake.jobs import route_intake_item_job, seal_intake_batch_job
from app.logging import get_logger

__all__ = [
    "DebounceWindow",
    "RecordedMessage",
    "SealedBatch",
    "extend_for_typing",
    "record_message",
    "seal_batch",
]

_log = get_logger(__name__)

_BATCH_OPEN = "open"
_BATCH_SEALED = "sealed"


@dataclass(frozen=True, slots=True)
class DebounceWindow:
    """How long one author's messages collapse before the batch seals."""

    quiet_seconds: int
    maximum_seconds: int


@dataclass(frozen=True, slots=True)
class RecordedMessage:
    """What one observed message did to the durable record."""

    intake_batch_id: UUID
    ordinal: int
    duplicate: bool
    client_user_id: UUID
    customer_account_id: UUID


@dataclass(frozen=True, slots=True)
class SealedBatch:
    """The Intake Item one sealed batch produced."""

    intake_item_id: UUID
    intake_batch_id: UUID
    message_count: int


async def _attributed_author(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    provider: str,
    author_provider_identity: str,
) -> tuple[UUID, UUID] | None:
    """Return the attributed reporter for a bot-authored provider identity."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select client_user_id, customer_account_id
            from public.intake_author_attributions
            where client_id = %s and provider = %s and author_provider_identity = %s
            """,
            (client_id, provider, author_provider_identity),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return as_uuid(row, "client_user_id"), as_uuid(row, "customer_account_id")


async def _resolve_reporter(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    release: ActiveRelease,
    receipt: MessageReceipt,
) -> tuple[UUID, UUID]:
    """Resolve the reporting client user and customer account for one message."""
    identity = receipt.author_id
    if receipt.author_is_bot:
        attributed = await _attributed_author(
            conn,
            client_id=client_id,
            provider=receipt.provider,
            author_provider_identity=identity,
        )
        if attributed is None:
            raise UnattributedAuthorError(
                client_id=client_id, author_provider_identity=identity
            )
        return attributed
    author: ResolvedAuthor = await resolve_author_on_connection(
        conn,
        client_id=client_id,
        provider=receipt.provider,
        installation_ref=release.policy.customer_surface.installation_ref,
        provider_user_id=identity,
    )
    return author.client_user_id, author.customer_account_id


async def _open_or_extend_batch(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    receipt: MessageReceipt,
    window: DebounceWindow,
    now: datetime,
) -> tuple[UUID, int, bool]:
    """Return the open batch for this author and channel, opening one if needed.

    The third element says whether the batch was opened by this call, which is
    what decides whether a seal Job has to be created.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, message_count, seal_deadline_at
            from public.intake_batches
            where client_id = %s and provider = %s and channel_ref = %s
              and author_provider_identity = %s and state = %s
            for update
            """,
            (
                client_id,
                receipt.provider,
                receipt.channel_id,
                receipt.author_id,
                _BATCH_OPEN,
            ),
        )
        existing = await cur.fetchone()
        if existing is not None:
            batch_id = as_uuid(existing, "id")
            ordinal = as_int(existing, "message_count") + 1
            quiet_until = now + timedelta(seconds=window.quiet_seconds)
            deadline = as_datetime(existing, "seal_deadline_at")
            await cur.execute(
                """
                update public.intake_batches
                set message_count = %s,
                    seal_not_before = least(%s, seal_deadline_at),
                    revision = revision + 1,
                    updated_at = now()
                where id = %s
                """,
                (ordinal, quiet_until, batch_id),
            )
            await cur.execute(
                """
                update public.jobs
                set not_before = least(%s, %s), revision = revision + 1, updated_at = now()
                where id = (select seal_job_id from public.intake_batches where id = %s)
                  and state = 'ready'
                """,
                (quiet_until, deadline, batch_id),
            )
            return batch_id, ordinal, False

        batch_id = uuid7()
        await cur.execute(
            """
            insert into public.intake_batches (
                id, client_id, provider, channel_ref, author_provider_identity,
                state, message_count, seal_not_before, seal_deadline_at
            )
            values (%s, %s, %s, %s, %s, %s, 1, %s, %s)
            """,
            (
                batch_id,
                client_id,
                receipt.provider,
                receipt.channel_id,
                receipt.author_id,
                _BATCH_OPEN,
                now + timedelta(seconds=window.quiet_seconds),
                now + timedelta(seconds=window.maximum_seconds),
            ),
        )
    return batch_id, 1, True


async def extend_for_typing(database: Database, *, client_id: UUID, receipt: TypingReceipt) -> bool:
    """Keep the author's open batch from sealing while they type the next message.

    The quiet window only has to cover the gap before someone starts typing again;
    typing then holds the batch open until the indicator lapses, never past the
    batch's hard deadline. Returns whether an open batch was extended: typing before
    a first message has no batch to hold.
    """
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.intake_batches
            set seal_not_before = least(greatest(seal_not_before, %s), seal_deadline_at),
                revision = revision + 1,
                updated_at = now()
            where client_id = %s and provider = %s and channel_ref = %s
              and author_provider_identity = %s and state = %s
              and seal_not_before < least(%s, seal_deadline_at)
            returning seal_job_id, seal_not_before
            """,
            (
                receipt.active_until,
                client_id,
                receipt.provider,
                receipt.channel_id,
                receipt.author_id,
                _BATCH_OPEN,
                receipt.active_until,
            ),
        )
        extended = await cur.fetchone()
        if extended is None:
            return False
        await cur.execute(
            """
            update public.jobs
            set not_before = %s, revision = revision + 1, updated_at = now()
            where id = %s and state = 'ready'
            """,
            (extended["seal_not_before"], extended["seal_job_id"]),
        )
    return True


async def record_message(
    database: Database,
    *,
    client_id: UUID,
    release: ActiveRelease,
    receipt: MessageReceipt,
    window: DebounceWindow,
) -> RecordedMessage:
    """Record one watched-channel message and schedule its batch to seal.

    Refuses an unwatched channel and an unattributed bot author before anything
    durable is written. A duplicate provider message id is a no-op that returns
    the batch the original message already joined.
    """
    if not release.policy.customer_surface.watches(receipt.channel_id):
        raise UnwatchedSurfaceError(client_id=client_id, channel_ref=receipt.channel_id)

    now = datetime.now(tz=UTC)
    async with database.transaction() as conn:
        client_user_id, customer_account_id = await _resolve_reporter(
            conn, client_id=client_id, release=release, receipt=receipt
        )
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select intake_batch_id, ordinal from public.intake_messages
                where client_id = %s and provider = %s and provider_message_id = %s
                """,
                (client_id, receipt.provider, receipt.message_id),
            )
            duplicate = await cur.fetchone()
        if duplicate is not None:
            _log.info(
                "intake_message_duplicate",
                client_id=str(client_id),
                provider_message_id=receipt.message_id,
            )
            return RecordedMessage(
                intake_batch_id=as_uuid(duplicate, "intake_batch_id"),
                ordinal=as_int(duplicate, "ordinal"),
                duplicate=True,
                client_user_id=client_user_id,
                customer_account_id=customer_account_id,
            )

        batch_id, ordinal, opened = await _open_or_extend_batch(
            conn, client_id=client_id, receipt=receipt, window=window, now=now
        )
        message_id = uuid7()
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.intake_messages (
                    id, client_id, intake_batch_id, provider, channel_ref,
                    provider_message_id, author_provider_identity, author_is_bot,
                    ordinal, content, reply_to_provider_message_id, thread_ref,
                    occurred_at, edited_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    message_id,
                    client_id,
                    batch_id,
                    receipt.provider,
                    receipt.channel_id,
                    receipt.message_id,
                    receipt.author_id,
                    receipt.author_is_bot,
                    ordinal,
                    receipt.content,
                    receipt.reply_to_message_id,
                    receipt.thread_id,
                    receipt.created_at,
                    receipt.edited_at,
                ),
            )

        if opened:
            job = seal_intake_batch_job(
                client_id=client_id,
                intake_batch_id=batch_id,
                not_before=now + timedelta(seconds=window.quiet_seconds),
            )
            await append_fact(
                conn,
                client_id=client_id,
                event=_batch_opened_event(
                    client_id=client_id, batch_id=batch_id, receipt=receipt, now=now
                ),
                audit=_batch_audit(
                    batch_id=batch_id, action="open_intake_batch", outcome=_BATCH_OPEN
                ),
                jobs=(job,),
            )
            async with conn.cursor() as cur:
                await cur.execute(
                    "update public.intake_batches set seal_job_id = %s where id = %s",
                    (job.job_id, batch_id),
                )

    _log.info(
        "intake_message_recorded",
        client_id=str(client_id),
        intake_batch_id=str(batch_id),
        ordinal=ordinal,
        opened_batch=opened,
    )
    return RecordedMessage(
        intake_batch_id=batch_id,
        ordinal=ordinal,
        duplicate=False,
        client_user_id=client_user_id,
        customer_account_id=customer_account_id,
    )


def _batch_opened_event(
    *, client_id: UUID, batch_id: UUID, receipt: MessageReceipt, now: datetime
) -> EventAppend:
    """Build the fact that one debounce batch opened."""
    return EventAppend(
        message_id=uuid7(),
        stream=StreamRef(kind=AggregateKind.INTAKE_ITEM, id=batch_id),
        event_type="intake_batch_opened",
        payload={
            "client_id": str(client_id),
            "provider": receipt.provider,
            "channel_ref": receipt.channel_id,
            "author_provider_identity": receipt.author_id,
        },
        actor=Actor(kind=ActorKind.PROVIDER_IDENTITY, ref=receipt.author_id),
        causation_id=None,
        correlation_id=batch_id,
        occurred_at=now,
    )


def _batch_audit(*, batch_id: UUID, action: str, outcome: str) -> AuditIntent:
    """Build the audit record for one batch lifecycle step."""
    return AuditIntent(
        audit_id=uuid7(),
        subject=StreamRef(kind=AggregateKind.INTAKE_ITEM, id=batch_id),
        action=action,
        outcome=outcome,
        reason_code=None,
        actor=Actor(kind=ActorKind.SERVICE, ref="intake.ingestion"),
        pinned_configuration_release_id=None,
        detail={},
    )


def _batch_digest(*, batch_id: UUID, provider_message_ids: tuple[str, ...]) -> str:
    """Return the stable digest of one sealed batch's ordered message identities."""
    payload = f"{batch_id}\n" + "\n".join(provider_message_ids)
    return f"sha256:{sha256(payload.encode('utf-8')).hexdigest()}"


async def seal_batch(database: Database, *, intake_batch_id: UUID) -> SealedBatch:
    """Seal one open batch into an ordered immutable Intake Item.

    Sealing is idempotent: a batch already sealed returns the Intake Item it
    produced instead of creating a second one.
    """
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select id, client_id, provider, channel_ref, author_provider_identity,
                       state, intake_item_id, message_count
                from public.intake_batches where id = %s for update
                """,
                (intake_batch_id,),
            )
            batch = await cur.fetchone()
        if batch is None:
            raise AggregateNotFoundError(
                table="intake_batches", row_id=intake_batch_id
            )
        client_id = as_uuid(batch, "client_id")
        if as_str(batch, "state") == _BATCH_SEALED:
            existing_item = batch["intake_item_id"]
            if existing_item is None:
                raise AggregateNotFoundError(
                    table="intake_items", row_id=intake_batch_id
                )
            return SealedBatch(
                intake_item_id=as_uuid(batch, "intake_item_id"),
                intake_batch_id=intake_batch_id,
                message_count=as_int(batch, "message_count"),
            )

        async with conn.cursor() as cur:
            await cur.execute(
                """
                select provider_message_id, content, reply_to_provider_message_id,
                       thread_ref, occurred_at, edited_at, ordinal
                from public.intake_messages
                where intake_batch_id = %s order by ordinal
                """,
                (intake_batch_id,),
            )
            rows = await cur.fetchall()
        if not rows:
            raise AggregateNotFoundError(
                table="intake_messages", row_id=intake_batch_id
            )

        messages: list[JsonValue] = [
            {
                "ordinal": as_int(row, "ordinal"),
                "provider_message_id": as_str(row, "provider_message_id"),
                "content": as_str(row, "content"),
                "reply_to_provider_message_id": row["reply_to_provider_message_id"],
                "thread_ref": row["thread_ref"],
                "occurred_at": as_datetime(row, "occurred_at").isoformat(),
                "edited_at": (
                    as_datetime(row, "edited_at").isoformat()
                    if row["edited_at"] is not None
                    else None
                ),
            }
            for row in rows
        ]
        provider_message_ids = tuple(as_str(row, "provider_message_id") for row in rows)
        reporter = await _attributed_or_resolved_reporter(
            conn, client_id=client_id, batch=batch
        )
        item_id = uuid7()
        now = datetime.now(tz=UTC)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.intake_items (
                    id, client_id, state, source_provider, source_channel_ref,
                    source_batch_digest, author_provider_identity, client_user_id,
                    customer_account_id, payload
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    item_id,
                    client_id,
                    IntakeItemState.RECEIVED.value,
                    as_str(batch, "provider"),
                    as_str(batch, "channel_ref"),
                    _batch_digest(
                        batch_id=intake_batch_id,
                        provider_message_ids=provider_message_ids,
                    ),
                    as_str(batch, "author_provider_identity"),
                    reporter[0],
                    reporter[1],
                    Jsonb({"messages": messages}),
                ),
            )
            await cur.execute(
                """
                update public.intake_batches
                set state = %s, intake_item_id = %s, sealed_at = now(),
                    revision = revision + 1, updated_at = now()
                where id = %s
                """,
                (_BATCH_SEALED, item_id, intake_batch_id),
            )
        await append_fact(
            conn,
            client_id=client_id,
            event=EventAppend(
                message_id=uuid7(),
                stream=StreamRef(kind=AggregateKind.INTAKE_ITEM, id=item_id),
                event_type="intake_item_sealed",
                payload={
                    "intake_batch_id": str(intake_batch_id),
                    "message_count": len(messages),
                    "channel_ref": as_str(batch, "channel_ref"),
                },
                actor=Actor(kind=ActorKind.SERVICE, ref="intake.ingestion"),
                causation_id=intake_batch_id,
                correlation_id=item_id,
                occurred_at=now,
            ),
            audit=_batch_audit(
                batch_id=intake_batch_id,
                action="seal_intake_batch",
                outcome=_BATCH_SEALED,
            ),
            jobs=(route_intake_item_job(client_id=client_id, intake_item_id=item_id),),
        )
    _log.info(
        "intake_item_sealed",
        client_id=str(client_id),
        intake_item_id=str(item_id),
        message_count=len(messages),
    )
    return SealedBatch(
        intake_item_id=item_id,
        intake_batch_id=intake_batch_id,
        message_count=len(messages),
    )


async def _attributed_or_resolved_reporter(
    conn: AsyncConnection[DictRow], *, client_id: UUID, batch: DictRow
) -> tuple[UUID, UUID]:
    """Re-resolve the batch author at seal time, so a rebinding takes effect."""
    identity = as_str(batch, "author_provider_identity")
    attributed = await _attributed_author(
        conn,
        client_id=client_id,
        provider=as_str(batch, "provider"),
        author_provider_identity=identity,
    )
    if attributed is not None:
        return attributed
    release = await load_active_release_on_connection(conn, client_id=client_id)
    author = await resolve_author_on_connection(
        conn,
        client_id=client_id,
        provider=PROVIDER_DISCORD,
        installation_ref=release.policy.customer_surface.installation_ref,
        provider_user_id=identity,
    )
    return author.client_user_id, author.customer_account_id
