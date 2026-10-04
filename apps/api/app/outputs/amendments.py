"""Amendments: the one thing that may follow a published Case File.

A correction is a separately identified, append-only record beside the Case File,
never a silent rewrite of the published core.

Contract: docs/arch-components/client-outputs.md#case-file-identity-and-assembly
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict
from utrace_contracts.domain.lifecycle import ActorKind

from app.db.pool import Database
from app.db.rows import as_datetime, as_int, as_str, as_uuid
from app.envelope import JsonValue
from app.ids import uuid7
from app.logging import get_logger

__all__ = [
    "CaseFileAmendment",
    "load_amendments",
    "record_amendment",
]

_log = get_logger(__name__)


class _Frozen(BaseModel):
    """Immutable, closed projection shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class CaseFileAmendment(_Frozen):
    """One separately identified correction recorded beside a published Case File."""

    id: UUID
    case_file_id: UUID
    ordinal: int
    reason: str
    recorded_at: datetime


async def record_amendment(
    database: Database,
    *,
    client_id: UUID,
    case_file_id: UUID,
    reason: str,
    body: dict[str, JsonValue],
    actor_kind: ActorKind,
    actor_ref: str,
) -> CaseFileAmendment:
    """Append one amendment beside a published Case File.

    The sealed core is never touched: an amendment is its own record with its own
    identity, so a reader can see both what was published and what corrected it.
    """
    amendment_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.case_file_amendments (
                id, client_id, case_file_id, ordinal, reason, body, actor_kind,
                actor_ref
            )
            select %s, %s, %s,
                   coalesce(max(existing.ordinal), 0) + 1,
                   %s, %s, %s, %s
            from public.case_file_amendments as existing
            where existing.case_file_id = %s
            returning ordinal, recorded_at
            """,
            (
                amendment_id,
                client_id,
                case_file_id,
                reason,
                Jsonb(body),
                actor_kind.value,
                actor_ref,
                case_file_id,
            ),
        )
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError("the amendment insert returned no row")
    amendment = CaseFileAmendment(
        id=amendment_id,
        case_file_id=case_file_id,
        ordinal=as_int(row, "ordinal"),
        reason=reason,
        recorded_at=as_datetime(row, "recorded_at"),
    )
    _log.info(
        "case_file_amended",
        client_id=str(client_id),
        case_file_id=str(case_file_id),
        amendment_id=str(amendment_id),
        ordinal=amendment.ordinal,
    )
    return amendment


async def load_amendments(
    database: Database, *, client_id: UUID, case_file_id: UUID
) -> tuple[CaseFileAmendment, ...]:
    """Return the amendments recorded beside one Case File, oldest first."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, case_file_id, ordinal, reason, recorded_at
            from public.case_file_amendments
            where client_id = %s and case_file_id = %s
            order by ordinal
            """,
            (client_id, case_file_id),
        )
        rows = list(await cur.fetchall())
    return tuple(
        CaseFileAmendment(
            id=as_uuid(row, "id"),
            case_file_id=as_uuid(row, "case_file_id"),
            ordinal=as_int(row, "ordinal"),
            reason=as_str(row, "reason"),
            recorded_at=as_datetime(row, "recorded_at"),
        )
        for row in rows
    )
