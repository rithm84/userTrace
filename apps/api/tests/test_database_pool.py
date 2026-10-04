"""The pool survives the server closing its connections, as a database restart does."""

from __future__ import annotations

from psycopg import AsyncConnection

from app.db.pool import Database
from app.settings import Settings

_TERMINATION_WAIT_MILLISECONDS = 5_000


async def _backend_pid(database: Database) -> int:
    async with database.connection() as conn:
        row = await (await conn.execute("select pg_backend_pid() as pid")).fetchone()
    assert row is not None
    return int(row["pid"])


async def test_a_connection_the_server_closed_is_replaced_on_lease(settings: Settings) -> None:
    database = Database(dsn=settings.database_url, min_size=1, max_size=1)
    await database.open()
    try:
        closed_pid = await _backend_pid(database)
        async with await AsyncConnection.connect(settings.database_url, autocommit=True) as admin:
            await admin.execute(
                "select pg_terminate_backend(%s, %s)", (closed_pid, _TERMINATION_WAIT_MILLISECONDS)
            )

        assert await _backend_pid(database) != closed_pid
    finally:
        await database.close()
