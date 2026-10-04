"""The resumable outbound control stream for one installation.

Sequence allocation lives in exactly one place. Every sequenced frame is
persisted with the sequence it was sent at, and the stream row carries both the
highest sequence allocated and the highest the extension acknowledged.

Resumption truncates rather than replays: the frames above the resume point are
superseded, and the socket re-projects current control-plane state. Assuming
missed delivery succeeded is what the contract forbids, and a partial unique
index on the live sequence keeps a truncated sequence reusable without ever
letting two live frames share one.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from app.db.pool import Database
from app.db.rows import as_int
from app.extension.frames import OutgoingFrame
from app.extension.protocol import ProtocolError
from app.ids import uuid7

__all__ = ["ControlStream", "StampedFrame"]


@dataclass(frozen=True, slots=True)
class StampedFrame:
    """One frame's envelope identity, after the stream recorded it."""

    message_id: UUID
    sent_at: datetime
    sequence: int | None


class ControlStream:
    """One installation's durable control stream."""

    def __init__(self, database: Database, *, installation_id: UUID) -> None:
        self._database = database
        self._installation_id = installation_id

    async def mark_connected(self) -> None:
        """Record that a connection is serving this installation."""
        async with self._database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.extension_control_streams (
                    extension_installation_id, connected_at
                )
                values (%s, now())
                on conflict (extension_installation_id) do update
                set connected_at = now(), disconnected_at = null, updated_at = now()
                """,
                (self._installation_id,),
            )

    async def mark_disconnected(self) -> None:
        """Record that no connection is serving this installation."""
        async with self._database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                update public.extension_control_streams
                set disconnected_at = now(), updated_at = now()
                where extension_installation_id = %s
                """,
                (self._installation_id,),
            )

    async def truncate(self, *, resume_from: int) -> int:
        """Move the cursor back to the resume point and supersede what follows.

        Returns how many frames were superseded, which is what distinguishes a
        resumed stream from a restarted one.
        """
        async with self._database.transaction() as conn:
            stream = await self._lock(conn)
            last_sequence = as_int(stream, "last_sequence")
            if resume_from > last_sequence:
                raise ProtocolError(
                    "sequence_gap",
                    f"the extension claims sequence {resume_from} above {last_sequence}",
                )
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update public.extension_control_frames
                    set superseded_at = now()
                    where extension_installation_id = %s and sequence > %s
                      and superseded_at is null
                    """,
                    (self._installation_id, resume_from),
                )
                superseded = cur.rowcount
                await cur.execute(
                    """
                    update public.extension_control_streams
                    set last_sequence = %s, acknowledged_sequence = %s, updated_at = now()
                    where extension_installation_id = %s
                    """,
                    (resume_from, resume_from, self._installation_id),
                )
        return superseded

    async def record(
        self, frame: OutgoingFrame, *, interview_session_id: UUID | None
    ) -> StampedFrame:
        """Stamp one frame, allocating and persisting its sequence when it has one."""
        message_id = uuid7()
        sent_at = datetime.now(tz=UTC)
        if not frame.sequenced:
            return StampedFrame(message_id=message_id, sent_at=sent_at, sequence=None)
        async with self._database.transaction() as conn:
            stream = await self._lock(conn)
            sequence = as_int(stream, "last_sequence") + 1
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update public.extension_control_streams
                    set last_sequence = %s, updated_at = now()
                    where extension_installation_id = %s
                    """,
                    (sequence, self._installation_id),
                )
                await cur.execute(
                    """
                    insert into public.extension_control_frames (
                        id, client_id, extension_installation_id,
                        interview_session_id, sequence, kind, payload
                    )
                    values (%s, null, %s, %s, %s, %s, %s)
                    """,
                    (
                        message_id,
                        self._installation_id,
                        interview_session_id,
                        sequence,
                        frame.kind,
                        Jsonb(dict(frame.payload)),
                    ),
                )
        return StampedFrame(message_id=message_id, sent_at=sent_at, sequence=sequence)

    async def acknowledge(self, sequence: int, *, strict: bool) -> None:
        """Record the highest contiguous sequence the extension processed.

        A sequence above what was sent is an integrity failure. A repeat is
        ignored; a regression is refused only when the extension declared it as
        an acknowledgement rather than as a heartbeat position.
        """
        async with self._database.transaction() as conn:
            stream = await self._lock(conn)
            last_sequence = as_int(stream, "last_sequence")
            acknowledged = as_int(stream, "acknowledged_sequence")
            if sequence > last_sequence:
                raise ProtocolError(
                    "sequence_gap",
                    f"acknowledged {sequence} above the sent sequence {last_sequence}",
                )
            if sequence <= acknowledged:
                if strict and sequence < acknowledged:
                    raise ProtocolError(
                        "sequence_regression",
                        f"acknowledged {sequence} below {acknowledged}",
                    )
                return
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update public.extension_control_streams
                    set acknowledged_sequence = %s, updated_at = now()
                    where extension_installation_id = %s
                    """,
                    (sequence, self._installation_id),
                )
                await cur.execute(
                    """
                    update public.extension_control_frames
                    set acknowledged_at = now()
                    where extension_installation_id = %s and sequence <= %s
                      and acknowledged_at is null and superseded_at is null
                    """,
                    (self._installation_id, sequence),
                )

    async def _lock(self, conn: AsyncConnection[DictRow]) -> DictRow:
        """Lock the stream row so two frames cannot take the same sequence."""
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.extension_control_streams (extension_installation_id)
                values (%s)
                on conflict (extension_installation_id) do nothing
                """,
                (self._installation_id,),
            )
            await cur.execute(
                """
                select last_sequence, acknowledged_sequence
                from public.extension_control_streams
                where extension_installation_id = %s
                for update
                """,
                (self._installation_id,),
            )
            row = await cur.fetchone()
        if row is None:
            raise ProtocolError("unknown_stream", "the control stream row is missing")
        return row
