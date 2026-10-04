"""Evidence windows: rolling live views and frozen immutable selections.

``evidence_window_id`` identifies one bounded selection of observations for a
live interview view, gate decision, implementation input, validation outcome,
Case File core version, or evaluation run. The window records purpose,
authorized caller, domain identities, query contract, source watermarks,
completeness state, creation time, and whether it is rolling or frozen.

Freezing captures the exact browser stream watermarks, server batch watermarks,
trace completeness revisions, and an ``accepted_through`` time, so data arriving
later cannot be represented as though the earlier decision saw it.

Contract: docs/arch-components/telemetry-and-evidence.md#evidence-windows-and-promotion
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from app.db.pool import Database
from app.db.rows import as_datetime, as_int, as_str, as_uuid
from app.envelope import JsonValue
from app.errors import AggregateNotFoundError
from app.ids import uuid7
from app.ingestion.errors import WindowFrozenError
from app.logging import get_logger

__all__ = [
    "EvidenceWindow",
    "Watermarks",
    "advance_rolling_window",
    "current_watermarks",
    "freeze_window",
    "load_window",
    "open_rolling_window",
]

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Watermarks:
    """What the ingestion path had accepted at one instant."""

    browser_accepted_through: int
    server_accepted_through: int
    correlation_revision: int
    accepted_through: datetime

    def as_json(self) -> Mapping[str, JsonValue]:
        """Return the watermark block a frozen evidence use records."""
        return {
            "browser_accepted_through": self.browser_accepted_through,
            "server_accepted_through": self.server_accepted_through,
            "correlation_revision": self.correlation_revision,
            "accepted_through": self.accepted_through.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class EvidenceWindow:
    """One bounded selection of observations."""

    evidence_window_id: UUID
    client_id: UUID
    interview_session_id: UUID
    product_signal_id: UUID
    mode: str
    purpose: str
    authorized_caller: str
    allowed_source_classes: tuple[str, ...]
    starts_at: datetime
    watermarks: Watermarks
    revision: int

    def is_frozen(self) -> bool:
        """Return whether later data can still reach this window."""
        return self.mode == "frozen"


async def current_watermarks(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID
) -> Watermarks:
    """Read the accepted watermarks of every stream bound to one session."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select
              coalesce((
                select max(streams.accepted_through_export_sequence)
                from public.browser_streams as streams
                join public.telemetry_activations as activations
                  on activations.id = streams.activation_id
                where activations.interview_session_id = %(session)s
              ), 0) as browser_accepted_through,
              coalesce((
                select max(streams.accepted_through_batch_sequence)
                from public.server_streams as streams
                join public.telemetry_activations as activations
                  on activations.id = streams.activation_id
                where activations.interview_session_id = %(session)s
              ), 0) as server_accepted_through,
              coalesce((
                select max(projections.revision)
                from public.correlation_projections as projections
                where projections.interview_session_id = %(session)s
              ), 0) as correlation_revision
            """,
            {"session": interview_session_id},
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="interview_sessions", row_id=interview_session_id
        )
    return Watermarks(
        browser_accepted_through=as_int(row, "browser_accepted_through"),
        server_accepted_through=as_int(row, "server_accepted_through"),
        correlation_revision=as_int(row, "correlation_revision"),
        accepted_through=datetime.now(tz=UTC),
    )


async def open_rolling_window(
    database: Database,
    *,
    client_id: UUID,
    interview_session_id: UUID,
    product_signal_id: UUID,
    activation_id: UUID | None,
    purpose: str,
    authorized_caller: str,
    allowed_source_classes: Sequence[str],
    query_contract: Mapping[str, JsonValue],
    domain_identities: Mapping[str, JsonValue],
) -> EvidenceWindow:
    """Open one rolling live window over the active session's current readiness."""
    window_id = uuid7()
    async with database.transaction() as conn:
        watermarks = await current_watermarks(
            conn, interview_session_id=interview_session_id
        )
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.telemetry_evidence_windows (
                    id, client_id, interview_session_id, product_signal_id,
                    activation_id, mode, purpose, authorized_caller,
                    domain_identities, query_contract, allowed_source_classes,
                    starts_at, accepted_through, browser_accepted_through,
                    server_accepted_through, completeness, correlation_revision
                )
                values (
                    %s, %s, %s, %s, %s, 'rolling', %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s
                )
                """,
                (
                    window_id,
                    client_id,
                    interview_session_id,
                    product_signal_id,
                    activation_id,
                    purpose,
                    authorized_caller,
                    Jsonb(dict(domain_identities)),
                    Jsonb(dict(query_contract)),
                    list(allowed_source_classes),
                    watermarks.accepted_through,
                    watermarks.accepted_through,
                    watermarks.browser_accepted_through,
                    watermarks.server_accepted_through,
                    Jsonb({"state": "advancing"}),
                    watermarks.correlation_revision,
                ),
            )
    _log.info(
        "evidence_window_opened",
        client_id=str(client_id),
        evidence_window_id=str(window_id),
        purpose=purpose,
        mode="rolling",
    )
    return EvidenceWindow(
        evidence_window_id=window_id,
        client_id=client_id,
        interview_session_id=interview_session_id,
        product_signal_id=product_signal_id,
        mode="rolling",
        purpose=purpose,
        authorized_caller=authorized_caller,
        allowed_source_classes=tuple(allowed_source_classes),
        starts_at=watermarks.accepted_through,
        watermarks=watermarks,
        revision=0,
    )


async def advance_rolling_window(
    database: Database, *, evidence_window_id: UUID
) -> EvidenceWindow:
    """Advance a rolling window to the session's current watermarks.

    A frozen window refuses: later data needs a new window and cannot alter the
    contents an earlier decision cited.
    """
    async with database.transaction() as conn:
        window = await _load(conn, evidence_window_id=evidence_window_id, lock=True)
        if window.is_frozen():
            raise WindowFrozenError(evidence_window_id=evidence_window_id)
        watermarks = await current_watermarks(
            conn, interview_session_id=window.interview_session_id
        )
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update public.telemetry_evidence_windows
                set accepted_through = %s,
                    browser_accepted_through = %s,
                    server_accepted_through = %s,
                    correlation_revision = %s,
                    revision = revision + 1,
                    updated_at = now()
                where id = %s
                returning revision
                """,
                (
                    watermarks.accepted_through,
                    watermarks.browser_accepted_through,
                    watermarks.server_accepted_through,
                    watermarks.correlation_revision,
                    evidence_window_id,
                ),
            )
            row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="telemetry_evidence_windows", row_id=evidence_window_id
        )
    return EvidenceWindow(
        evidence_window_id=window.evidence_window_id,
        client_id=window.client_id,
        interview_session_id=window.interview_session_id,
        product_signal_id=window.product_signal_id,
        mode="rolling",
        purpose=window.purpose,
        authorized_caller=window.authorized_caller,
        allowed_source_classes=window.allowed_source_classes,
        starts_at=window.starts_at,
        watermarks=watermarks,
        revision=as_int(row, "revision"),
    )


async def freeze_window(
    database: Database,
    *,
    source_window_id: UUID,
    purpose: str,
    authorized_caller: str,
    completeness: Mapping[str, JsonValue],
) -> EvidenceWindow:
    """Freeze a new immutable window from a rolling one.

    Freezing copies the rolling window's bounds into a new row rather than
    sealing the rolling one, so the live view keeps advancing while the decision
    cites a selection that can never change.
    """
    frozen_id = uuid7()
    async with database.transaction() as conn:
        source = await _load(conn, evidence_window_id=source_window_id, lock=True)
        if source.is_frozen():
            raise WindowFrozenError(evidence_window_id=source_window_id)
        watermarks = await current_watermarks(
            conn, interview_session_id=source.interview_session_id
        )
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.telemetry_evidence_windows (
                    id, client_id, interview_session_id, product_signal_id,
                    activation_id, mode, purpose, authorized_caller,
                    domain_identities, query_contract, allowed_source_classes,
                    starts_at, accepted_through, browser_accepted_through,
                    server_accepted_through, completeness, correlation_revision,
                    frozen_at
                )
                select %s, client_id, interview_session_id, product_signal_id,
                       activation_id, 'frozen', %s, %s, domain_identities,
                       query_contract, allowed_source_classes, starts_at, %s, %s, %s,
                       %s, %s, now()
                from public.telemetry_evidence_windows
                where id = %s
                """,
                (
                    frozen_id,
                    purpose,
                    authorized_caller,
                    watermarks.accepted_through,
                    watermarks.browser_accepted_through,
                    watermarks.server_accepted_through,
                    Jsonb(dict(completeness)),
                    watermarks.correlation_revision,
                    source_window_id,
                ),
            )
    _log.info(
        "evidence_window_frozen",
        client_id=str(source.client_id),
        source_window_id=str(source_window_id),
        evidence_window_id=str(frozen_id),
        purpose=purpose,
        browser_accepted_through=watermarks.browser_accepted_through,
        server_accepted_through=watermarks.server_accepted_through,
    )
    return EvidenceWindow(
        evidence_window_id=frozen_id,
        client_id=source.client_id,
        interview_session_id=source.interview_session_id,
        product_signal_id=source.product_signal_id,
        mode="frozen",
        purpose=purpose,
        authorized_caller=authorized_caller,
        allowed_source_classes=source.allowed_source_classes,
        starts_at=source.starts_at,
        watermarks=watermarks,
        revision=0,
    )


async def load_window(database: Database, *, evidence_window_id: UUID) -> EvidenceWindow:
    """Load one evidence window."""
    async with database.connection() as conn:
        return await _load(conn, evidence_window_id=evidence_window_id, lock=False)


async def _load(
    conn: AsyncConnection[DictRow], *, evidence_window_id: UUID, lock: bool
) -> EvidenceWindow:
    """Read one window, optionally locking it for a freeze or an advance."""
    statement = """
        select id, client_id, interview_session_id, product_signal_id, mode, purpose,
               authorized_caller, allowed_source_classes, starts_at, accepted_through,
               browser_accepted_through, server_accepted_through, correlation_revision,
               revision
        from public.telemetry_evidence_windows
        where id = %s
    """
    if lock:
        statement += " for update"
    async with conn.cursor() as cur:
        await cur.execute(statement, (evidence_window_id,))
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="telemetry_evidence_windows", row_id=evidence_window_id
        )
    classes = row["allowed_source_classes"]
    if not isinstance(classes, list):
        raise TypeError("allowed_source_classes is not an array")
    return EvidenceWindow(
        evidence_window_id=as_uuid(row, "id"),
        client_id=as_uuid(row, "client_id"),
        interview_session_id=as_uuid(row, "interview_session_id"),
        product_signal_id=as_uuid(row, "product_signal_id"),
        mode=as_str(row, "mode"),
        purpose=as_str(row, "purpose"),
        authorized_caller=as_str(row, "authorized_caller"),
        allowed_source_classes=tuple(str(item) for item in classes),
        starts_at=as_datetime(row, "starts_at"),
        watermarks=Watermarks(
            browser_accepted_through=as_int(row, "browser_accepted_through"),
            server_accepted_through=as_int(row, "server_accepted_through"),
            correlation_revision=as_int(row, "correlation_revision"),
            accepted_through=as_datetime(row, "accepted_through"),
        ),
        revision=as_int(row, "revision"),
    )
