"""Transactional outbox and its pgmq wake-ups.

The outbox is the only path from committed workflow state to external effects.
pgmq carries wake-ups so a worker does not have to poll hard; authority stays in
Postgres, and pg_cron rescans so a lost wake-up cannot strand work.
"""

from dataclasses import dataclass
from uuid import UUID

from app.db.pool import Database
from app.db.rows import as_int
from app.logging import get_logger

__all__ = ["Wakeup", "acknowledge_wakeup", "read_wakeups", "scan_ready"]

_log = get_logger(__name__)

_QUEUE = "utrace_outbox"


@dataclass(frozen=True, slots=True)
class Wakeup:
    """One queue message pointing at an outbox row. It proves nothing by itself."""

    message_id: int
    outbox_id: UUID
    client_id: UUID
    job_id: UUID | None
    command_id: UUID | None


async def scan_ready(database: Database, *, limit: int) -> int:
    """Move due outbox rows onto the wake-up queue and report how many moved."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute("select utrace.enqueue_ready_outbox(%s) as moved", (limit,))
        row = await cur.fetchone()
    return as_int(row, "moved") if row is not None else 0


async def read_wakeups(
    database: Database, *, batch_size: int, visibility_seconds: int
) -> tuple[Wakeup, ...]:
    """Read wake-ups with a visibility timeout so a crashed worker releases them."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select msg_id, message from pgmq.read(%s, %s, %s)",
            (_QUEUE, visibility_seconds, batch_size),
        )
        rows = await cur.fetchall()
    wakeups: list[Wakeup] = []
    for row in rows:
        body = row["message"]
        if not isinstance(body, dict):
            raise TypeError(
                f"pgmq message {row['msg_id']} on {_QUEUE} is not a JSON object"
            )
        wakeups.append(
            Wakeup(
                message_id=as_int(row, "msg_id"),
                outbox_id=UUID(str(body["outbox_id"])),
                client_id=UUID(str(body["client_id"])),
                job_id=_maybe_uuid(body["job_id"]),
                command_id=_maybe_uuid(body["command_id"]),
            )
        )
    return tuple(wakeups)


def _maybe_uuid(value: object) -> UUID | None:
    """Parse an optional identifier carried in a queue message."""
    if value is None:
        return None
    return UUID(str(value))


async def acknowledge_wakeup(database: Database, *, message_id: int) -> None:
    """Delete a consumed wake-up. The outbox row remains the durable record."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute("select pgmq.delete(%s, %s::bigint)", (_QUEUE, message_id))
