"""The browser telemetry gateway.

One authenticated WebSocket per top-document runtime. It accepts telemetry
writes plus acknowledgements and flow control, and never browser commands,
detail queries, or control-plane state.

The gateway holds no durable product truth: it authenticates, checks the fixed
platform ceilings and the activation's negotiated limits, applies the
non-overridable never-collect checks in memory, commits the receipt and the
normalized records in one transaction, and only then acknowledges. A reconnect
may reach another instance and resume from the durable stream cursor.

Contract: docs/arch-components/telemetry-and-evidence.md#ingress-topology-and-ownership
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from json import dumps
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.db.pool import Database
from app.ids import uuid7
from app.ingestion.activation import (
    BrowserActivation,
    assert_scope_matches,
    load_browser_activation,
)
from app.ingestion.artifacts import ArtifactStore
from app.ingestion.browser_records import (
    advance_accepted_through,
    end_stream,
    fail_stream,
    open_stream,
    record_identity_conflict,
    store_gap,
    store_observation,
)
from app.ingestion.credentials import resolve_credential
from app.ingestion.errors import (
    BackpressureError,
    IdentityReuseError,
    IngestionError,
    IntegrityViolationError,
    TelemetryAuthenticationError,
)
from app.ingestion.frames import (
    AckFrame,
    ErrorFrame,
    FlowControlFrame,
    ObservationBatchFrame,
    ServerFrame,
    StreamAcceptedFrame,
    StreamEndFrame,
    StreamOpenFrame,
    decode_client_frame,
)
from app.ingestion.journal import (
    ReceiptDraft,
    ReceiptOutcome,
    advance_stage,
    find_receipt,
    record_failure,
    write_receipt,
)
from app.ingestion.limits import (
    BASELINE_ARTIFACT_THRESHOLD_BYTES,
    BROWSER_MAX_BATCH_BYTES,
    BROWSER_MAX_OBSERVATION_BYTES,
    require_within,
)
from app.ingestion.never_collect import assert_no_forbidden_material
from app.ingestion.normalize import (
    CANONICAL_SCHEMA_VERSION,
    CanonicalObservation,
    canonical_observation,
    observation_gap,
)
from app.ingestion.rate_budget import RateBudget
from app.ingestion.scheduling import schedule_correlation, schedule_readiness
from app.logging import get_logger

__all__ = ["INGEST_SUBPROTOCOL", "BrowserIngestSession", "credential_from_subprotocols"]

_log = get_logger(__name__)

INGEST_SUBPROTOCOL = "utrace.browser.v1"
_CREDENTIAL_PREFIX = "utrace.credential."

_CLOSE_POLICY_VIOLATION = 1008
_CLOSE_NORMAL = 1000

_FLOW_CONTROL_RETRY_AFTER_MS = 1_000


def credential_from_subprotocols(header: str | None) -> str:
    """Read the credential the SDK presents as a WebSocket subprotocol value.

    A browser cannot set request headers on a WebSocket, so the credential
    travels as a subprotocol value, which Chrome sends in
    ``Sec-WebSocket-Protocol``. The URL is not an allowed carrier.
    """
    if header is None:
        raise TelemetryAuthenticationError(
            "the telemetry WebSocket presented no subprotocol credential"
        )
    for candidate in (value.strip() for value in header.split(",")):
        if candidate.startswith(_CREDENTIAL_PREFIX):
            token = candidate[len(_CREDENTIAL_PREFIX) :]
            if len(token) == 0:
                raise TelemetryAuthenticationError(
                    "the credential subprotocol value carries no token"
                )
            return token
    raise TelemetryAuthenticationError(
        "no 'utrace.credential.<token>' subprotocol value was offered"
    )


@dataclass(slots=True)
class _StreamCursor:
    """Everything the connection tracks about its one open stream."""

    browser_stream_id: UUID
    accepted_through: int
    highest_received: int


class BrowserIngestSession:
    """One browser telemetry connection, from ``stream.open`` to close.

    The session is connection-affine while connected but holds no authority: the
    durable stream row is the cursor, so a reconnect on another instance resumes
    from exactly the same acknowledgement.
    """

    def __init__(
        self, websocket: WebSocket, *, database: Database, artifacts: ArtifactStore
    ) -> None:
        self._websocket = websocket
        self._database = database
        self._artifacts = artifacts
        self._activation: BrowserActivation | None = None
        self._credential_id: UUID | None = None
        self._cursor: _StreamCursor | None = None
        self._budget: RateBudget | None = None

    async def run(self) -> None:
        """Serve one connection until it closes or fails."""
        try:
            token = credential_from_subprotocols(
                self._websocket.headers.get("sec-websocket-protocol")
            )
        except IngestionError as error:
            await self._websocket.accept(subprotocol=INGEST_SUBPROTOCOL)
            await self._refuse(error, batch_id=None)
            return
        await self._websocket.accept(subprotocol=INGEST_SUBPROTOCOL)
        try:
            resolved = await resolve_credential(
                self._database, token=token, expected_kind="browser"
            )
        except IngestionError as error:
            await self._refuse(error, batch_id=None)
            return
        self._credential_id = resolved.telemetry_credential_id
        activation = await load_browser_activation(
            self._database, activation_id=resolved.activation_id
        )
        self._activation = activation
        self._budget = RateBudget(
            max_bytes_per_second=activation.limits.max_bytes_per_second,
            max_records_per_second=activation.limits.max_records_per_second,
        )
        try:
            await self._serve()
        except WebSocketDisconnect:
            _log.info(
                "browser_stream_disconnected",
                activation_id=str(activation.activation_id),
                browser_stream_id=(
                    str(self._cursor.browser_stream_id)
                    if self._cursor is not None
                    else None
                ),
            )

    async def _serve(self) -> None:
        """Read and dispatch client frames until the stream ends or fails."""
        while True:
            text = await self._websocket.receive_text()
            try:
                frame = decode_client_frame(text)
            except IngestionError as error:
                await self._refuse(error, batch_id=None)
                return
            batch_id = frame.batch_id if isinstance(frame, ObservationBatchFrame) else None
            try:
                match frame:
                    case StreamOpenFrame():
                        await self._handle_open(frame)
                    case ObservationBatchFrame():
                        await self._handle_batch(frame, text)
                    case StreamEndFrame():
                        await self._handle_end(frame)
                        return
            except BackpressureError as error:
                await self._pause(error)
            except IdentityReuseError as error:
                await self._fail_on_identity_reuse(error)
                await self._refuse(error, batch_id=batch_id)
                return
            except IngestionError as error:
                await self._refuse(error, batch_id=batch_id)
                return

    def _require_activation(self) -> BrowserActivation:
        """Return the authenticated activation, or refuse."""
        if self._activation is None:
            raise TelemetryAuthenticationError("the stream was never authenticated")
        return self._activation

    def _require_cursor(self) -> _StreamCursor:
        """Return the open stream cursor, or refuse a frame that arrived too early."""
        if self._cursor is None:
            raise IntegrityViolationError(
                identity_kind="frame",
                identity_value="observation.batch",
                reason="arrived before the stream was opened",
            )
        return self._cursor

    def _require_budget(self) -> RateBudget:
        """Return this connection's rate budget, or refuse."""
        if self._budget is None:
            raise TelemetryAuthenticationError("the stream was never authenticated")
        return self._budget

    def _require_credential(self) -> UUID:
        """Return the credential this connection authenticated with, or refuse."""
        if self._credential_id is None:
            raise TelemetryAuthenticationError("the stream was never authenticated")
        return self._credential_id

    async def _handle_open(self, frame: StreamOpenFrame) -> None:
        """Authenticate the claimed scope and resume from the durable cursor."""
        activation = self._require_activation()
        assert_scope_matches(activation, frame.scope)
        if frame.scope.browser_stream_id != frame.browser_stream_id:
            raise IntegrityViolationError(
                identity_kind="browser_stream_id",
                identity_value=str(frame.browser_stream_id),
                reason="disagrees with the browser_stream_id in the stream scope",
            )
        if frame.document_attachment_id != activation.document_attachment_id:
            raise IntegrityViolationError(
                identity_kind="document_attachment_id",
                identity_value=str(frame.document_attachment_id),
                reason="is not the document this activation attested",
            )
        async with self._database.transaction() as conn:
            state = await open_stream(
                conn,
                activation=activation,
                browser_stream_id=frame.browser_stream_id,
                runtime_id=frame.runtime_id,
                document_attachment_id=frame.document_attachment_id,
            )
            await schedule_readiness(
                conn,
                client_id=activation.client_id,
                activation_id=activation.activation_id,
                interview_session_id=activation.interview_session_id,
                trigger="browser_stream_opened",
            )
        if frame.resume_from_export_sequence > state.accepted_through_export_sequence:
            raise IntegrityViolationError(
                identity_kind="resume_from_export_sequence",
                identity_value=str(frame.resume_from_export_sequence),
                reason=(
                    "claims an acknowledgement beyond the durable watermark of "
                    f"{state.accepted_through_export_sequence}"
                ),
            )
        self._cursor = _StreamCursor(
            browser_stream_id=frame.browser_stream_id,
            accepted_through=state.accepted_through_export_sequence,
            highest_received=state.highest_received_export_sequence,
        )
        await self._send(
            StreamAcceptedFrame(
                browser_stream_id=frame.browser_stream_id,
                accepted_through_export_sequence=state.accepted_through_export_sequence,
            )
        )

    async def _handle_batch(self, frame: ObservationBatchFrame, text: str) -> None:
        """Check, commit, and only then acknowledge one batch."""
        activation = self._require_activation()
        cursor = self._require_cursor()
        if frame.browser_stream_id != cursor.browser_stream_id:
            raise IntegrityViolationError(
                identity_kind="browser_stream_id",
                identity_value=str(frame.browser_stream_id),
                reason="is not the stream this connection opened",
            )

        raw = text.encode("utf-8")
        received_at = datetime.now(tz=UTC)
        records = self._check_batch(activation, frame, raw)
        self._require_budget().consume(
            byte_count=len(raw),
            record_count=len(records),
            now=received_at,
            retry_after_ms=_FLOW_CONTROL_RETRY_AFTER_MS,
        )

        content_sha256 = sha256(raw).hexdigest()
        async with self._database.transaction() as conn:
            existing = await find_receipt(
                conn,
                client_id=activation.client_id,
                source_stream_id=frame.browser_stream_id,
                batch_id=frame.batch_id,
            )
            if existing is None:
                accepted_through = await self._commit_batch(
                    conn,
                    activation=activation,
                    frame=frame,
                    records=records,
                    raw=raw,
                    content_sha256=content_sha256,
                    received_at=received_at,
                )
            else:
                accepted_through = await self._replay(
                    conn, existing=existing, frame=frame, content_sha256=content_sha256
                )
        cursor.accepted_through = accepted_through
        cursor.highest_received = max(cursor.highest_received, frame.last_export_sequence)
        await self._send(
            AckFrame(
                browser_stream_id=frame.browser_stream_id,
                accepted_through_export_sequence=accepted_through,
            )
        )

    def _check_batch(
        self, activation: BrowserActivation, frame: ObservationBatchFrame, raw: bytes
    ) -> list[CanonicalObservation]:
        """Apply the payload limits and the platform exclusions before any write."""
        require_within(
            "batch_bytes",
            len(raw),
            min(activation.limits.max_batch_bytes, BROWSER_MAX_BATCH_BYTES),
        )
        received_at = datetime.now(tz=UTC)
        records: list[CanonicalObservation] = []
        for envelope in frame.observations:
            require_within(
                "observation_bytes",
                len(envelope.model_dump_json().encode("utf-8")),
                min(activation.limits.max_observation_bytes, BROWSER_MAX_OBSERVATION_BYTES),
            )
            assert_no_forbidden_material(
                envelope.payload, f"{envelope.observation_type}.payload", 0
            )
            records.append(canonical_observation(envelope, received_at=received_at))
        return records

    async def _replay(
        self,
        conn: AsyncConnection[DictRow],
        *,
        existing: ReceiptOutcome,
        frame: ObservationBatchFrame,
        content_sha256: str,
    ) -> int:
        """Acknowledge an exact retry without creating new evidence."""
        if existing.content_sha256 != content_sha256:
            raise IdentityReuseError(
                identity_kind="batch_id",
                identity_value=str(frame.batch_id),
                first_content_sha256=existing.content_sha256,
                conflicting_content_sha256=content_sha256,
                first_ingestion_receipt_id=existing.ingestion_receipt_id,
            )
        _log.info(
            "browser_batch_replayed",
            browser_stream_id=str(frame.browser_stream_id),
            batch_id=str(frame.batch_id),
            ingestion_receipt_id=str(existing.ingestion_receipt_id),
        )
        return await advance_accepted_through(
            conn,
            browser_stream_id=frame.browser_stream_id,
            highest_received=frame.last_export_sequence,
        )

    async def _commit_batch(
        self,
        conn: AsyncConnection[DictRow],
        *,
        activation: BrowserActivation,
        frame: ObservationBatchFrame,
        records: list[CanonicalObservation],
        raw: bytes,
        content_sha256: str,
        received_at: datetime,
    ) -> int:
        """Write the raw batch, the receipt, the canonical records, and the watermark."""
        artifact = await self._artifacts.put(
            conn,
            client_id=activation.client_id,
            artifact_class="browser_raw_batch",
            media_type="application/json",
            schema_type="browser-observation/ingest-client-frame.v1",
            retention_class="telemetry_source",
            body=raw,
            manifest=None,
        )
        receipt = await write_receipt(
            conn,
            ReceiptDraft(
                client_id=activation.client_id,
                activation_id=activation.activation_id,
                producer_kind="browser",
                telemetry_credential_id=self._require_credential(),
                source_stream_id=frame.browser_stream_id,
                batch_id=frame.batch_id,
                batch_sequence=None,
                first_export_sequence=frame.first_export_sequence,
                last_export_sequence=frame.last_export_sequence,
                byte_count=len(raw),
                record_count=len(records),
                content_sha256=content_sha256,
                source_schema_version=activation.source_schema_version,
                canonical_schema_version=CANONICAL_SCHEMA_VERSION,
                policy_version=activation.policy_version,
                raw_artifact_id=artifact.artifact_id,
            ),
            stage="received",
            outcome="accepted",
        )
        for stage in ("authenticated", "source_validated"):
            await advance_stage(
                conn,
                client_id=activation.client_id,
                ingestion_receipt_id=receipt.ingestion_receipt_id,
                stage=stage,
                normalization_run_id=None,
            )
        normalization_run_id = uuid7()
        stored = 0
        for record in records:
            if await store_observation(
                conn,
                activation=activation,
                browser_stream_id=frame.browser_stream_id,
                ingestion_receipt_id=receipt.ingestion_receipt_id,
                normalization_run_id=normalization_run_id,
                received_at=received_at,
                record=record,
            ):
                stored += 1
            if record.observation_type == "observation.gap":
                await store_gap(
                    conn,
                    client_id=activation.client_id,
                    browser_stream_id=frame.browser_stream_id,
                    ingestion_receipt_id=receipt.ingestion_receipt_id,
                    gap=observation_gap(record.payload),
                )
            await self._store_large_baseline(conn, activation=activation, record=record)
        for stage in ("normalized", "indexed"):
            await advance_stage(
                conn,
                client_id=activation.client_id,
                ingestion_receipt_id=receipt.ingestion_receipt_id,
                stage=stage,
                normalization_run_id=normalization_run_id,
            )
        await self._schedule_derivation(conn, activation=activation, records=records)
        _log.info(
            "browser_batch_committed",
            client_id=str(activation.client_id),
            browser_stream_id=str(frame.browser_stream_id),
            batch_id=str(frame.batch_id),
            stored=stored,
            duplicates=len(records) - stored,
        )
        return await advance_accepted_through(
            conn,
            browser_stream_id=frame.browser_stream_id,
            highest_received=frame.last_export_sequence,
        )

    async def _schedule_derivation(
        self,
        conn: AsyncConnection[DictRow],
        *,
        activation: BrowserActivation,
        records: list[CanonicalObservation],
    ) -> None:
        """Schedule the correlation and readiness this batch owes.

        A completed browser request is the half a server ``SERVER`` span joins
        onto, so it is the observation that schedules correlation. A reported
        delivery gap changes what readiness may claim, so it schedules a fresh
        evaluation rather than waiting for the expiry scan.
        """
        await schedule_correlation(
            conn,
            client_id=activation.client_id,
            activation_id=activation.activation_id,
            trace_ids=(
                record.trace_id
                for record in records
                if record.observation_type == "request.completed"
                and record.trace_id is not None
            ),
        )
        if any(record.observation_type == "observation.gap" for record in records):
            await schedule_readiness(
                conn,
                client_id=activation.client_id,
                activation_id=activation.activation_id,
                interview_session_id=activation.interview_session_id,
                trigger="browser_delivery_gap",
            )

    async def _store_large_baseline(
        self,
        conn: AsyncConnection[DictRow],
        *,
        activation: BrowserActivation,
        record: CanonicalObservation,
    ) -> None:
        """Move an oversized baseline body into object storage as an artifact."""
        if not record.observation_type.startswith("baseline."):
            return
        body = dumps(dict(record.payload), separators=(",", ":")).encode("utf-8")
        if len(body) <= BASELINE_ARTIFACT_THRESHOLD_BYTES:
            return
        await self._artifacts.put(
            conn,
            client_id=activation.client_id,
            artifact_class="browser_baseline",
            media_type="application/json",
            schema_type=f"browser-observation/payload-{record.observation_type}.v1",
            retention_class="telemetry_source",
            body=body,
            manifest=None,
        )

    async def _handle_end(self, frame: StreamEndFrame) -> None:
        """Record the terminal sequence. ``paused`` keeps the stream resumable."""
        cursor = self._require_cursor()
        if frame.browser_stream_id != cursor.browser_stream_id:
            raise IntegrityViolationError(
                identity_kind="browser_stream_id",
                identity_value=str(frame.browser_stream_id),
                reason="is not the stream this connection opened",
            )
        activation = self._require_activation()
        async with self._database.transaction() as conn:
            await end_stream(
                conn,
                browser_stream_id=frame.browser_stream_id,
                terminal_export_sequence=frame.terminal_export_sequence,
                reason=frame.reason,
            )
            await schedule_readiness(
                conn,
                client_id=activation.client_id,
                activation_id=activation.activation_id,
                interview_session_id=activation.interview_session_id,
                trigger=f"browser_stream_ended:{frame.reason}",
            )
        await self._websocket.close(code=_CLOSE_NORMAL, reason=frame.reason)

    async def _pause(self, error: BackpressureError) -> None:
        """Apply explicit backpressure without advancing the acknowledgement."""
        _log.info(
            "browser_ingest_flow_control",
            limit_name=error.limit_name,
            retry_after_ms=error.retry_after_ms,
            browser_stream_id=(
                str(self._cursor.browser_stream_id) if self._cursor is not None else None
            ),
        )
        await self._send(
            FlowControlFrame(state="pause", retry_after_ms=error.retry_after_ms)
        )

    async def _fail_on_identity_reuse(self, error: IdentityReuseError) -> None:
        """Keep both versions as diagnostics and fail the source stream."""
        activation = self._require_activation()
        cursor = self._cursor
        if cursor is None:
            return
        async with self._database.transaction() as conn:
            await record_identity_conflict(
                conn,
                client_id=activation.client_id,
                activation_id=activation.activation_id,
                producer_kind="browser",
                source_stream_id=cursor.browser_stream_id,
                error=error,
            )
            await fail_stream(
                conn,
                browser_stream_id=cursor.browser_stream_id,
                reason=error.code,
            )

    async def _send(self, frame: ServerFrame) -> None:
        """Send one server frame as text."""
        await self._websocket.send_text(frame.model_dump_json(exclude_none=True))

    async def _refuse(self, error: IngestionError, *, batch_id: UUID | None) -> None:
        """Record the typed failure, report it, and close.

        The error frame carries the stable code and a safe message. It never
        carries the rejected product value.
        """
        activation = self._activation
        if activation is not None:
            await record_failure(
                self._database,
                client_id=activation.client_id,
                activation_id=activation.activation_id,
                ingestion_receipt_id=None,
                producer_kind="browser",
                source_stream_id=(
                    self._cursor.browser_stream_id if self._cursor is not None else None
                ),
                error=error,
                readiness_consequence="withdraw_readiness",
                affected_watermark=(
                    self._cursor.accepted_through if self._cursor is not None else None
                ),
                affected_trace_id=None,
                detail={"batch_id": str(batch_id) if batch_id is not None else None},
            )
        await self._send(
            ErrorFrame(
                code=error.code,
                message=error.reason,
                retryable=error.retry_classification != "never",
                batch_id=batch_id,
            )
        )
        await self._websocket.close(code=_CLOSE_POLICY_VIOLATION, reason=error.code)
