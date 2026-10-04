"""Global action preferences, owned by the control plane.

Durable extension storage keeps only a cache of these values, so a change is a
request rather than a local decision. User preferences may narrow browser
authority immediately; they can never broaden it, and no per-client,
per-session, or per-command prompt can either.

The revision is monotonic and an update declares the revision it expected, so a
settings page working from a stale cache is refused rather than silently
overwriting a newer choice.
"""

from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.db.pool import Database
from app.db.rows import as_bool, as_int
from app.errors import AggregateNotFoundError, RevisionConflictError
from app.logging import get_logger

__all__ = ["PreferencesRecord", "load_preferences", "update_preferences"]

_log = get_logger(__name__)

_TABLE = "extension_preferences"


@dataclass(frozen=True, slots=True)
class PreferencesRecord:
    """One person's global action preferences at one revision."""

    utrace_user_id: UUID
    preferences_revision: int
    visual_guidance_enabled: bool
    agent_cursor_enabled: bool


async def load_preferences(
    conn: AsyncConnection[DictRow], *, utrace_user_id: UUID
) -> PreferencesRecord:
    """Return the current preferences, creating the default row on first read."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            insert into public.{_TABLE} (utrace_user_id)
            values (%s)
            on conflict (utrace_user_id) do nothing
            """,  # the table name is a module constant, never caller input
            (utrace_user_id,),
        )
        await cur.execute(
            f"""
            select preferences_revision, visual_guidance_enabled, agent_cursor_enabled
            from public.{_TABLE}
            where utrace_user_id = %s
            """,
            (utrace_user_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table=_TABLE, row_id=utrace_user_id)
    return PreferencesRecord(
        utrace_user_id=utrace_user_id,
        preferences_revision=as_int(row, "preferences_revision"),
        visual_guidance_enabled=as_bool(row, "visual_guidance_enabled"),
        agent_cursor_enabled=as_bool(row, "agent_cursor_enabled"),
    )


async def update_preferences(
    database: Database,
    *,
    utrace_user_id: UUID,
    expected_revision: int,
    visual_guidance_enabled: bool,
    agent_cursor_enabled: bool,
) -> PreferencesRecord:
    """Apply one requested change against the revision the caller expected."""
    async with database.transaction() as conn:
        current = await load_preferences(conn, utrace_user_id=utrace_user_id)
        if current.preferences_revision != expected_revision:
            raise RevisionConflictError(
                table=_TABLE,
                row_id=utrace_user_id,
                expected=expected_revision,
                actual=current.preferences_revision,
            )
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                update public.{_TABLE}
                set visual_guidance_enabled = %s,
                    agent_cursor_enabled = %s,
                    preferences_revision = preferences_revision + 1,
                    updated_at = now()
                where utrace_user_id = %s
                returning preferences_revision
                """,
                (visual_guidance_enabled, agent_cursor_enabled, utrace_user_id),
            )
            row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table=_TABLE, row_id=utrace_user_id)
    _log.info(
        "extension_preferences_updated",
        utrace_user_id=str(utrace_user_id),
        preferences_revision=as_int(row, "preferences_revision"),
        visual_guidance_enabled=visual_guidance_enabled,
        agent_cursor_enabled=agent_cursor_enabled,
    )
    return PreferencesRecord(
        utrace_user_id=utrace_user_id,
        preferences_revision=as_int(row, "preferences_revision"),
        visual_guidance_enabled=visual_guidance_enabled,
        agent_cursor_enabled=agent_cursor_enabled,
    )
