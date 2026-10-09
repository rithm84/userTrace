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
kind, for the page the participant has open (`open_page_readiness`): an expired
projection, or one evaluated for a page they have since left, is not readiness,
so a stale projection can never authorize observation-dependent probing.

Contract: docs/arch-components/telemetry-and-evidence.md#completeness-and-readiness
"""

from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.db.pool import Database
from app.db.rows import as_datetime, as_optional_str, as_str, as_uuid
from app.ingestion.readiness import READINESS_LIFETIME_SECONDS, open_page_readiness
from app.ingestion.settings import get_ingestion_settings
from app.logging import get_logger
from app.preview.ports import ServerSdkActivation, TelemetryReadiness

__all__ = ["IngestionObservationReadiness"]

_log = get_logger(__name__)

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
        """Project the session's browser, server, and correlation readiness for its open page."""
        observed_at = datetime.now(tz=UTC)
        async with self._database.connection() as conn:
            page = await open_page_readiness(
                conn,
                client_id=client_id,
                interview_session_id=interview_session_id,
                observed_at=observed_at,
            )
        ready = {kind.kind: kind.ready for kind in page.kinds}
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
            detail="; ".join(
                f"{kind.kind}: {kind.reason}" for kind in page.kinds if not kind.ready
            ),
        )


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
