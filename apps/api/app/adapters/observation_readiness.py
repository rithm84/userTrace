"""``ObservationReadinessPort`` over the telemetry ingestion stream.

The preview readiness runner asks two questions it cannot answer itself: whether
the prepared runtime's Server SDK activated, and whether the browser, server, and
correlation observation paths are ready. Both are read here from durable
ingestion state.

Activation is observed, never caused. The runtime's own SDK activates over
``WS /v1/server-sdk/control`` using the bootstrap credential the preview stream
injected; this adapter reports what that handshake committed. A preview whose SDK
has not activated reports ``activated=false`` with the reason, and the probe
fails rather than being recorded as a pass nobody observed.

Readiness is read from the newest ``telemetry_readiness_projections`` row per
kind, and an expired projection is not readiness: a stale projection can never
authorize observation-dependent probing.

Contract: docs/arch-components/telemetry-and-evidence.md#completeness-and-readiness
"""

from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.db.pool import Database
from app.db.rows import as_bool, as_datetime, as_optional_str, as_str, as_uuid
from app.ingestion.readiness import READINESS_LIFETIME_SECONDS
from app.ingestion.settings import get_ingestion_settings
from app.logging import get_logger
from app.preview.ports import ServerSdkActivation, TelemetryReadiness

__all__ = ["IngestionObservationReadiness"]

_log = get_logger(__name__)

#: The kinds ``telemetry_readiness_projections`` records, in report order.
_KINDS = ("browser", "server", "correlation")

#: What the health reports call the SDK build. Absent until one arrives.
_UNREPORTED_SDK_VERSION = "unreported"


class IngestionObservationReadiness:
    """``ObservationReadinessPort`` backed by the ingestion stream's own rows."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def activation(
        self,
        *,
        client_id: UUID,
        interview_session_id: UUID,
        preview_runtime_allocation_id: UUID,
    ) -> ServerSdkActivation:
        """Report the server activation the runtime's own SDK committed."""
        async with self._database.connection() as conn:
            row = await _server_activation_row(
                conn,
                client_id=client_id,
                interview_session_id=interview_session_id,
                preview_runtime_allocation_id=preview_runtime_allocation_id,
            )
            if row is None:
                return ServerSdkActivation(
                    activated=False,
                    service_name="",
                    manifest_trust_anchor_key_id="",
                    sdk_version=_UNREPORTED_SDK_VERSION,
                    activated_at=None,
                    preview_entry_token=None,
                    detail=(
                        "the prepared runtime's Server SDK has not activated on "
                        "WS /v1/server-sdk/control for this allocation"
                    ),
                )
            activation_id = as_uuid(row, "id")
            sdk_version = await _latest_sdk_version(conn, activation_id=activation_id)
            entry_token = as_optional_str(row, "preview_entry_token")
        activated = entry_token is not None
        return ServerSdkActivation(
            activated=activated,
            service_name=as_str(row, "service_key"),
            manifest_trust_anchor_key_id=(
                get_ingestion_settings().server_manifest_trust_anchor_key_id
            ),
            sdk_version=sdk_version,
            activated_at=as_datetime(row, "created_at"),
            preview_entry_token=entry_token,
            detail=(
                ""
                if activated
                else "the activation issued no preview entry token for the allocation"
            ),
        )

    async def readiness(
        self, *, client_id: UUID, interview_session_id: UUID
    ) -> TelemetryReadiness:
        """Project the session's browser, server, and correlation readiness."""
        observed_at = datetime.now(tz=UTC)
        async with self._database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select distinct on (kind)
                       kind, ready, revision, checked_at, expires_at, failures
                from public.telemetry_readiness_projections
                where client_id = %s and interview_session_id = %s
                order by kind, checked_at desc, revision desc
                """,
                (client_id, interview_session_id),
            )
            rows = await cur.fetchall()
        current = {as_str(row, "kind"): row for row in rows}
        ready: dict[str, bool] = {}
        reasons: list[str] = []
        for kind in _KINDS:
            row = current.get(kind)
            if row is None:
                ready[kind] = False
                reasons.append(f"{kind}: no readiness projection")
                continue
            expires_at = as_datetime(row, "expires_at")
            if expires_at <= observed_at:
                ready[kind] = False
                reasons.append(
                    f"{kind}: the projection expired at {expires_at.isoformat()}"
                )
                continue
            ready[kind] = as_bool(row, "ready")
            if not ready[kind]:
                reasons.append(f"{kind}: {_failure_codes(row)}")
        _log.info(
            "observation_readiness_read",
            client_id=str(client_id),
            interview_session_id=str(interview_session_id),
            browser_ready=ready["browser"],
            server_ready=ready["server"],
            correlation_ready=ready["correlation"],
            readiness_lifetime_seconds=READINESS_LIFETIME_SECONDS,
        )
        return TelemetryReadiness(
            browser_ready=ready["browser"],
            server_ready=ready["server"],
            correlation_ready=ready["correlation"],
            observed_at=observed_at,
            detail="; ".join(reasons),
        )


def _failure_codes(row: DictRow) -> str:
    """Render one projection's typed failure codes, or say it reported none."""
    failures = row["failures"]
    if not isinstance(failures, list) or len(failures) == 0:
        return "not ready, with no failure recorded"
    codes = [
        str(failure["code"])
        for failure in failures
        if isinstance(failure, dict) and "code" in failure
    ]
    return ", ".join(codes) if len(codes) > 0 else "not ready, with no failure code"


async def _server_activation_row(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    interview_session_id: UUID,
    preview_runtime_allocation_id: UUID,
) -> DictRow | None:
    """Read the active server activation bound to one allocation."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select activations.id, activations.service_key, activations.created_at,
                   allocations.preview_entry_token
            from public.telemetry_activations as activations
            join public.preview_runtime_allocations as allocations
              on allocations.id = activations.preview_runtime_allocation_id
            where activations.kind = 'server'
              and activations.state = 'active'
              and activations.client_id = %s
              and activations.interview_session_id = %s
              and activations.preview_runtime_allocation_id = %s
            order by activations.created_at desc
            limit 1
            """,
            (client_id, interview_session_id, preview_runtime_allocation_id),
        )
        return await cur.fetchone()


async def _latest_sdk_version(
    conn: AsyncConnection[DictRow], *, activation_id: UUID
) -> str:
    """Return the SDK build the newest health report named, if one arrived."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select sdk_version from public.server_health_reports
            where activation_id = %s order by reported_at desc limit 1
            """,
            (activation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return _UNREPORTED_SDK_VERSION
    return as_str(row, "sdk_version")
