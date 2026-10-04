"""Apply unapplied Supabase SQL migrations directly to the database in ``UTRACE_DATABASE_URL``.

The Supabase CLI's ``db push`` refuses to run when the remote database records migration
versions that are absent from the local checkout, which happens whenever several build
worktrees share one database or one worktree targets a fresh database. This script applies
each local file under ``supabase/migrations`` whose version is not yet recorded in
``supabase_migrations.schema_migrations`` and records it exactly as the CLI would, so a later
``supabase db push`` sees an up-to-date database.

Run from the repository root or ``apps/api``::

    uv run --project apps/api python apps/api/scripts/apply_migrations.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import psycopg

MIGRATION_NAME = re.compile(r"^(?P<version>\d{14})_(?P<name>[a-z0-9_]+)\.sql$")


def repository_root() -> Path:
    for candidate in (Path.cwd(), *Path(__file__).resolve().parents):
        if (candidate / "supabase" / "migrations").is_dir():
            return candidate
    raise FileNotFoundError("no supabase/migrations directory above the current directory")


def local_migrations(root: Path) -> list[tuple[str, str, Path]]:
    found: list[tuple[str, str, Path]] = []
    for path in sorted((root / "supabase" / "migrations").glob("*.sql")):
        match = MIGRATION_NAME.match(path.name)
        if match is None:
            raise ValueError(f"migration file name is not <14-digit version>_<snake_name>.sql: {path.name}")
        found.append((match.group("version"), match.group("name"), path))
    return found


def main() -> int:
    url = os.environ.get("UTRACE_DATABASE_URL")
    if url is None:
        raise KeyError("UTRACE_DATABASE_URL is not set; source the repository .env.local first")
    root = repository_root()
    migrations = local_migrations(root)
    with psycopg.connect(url, autocommit=False) as connection:
        connection.execute("create schema if not exists supabase_migrations")
        connection.execute(
            "create table if not exists supabase_migrations.schema_migrations "
            "(version text primary key, statements text[], name text)"
        )
        applied = {
            row[0] for row in connection.execute("select version from supabase_migrations.schema_migrations")
        }
        connection.commit()
        pending = [item for item in migrations if item[0] not in applied]
        for version, name, path in pending:
            sql = path.read_text()
            connection.execute(sql)
            connection.execute(
                "insert into supabase_migrations.schema_migrations (version, name, statements) values (%s, %s, %s)",
                (version, name, [sql]),
            )
            connection.commit()
            print(f"applied {path.name}")
        print(f"{len(pending)} applied, {len(migrations) - len(pending)} already recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
