"""``demo reset-target``: return a demo database to its pre-``prepare`` state.

The control plane exposes no delete route for any of this, so the reset is the
one place the harness speaks SQL. It is deliberately blunt and deliberately
loud: it prints every table it will touch before it touches anything, and it
refuses to run without ``--i-understand-this-deletes-demo-state``.

What it removes: every base table in the ``public`` schema, in one
``truncate ... cascade``. That is every client, not only the demo one, which is
why this belongs to a demo database and nowhere else. The schema itself, the
recorded migrations in ``supabase_migrations.schema_migrations``, and Supabase
Auth users in ``auth.users`` are untouched.

What it does not remove: queued wake-ups in the ``pgmq`` schema. Those reference
rows that truncation deletes, so a worker left running against a reset target
drains dead wake-ups. Stop the API and the worker before resetting.

A reset target is not demo-ready until it is reseeded and its preview template
is pinned again, from the repository root with ``.env.local`` loaded:
``apps/api/scripts/seed_shopalytics_client.py``, then
``apps/api/scripts/build_preview_template.py`` with ``--image-reference``,
``--snapshot-id``, ``--client-id``, the reseeded ``--configuration-release-id``,
and ``--warm-pool-target``. Without the template, claims cannot prepare a base
preview and the warm pool stays empty.
"""

from __future__ import annotations

import sys
from typing import Final

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from pydantic import BaseModel, ConfigDict

from demo_harness.settings import DatabaseSettings

__all__ = ["TableCount", "inventory", "print_inventory", "reset_target"]

_TABLE_QUERY: Final[str] = """
select table_name
from information_schema.tables
where table_schema = 'public' and table_type = 'BASE TABLE'
order by table_name
"""


class TableCount(BaseModel):
    """One public table and how many rows it currently holds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    table: str
    rows: int


async def _tables(conn: AsyncConnection[DictRow]) -> list[str]:
    """Return every base table in the ``public`` schema, alphabetically."""
    async with conn.cursor() as cur:
        await cur.execute(_TABLE_QUERY)
        return [str(row["table_name"]) for row in await cur.fetchall()]


async def _counts(conn: AsyncConnection[DictRow], tables: list[str]) -> list[TableCount]:
    """Return the row count of every named table."""
    counts: list[TableCount] = []
    async with conn.cursor() as cur:
        for table in tables:
            await cur.execute(f'select count(*) as total from public."{table}"')
            row = await cur.fetchone()
            counts.append(TableCount(table=table, rows=0 if row is None else int(row["total"])))
    return counts


async def inventory(settings: DatabaseSettings) -> list[TableCount]:
    """Report what the target currently holds, changing nothing."""
    async with await AsyncConnection.connect(settings.dsn, row_factory=dict_row) as conn:
        tables = await _tables(conn)
        return await _counts(conn, tables)


async def reset_target(settings: DatabaseSettings) -> list[TableCount]:
    """Truncate every public table and return what was removed."""
    async with await AsyncConnection.connect(settings.dsn, row_factory=dict_row) as conn:
        tables = await _tables(conn)
        before = await _counts(conn, tables)
        removed = [count for count in before if count.rows > 0]
        quoted = ", ".join(f'public."{table}"' for table in tables)
        sys.stdout.write(f"truncating {len(tables)} tables: {quoted}\n")
        async with conn.cursor() as cur:
            await cur.execute(f"truncate table {quoted} restart identity cascade")
        await conn.commit()
        after = await _counts(conn, tables)
    left = [count for count in after if count.rows > 0]
    if left:
        raise RuntimeError(
            f"truncation left rows behind in {[count.table for count in left]}; the target "
            f"is not in a clean pre-prepare state"
        )
    return removed


def print_inventory(title: str, counts: list[TableCount]) -> None:
    """Print the non-empty tables, or say the target is already clean."""
    occupied = [count for count in counts if count.rows > 0]
    sys.stdout.write(f"{title}\n")
    if not occupied:
        sys.stdout.write(f"  {len(counts)} public tables, all empty\n")
        return
    total = sum(count.rows for count in occupied)
    sys.stdout.write(
        f"  {total} rows across {len(occupied)} of {len(counts)} public tables:\n"
    )
    for count in sorted(occupied, key=lambda entry: (-entry.rows, entry.table)):
        sys.stdout.write(f"    {count.rows:>8}  public.{count.table}\n")
