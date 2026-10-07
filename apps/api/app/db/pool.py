"""Postgres connector.

One async connection pool over Supabase Postgres. This is the single transaction
authority: every durable write goes through a transaction opened here.
"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Final

from psycopg import AsyncConnection, sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.logging import get_logger

__all__ = ["Database"]

_log = get_logger(__name__)

#: libpq's TCP liveness settings for every connection. A connection whose packets
#: stop arriving otherwise looks healthy: a query sent on it waits out the kernel's
#: retransmission limit (about 15 minutes on Linux) and a reply that never arrives
#: is waited for indefinitely, which froze the worker's Job loop and left an API
#: transaction holding its locks. With these, a dead peer is noticed within about
#: 25 seconds while idle or waiting, and unacknowledged data fails after 20.
_TCP_LIVENESS: Final[Mapping[str, int]] = {
    "keepalives": 1,
    "keepalives_idle": 10,
    "keepalives_interval": 5,
    "keepalives_count": 3,
    "tcp_user_timeout": 20_000,
}


class Database:
    """Owns the connection pool and hands out connections and transactions."""

    def __init__(self, dsn: str, min_size: int, max_size: int) -> None:
        self._dsn = make_conninfo(dsn, **_TCP_LIVENESS)
        # A database restart or failover closes every server connection while the
        # pool still holds them; checking on lease replaces a dead one instead of
        # failing the request that drew it.
        self._pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
            conninfo=self._dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row, "autocommit": True},
            check=AsyncConnectionPool.check_connection,
            open=False,
        )

    async def open(self) -> None:
        """Open the pool and wait until the minimum connections are live."""
        await self._pool.open(wait=True)
        _log.info("database_pool_opened", min_size=self._pool.min_size, max_size=self._pool.max_size)

    async def close(self) -> None:
        """Drain and close the pool."""
        await self._pool.close()
        _log.info("database_pool_closed")

    async def now(self) -> datetime:
        """The database's current time, the clock every durable row is stamped with.

        A bound compared with stored timestamps comes from here, never from this
        process's clock, which can trail the database's by milliseconds.
        """
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("select now() as now")
            row = await cur.fetchone()
        if row is None or not isinstance(row["now"], datetime):
            raise TypeError("the database returned no timestamp for now()")
        return row["now"]

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection[DictRow]]:
        """Lease one autocommit connection for read-only work."""
        async with self._pool.connection() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection[DictRow]]:
        """Lease one connection inside an explicit transaction that commits once."""
        async with self._pool.connection() as conn, conn.transaction():
            yield conn

    @asynccontextmanager
    async def listener(self, channel: str) -> AsyncIterator[AsyncConnection[DictRow]]:
        """Open a dedicated connection that is already listening on ``channel``.

        A ``LISTEN`` belongs to the session that issued it, so it cannot borrow a
        pooled connection: the caller reads ``notifies()`` from this one for as
        long as it listens, and leaving the block closes it. The DSN must reach
        Postgres directly or through a session-mode pooler, which keeps one
        server session per client connection; a transaction-mode pooler drops
        the ``LISTEN`` after its statement.
        """
        async with await AsyncConnection.connect(
            self._dsn, autocommit=True, row_factory=dict_row
        ) as conn:
            await conn.execute(sql.SQL("listen {}").format(sql.Identifier(channel)))
            yield conn
