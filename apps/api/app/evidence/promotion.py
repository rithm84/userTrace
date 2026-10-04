"""Promotion of telemetry into citable evidence.

Every promoted item preserves its canonical record or artifact reference, exact
source record IDs, client and activation scope, trace and target context, policy
and schema versions, correlation and completeness revision, source watermarks,
and the claim it supports or contradicts.

Before eligible source telemetry expires, every promoted item is materialized as
the minimum immutable artifact needed to preserve its meaning and provenance, so
a durable citation never depends solely on a record scheduled for deletion. A
materialized artifact does not transform a user statement into telemetry or
telemetry into user intent: the source class travels with the item.

Contract: docs/arch-components/telemetry-and-evidence.md#evidence-windows-and-promotion
"""

from collections.abc import Sequence
from dataclasses import dataclass
from json import dumps
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from app.db.pool import Database
from app.db.rows import as_optional_str, as_str, as_uuid
from app.errors import AggregateNotFoundError
from app.evidence.windows import EvidenceWindow, load_window
from app.ids import uuid7
from app.ingestion.artifacts import ArtifactStore
from app.ingestion.errors import QueryAuthorizationError
from app.logging import get_logger

__all__ = ["PromotedItem", "PromotionRequest", "materialize_window", "promote_items"]

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PromotionRequest:
    """One item a caller asks the control plane to promote into a window."""

    source_class: str
    source_record_id: UUID
    claim_ref: str
    supports: bool


@dataclass(frozen=True, slots=True)
class PromotedItem:
    """One cited item, with everything needed to resolve it later."""

    evidence_item_id: UUID
    source_class: str
    source_record_id: UUID
    claim_ref: str
    supports: bool
    artifact_id: UUID | None


_SOURCE_TABLES = {
    "browser_observation": "browser_observations",
    "server_span": "server_spans",
}


async def promote_items(
    database: Database,
    *,
    evidence_window_id: UUID,
    requests: Sequence[PromotionRequest],
) -> tuple[PromotedItem, ...]:
    """Promote source records into one evidence window.

    Every request is checked against the window's client scope and its allowed
    source classes before anything is written: a window that admits only browser
    observations cannot be made to cite a server span.
    """
    window = await load_window(database, evidence_window_id=evidence_window_id)
    promoted: list[PromotedItem] = []
    async with database.transaction() as conn:
        for request in requests:
            if request.source_class not in window.allowed_source_classes:
                raise QueryAuthorizationError(
                    subject=f"evidence window {evidence_window_id}",
                    reason=(
                        f"source class '{request.source_class}' is not among the "
                        "classes this window admits"
                    ),
                )
            table = _SOURCE_TABLES.get(request.source_class)
            if table is None:
                raise QueryAuthorizationError(
                    subject=f"evidence window {evidence_window_id}",
                    reason=f"'{request.source_class}' is not a telemetry source class",
                )
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    select id, client_id, trace_id, span_id, policy_version,
                           source_schema_version
                    from public.{table}
                    where id = %s and client_id = %s
                    """,
                    (request.source_record_id, window.client_id),
                )
                row = await cur.fetchone()
            if row is None:
                raise AggregateNotFoundError(
                    table=table, row_id=request.source_record_id
                )
            item_id = uuid7()
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    insert into public.telemetry_evidence_items (
                        id, client_id, evidence_window_id, source_class,
                        source_record_id, trace_id, span_id, claim_ref, supports,
                        policy_version, source_schema_version, correlation_revision,
                        watermarks
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (
                        evidence_window_id, source_class, source_record_id, claim_ref
                    ) do nothing
                    """,
                    (
                        item_id,
                        window.client_id,
                        evidence_window_id,
                        request.source_class,
                        request.source_record_id,
                        as_optional_str(row, "trace_id"),
                        as_optional_str(row, "span_id"),
                        request.claim_ref,
                        request.supports,
                        as_str(row, "policy_version"),
                        as_str(row, "source_schema_version"),
                        window.watermarks.correlation_revision,
                        Jsonb(dict(window.watermarks.as_json())),
                    ),
                )
            promoted.append(
                PromotedItem(
                    evidence_item_id=item_id,
                    source_class=request.source_class,
                    source_record_id=request.source_record_id,
                    claim_ref=request.claim_ref,
                    supports=request.supports,
                    artifact_id=None,
                )
            )
    _log.info(
        "telemetry_evidence_promoted",
        client_id=str(window.client_id),
        evidence_window_id=str(evidence_window_id),
        item_count=len(promoted),
    )
    return tuple(promoted)


async def materialize_window(
    database: Database, *, artifacts: ArtifactStore, evidence_window_id: UUID
) -> tuple[PromotedItem, ...]:
    """Materialize every promoted item in one window as an immutable artifact.

    Only the cited items are materialized. Unrelated surrounding telemetry is not
    retained merely because one item next to it was promoted.
    """
    window = await load_window(database, evidence_window_id=evidence_window_id)
    materialized: list[PromotedItem] = []
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select id, source_class, source_record_id, claim_ref, supports,
                       artifact_id
                from public.telemetry_evidence_items
                where evidence_window_id = %s and client_id = %s
                order by promoted_at
                """,
                (evidence_window_id, window.client_id),
            )
            items = await cur.fetchall()
        for item in items:
            if item["artifact_id"] is not None:
                continue
            source_class = as_str(item, "source_class")
            body = await _minimum_evidence_body(
                conn,
                window=window,
                source_class=source_class,
                source_record_id=as_uuid(item, "source_record_id"),
                claim_ref=as_str(item, "claim_ref"),
            )
            artifact = await artifacts.put(
                conn,
                client_id=window.client_id,
                artifact_class="promoted_evidence",
                media_type="application/json",
                schema_type=f"telemetry/promoted-{source_class}.v1",
                retention_class="evidence_citation",
                body=body,
                manifest=None,
            )
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update public.telemetry_evidence_items
                    set artifact_id = %s
                    where id = %s
                    """,
                    (artifact.artifact_id, as_uuid(item, "id")),
                )
            materialized.append(
                PromotedItem(
                    evidence_item_id=as_uuid(item, "id"),
                    source_class=source_class,
                    source_record_id=as_uuid(item, "source_record_id"),
                    claim_ref=as_str(item, "claim_ref"),
                    supports=bool(item["supports"]),
                    artifact_id=artifact.artifact_id,
                )
            )
    _log.info(
        "telemetry_evidence_materialized",
        client_id=str(window.client_id),
        evidence_window_id=str(evidence_window_id),
        item_count=len(materialized),
    )
    return tuple(materialized)


async def _minimum_evidence_body(
    conn: AsyncConnection[DictRow],
    *,
    window: EvidenceWindow,
    source_class: str,
    source_record_id: UUID,
    claim_ref: str,
) -> bytes:
    """Build the smallest immutable body that preserves meaning and provenance."""
    table = _SOURCE_TABLES[source_class]
    async with conn.cursor() as cur:
        await cur.execute(
            f"select * from public.{table} where id = %s and client_id = %s",
            (source_record_id, window.client_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table=table, row_id=source_record_id)
    body = {
        "source_class": source_class,
        "source_record_id": str(source_record_id),
        "claim_ref": claim_ref,
        "client_id": str(window.client_id),
        "interview_session_id": str(window.interview_session_id),
        "product_signal_id": str(window.product_signal_id),
        "evidence_window_id": str(window.evidence_window_id),
        "watermarks": dict(window.watermarks.as_json()),
        "record": {key: _jsonable(value) for key, value in row.items()},
    }
    return dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _jsonable(value: object) -> object:
    """Render one column value for the immutable evidence body."""
    match value:
        case None | bool() | int() | float() | str():
            return value
        case dict():
            return {str(key): _jsonable(item) for key, item in value.items()}
        case list():
            return [_jsonable(item) for item in value]
        case _:
            return str(value)
