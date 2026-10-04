"""Command dispatch and Result acceptance.

A Command moves through created, queued, dispatching, dispatched or acknowledged,
reconciling, and exactly one terminal Result. Its ``command_id`` is the provider
idempotency key. Delivery is at least once, so the inbox deduplicates by
message_id and admits only one terminal receipt per Command; a repeated Command
replays its canonical terminal Result instead of executing again.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    CommandDisposition,
    CommandState,
    EffectState,
    ResultKind,
    RetryAdvice,
)

from app.db.pool import Database
from app.db.rows import as_optional_str, as_str, as_uuid
from app.envelope import JsonValue
from app.errors import AggregateNotFoundError, IllegalTransitionError
from app.ids import uuid7
from app.logging import get_logger

__all__ = [
    "TerminalResult",
    "mark_dispatched",
    "mark_dispatching",
    "record_inbound",
    "record_terminal_result",
    "replay_terminal_result",
    "start_reconciliation",
]

_log = get_logger(__name__)

_MACHINE = "command"
_MACHINE_VERSION = 1


@dataclass(frozen=True, slots=True)
class TerminalResult:
    """The single canonical outcome of one Command."""

    result_id: UUID
    command_id: UUID
    disposition: CommandDisposition
    effect_state: EffectState
    technical_category: str | None
    retry_advice: RetryAdvice | None
    payload: Mapping[str, JsonValue]


async def _advance(
    conn: AsyncConnection[DictRow],
    command_id: UUID,
    legal_predecessors: frozenset[str],
    target: CommandState,
    timestamp_column: str,
    provider_ref: str | None,
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            "select state from public.commands where id = %s for update", (command_id,)
        )
        row = await cur.fetchone()
        if row is None:
            raise AggregateNotFoundError(table="commands", row_id=command_id)
        current = as_str(row, "state")
        if current not in legal_predecessors:
            raise IllegalTransitionError(
                machine=_MACHINE,
                machine_version=_MACHINE_VERSION,
                state=current,
                trigger=target.value,
            )
        await cur.execute(
            f"""
            update public.commands
            set state = %s, {timestamp_column} = now(),
                provider_ref = coalesce(%s, provider_ref),
                revision = revision + 1, updated_at = now()
            where id = %s
            """,  # timestamp_column is a fixed internal literal, never user input
            (target.value, provider_ref, command_id),
        )


async def mark_dispatching(database: Database, *, command_id: UUID) -> None:
    """Claim a queued Command for dispatch."""
    async with database.transaction() as conn:
        await _advance(
            conn,
            command_id,
            frozenset({CommandState.CREATED.value, CommandState.QUEUED.value}),
            CommandState.DISPATCHING,
            "dispatching_at",
            None,
        )


async def mark_dispatched(
    database: Database, *, command_id: UUID, provider_ref: str
) -> None:
    """Record that the adapter handed the effect to the provider."""
    async with database.transaction() as conn:
        await _advance(
            conn,
            command_id,
            frozenset({CommandState.DISPATCHING.value}),
            CommandState.DISPATCHED,
            "dispatched_at",
            provider_ref,
        )
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update public.outbox
                set state = 'dispatched', dispatched_at = now(), updated_at = now()
                where command_id = %s
                """,
                (command_id,),
            )


async def start_reconciliation(database: Database, *, command_id: UUID) -> None:
    """Move an uncertain dispatched effect into reconciliation instead of repeating it."""
    async with database.transaction() as conn:
        await _advance(
            conn,
            command_id,
            frozenset(
                {
                    CommandState.DISPATCHING.value,
                    CommandState.DISPATCHED.value,
                    CommandState.ACKNOWLEDGED.value,
                }
            ),
            CommandState.RECONCILING,
            "reconciling_at",
            None,
        )


async def record_inbound(
    database: Database,
    *,
    message_id: UUID,
    client_id: UUID,
    source: str,
    message_kind: str,
    command_id: UUID | None,
    payload: Mapping[str, JsonValue],
) -> bool:
    """Record one inbound receipt. Returns ``False`` when it was already seen.

    Deduplication is by message_id, and a partial unique index additionally
    permits only one ``terminal_result`` receipt per Command.
    """
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.inbox (
                message_id, client_id, source, message_kind, command_id, payload
            )
            values (%s, %s, %s, %s, %s, %s)
            on conflict do nothing
            returning message_id
            """,
            (message_id, client_id, source, message_kind, command_id, Jsonb(payload)),
        )
        recorded = await cur.fetchone() is not None
    if not recorded:
        _log.info(
            "inbound_message_deduplicated",
            message_id=str(message_id),
            source=source,
            message_kind=message_kind,
            command_id=str(command_id) if command_id is not None else None,
        )
    return recorded


async def replay_terminal_result(
    database: Database, *, command_id: UUID
) -> TerminalResult | None:
    """Return the canonical terminal Result for a Command, if one exists."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, command_id, disposition, effect_state, technical_category,
                   retry_advice, payload
            from public.results
            where command_id = %s and kind = %s
            """,
            (command_id, ResultKind.TERMINAL.value),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    advice = as_optional_str(row, "retry_advice")
    return TerminalResult(
        result_id=as_uuid(row, "id"),
        command_id=as_uuid(row, "command_id"),
        disposition=CommandDisposition(as_str(row, "disposition")),
        effect_state=EffectState(as_str(row, "effect_state")),
        technical_category=as_optional_str(row, "technical_category"),
        retry_advice=RetryAdvice(advice) if advice is not None else None,
        payload=dict(row["payload"]),
    )


async def record_terminal_result(
    database: Database,
    *,
    message_id: UUID,
    client_id: UUID,
    source: str,
    result: TerminalResult,
) -> TerminalResult:
    """Accept one terminal Result, or replay the canonical one on a repeat.

    The inbox admits the receipt first. A duplicate receipt never writes a second
    Result and never advances the Command twice.
    """
    fresh = await record_inbound(
        database,
        message_id=message_id,
        client_id=client_id,
        source=source,
        message_kind="terminal_result",
        command_id=result.command_id,
        payload=result.payload,
    )
    if not fresh:
        existing = await replay_terminal_result(database, command_id=result.command_id)
        if existing is None:
            raise AggregateNotFoundError(table="results", row_id=result.command_id)
        return existing

    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.results (
                    id, client_id, command_id, kind, disposition, effect_state,
                    technical_category, retry_advice, payload
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    result.result_id,
                    client_id,
                    result.command_id,
                    ResultKind.TERMINAL.value,
                    result.disposition.value,
                    result.effect_state.value,
                    result.technical_category,
                    result.retry_advice.value if result.retry_advice is not None else None,
                    Jsonb(result.payload),
                ),
            )
        await _advance(
            conn,
            result.command_id,
            frozenset(
                {
                    CommandState.QUEUED.value,
                    CommandState.DISPATCHING.value,
                    CommandState.DISPATCHED.value,
                    CommandState.ACKNOWLEDGED.value,
                    CommandState.RECONCILING.value,
                }
            ),
            CommandState.TERMINAL,
            "terminal_at",
            None,
        )
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update public.inbox set processed_at = now() where message_id = %s
                """,
                (message_id,),
            )
            await cur.execute(
                """
                insert into public.audit_records (
                    id, client_id, subject_kind, subject_id, action, outcome,
                    reason_code, actor_kind, actor_ref, detail
                )
                values (%s, %s, 'command', %s, 'record_terminal_result', %s, %s,
                        'provider_identity', %s, %s)
                """,
                (
                    uuid7(),
                    client_id,
                    result.command_id,
                    result.disposition.value,
                    result.technical_category,
                    source,
                    Jsonb(
                        {
                            "effect_state": result.effect_state.value,
                            "retry_advice": (
                                result.retry_advice.value
                                if result.retry_advice is not None
                                else None
                            ),
                        }
                    ),
                ),
            )
    _log.info(
        "command_terminal_result_accepted",
        client_id=str(client_id),
        command_id=str(result.command_id),
        disposition=result.disposition.value,
        effect_state=result.effect_state.value,
        source=source,
    )
    return result
