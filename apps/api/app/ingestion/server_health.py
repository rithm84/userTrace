"""Durable records from the Server SDK control channel.

Health reports, control commands, their acknowledgements, and the terminal
stream report are the operational facts a control socket produces. They carry no
product content, so the whole record is safe to keep, and keeping it is what lets
readiness be evaluated from durable state after the socket is gone.

Contract: docs/arch-components/server-sdk.md#readiness-health-and-failure
"""

from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from app.db.pool import Database
from app.db.rows import as_uuid
from app.ids import uuid7
from app.ingestion.control_protocol import (
    ServerSdkFailure,
    ServerSdkHealthReport,
    StreamReportMessage,
)
from app.ingestion.errors import IngestionError, IntegrityViolationError
from app.ingestion.journal import record_failure
from app.ingestion.server_activation import ServerActivation
from app.logging import get_logger

__all__ = [
    "SdkReportedFailure",
    "acknowledge_command",
    "mark_command_delivered",
    "record_control_command",
    "record_health_report",
    "record_stream_report",
    "report_stream_failures",
]

_log = get_logger(__name__)


async def record_health_report(
    conn: AsyncConnection[DictRow],
    *,
    activation: ServerActivation,
    report: ServerSdkHealthReport,
) -> UUID:
    """Persist one health report against its activation and stream.

    A report naming another stream or another activation is refused: a health
    report is evidence about one service instance, and accepting a foreign one
    would let a live stream be described by a process that is not it.
    """
    if report.server_stream_id != activation.server_stream_id:
        raise IntegrityViolationError(
            identity_kind="server_stream_id",
            identity_value=str(report.server_stream_id),
            reason="is not the stream this control connection activated",
        )
    if report.activation_id is not None and report.activation_id != activation.activation_id:
        raise IntegrityViolationError(
            identity_kind="activation_id",
            identity_value=str(report.activation_id),
            reason="is not the activation this control connection holds",
        )
    if report.service_instance_id != activation.service_instance_id:
        raise IntegrityViolationError(
            identity_kind="service_instance_id",
            identity_value=str(report.service_instance_id),
            reason="is not the service instance this activation bound",
        )
    report_id = uuid7()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.server_health_reports (
                id, client_id, activation_id, server_stream_id, reported_at,
                lifecycle_state, sdk_version, contract_major, node_version,
                service_key, service_instance_id, manifest_version, policy_version,
                utrace_schema_version, semantic_convention_version,
                credential_expires_at, capabilities, adapters, buffer, probe_result,
                failures
            )
            values (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s
            )
            """,
            (
                report_id,
                activation.client_id,
                activation.activation_id,
                activation.server_stream_id,
                report.reported_at,
                report.lifecycle_state,
                report.sdk_version,
                report.contract_major,
                report.node_version,
                report.service_key,
                report.service_instance_id,
                report.manifest_version,
                report.policy_version,
                report.utrace_schema_version,
                report.semantic_convention_version,
                report.credential_expires_at,
                Jsonb(list(report.capabilities)),
                Jsonb([status.model_dump(mode="json") for status in report.adapters]),
                Jsonb(report.buffer.model_dump(mode="json")),
                Jsonb(report.probe_result.model_dump(mode="json")),
                Jsonb(
                    [failure.model_dump(mode="json") for failure in report.failures]
                ),
            ),
        )
    _log.info(
        "server_health_report_recorded",
        client_id=str(activation.client_id),
        activation_id=str(activation.activation_id),
        server_stream_id=str(activation.server_stream_id),
        lifecycle_state=report.lifecycle_state,
        probe_state=report.probe_result.state,
        failure_count=len(report.failures),
    )
    return report_id


async def record_stream_report(
    conn: AsyncConnection[DictRow],
    *,
    activation: ServerActivation,
    report: StreamReportMessage,
) -> None:
    """Close the stream with the terminal report the SDK sent.

    An unacknowledged tail is recorded as a possible evidence gap: the stream
    ends ``failed`` rather than ``ended``, so nothing downstream can read it as a
    complete trace.
    """
    if report.server_stream_id != activation.server_stream_id:
        raise IntegrityViolationError(
            identity_kind="server_stream_id",
            identity_value=str(report.server_stream_id),
            reason="is not the stream this control connection activated",
        )
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.server_streams
            set state = %s, clean = %s, revision = revision + 1, updated_at = now()
            where id = %s
            """,
            (
                "ended" if report.clean else "failed",
                report.clean,
                activation.server_stream_id,
            ),
        )
    _log.info(
        "server_stream_report_recorded",
        client_id=str(activation.client_id),
        server_stream_id=str(activation.server_stream_id),
        clean=report.clean,
        last_produced_sequence=report.last_produced_sequence,
        last_acknowledged_sequence=report.last_acknowledged_sequence,
        failure_count=len(report.failures),
    )


async def record_control_command(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    activation_id: UUID,
    server_stream_id: UUID,
    kind: str,
    reason: str,
) -> UUID:
    """Record one control command before it is delivered over the socket."""
    command_id = uuid7()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.server_control_commands (
                id, client_id, activation_id, server_stream_id, kind, reason
            )
            values (%s, %s, %s, %s, %s, %s)
            """,
            (command_id, client_id, activation_id, server_stream_id, kind, reason),
        )
    return command_id


async def acknowledge_command(
    database: Database, *, command_id: UUID, lifecycle_state: str, activation_id: UUID
) -> None:
    """Record the lifecycle state one command reached on the SDK.

    The acknowledgement is proof the SDK applied the command; the command row
    alone is not. A command belonging to another activation is refused.
    """
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select activation_id from public.server_control_commands
            where id = %s for update
            """,
            (command_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise IntegrityViolationError(
                identity_kind="command_id",
                identity_value=str(command_id),
                reason="names no control command this control plane issued",
            )
        if as_uuid(row, "activation_id") != activation_id:
            raise IntegrityViolationError(
                identity_kind="command_id",
                identity_value=str(command_id),
                reason="belongs to another observation activation",
            )
        await cur.execute(
            """
            update public.server_control_commands
            set acknowledged_at = now(), acknowledged_lifecycle_state = %s
            where id = %s
            """,
            (lifecycle_state, command_id),
        )
    _log.info(
        "server_control_command_acknowledged",
        command_id=str(command_id),
        activation_id=str(activation_id),
        lifecycle_state=lifecycle_state,
    )


async def mark_command_delivered(
    database: Database, *, command_id: UUID
) -> None:
    """Record that one command actually crossed the control socket."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.server_control_commands
            set delivered_at = now() where id = %s and delivered_at is null
            """,
            (command_id,),
        )


async def report_stream_failures(
    database: Database,
    *,
    activation: ServerActivation,
    failures: tuple[ServerSdkFailure, ...],
    readiness_consequence: str,
) -> None:
    """Record the SDK's reported failures as typed ingestion control facts.

    They keep the SDK's own stable code and retry classification. A failure
    record carries the safe identities and the redacted diagnostic fields, never
    the rejected product value, so it is the same shape as a failure ingestion
    detected itself.
    """
    for failure in failures:
        await record_failure(
            database,
            client_id=activation.client_id,
            activation_id=activation.activation_id,
            ingestion_receipt_id=None,
            producer_kind="server",
            source_stream_id=activation.server_stream_id,
            error=SdkReportedFailure(
                code=failure.code, retry_classification=failure.retry
            ),
            readiness_consequence=readiness_consequence,
            affected_watermark=failure.batch_sequence,
            affected_trace_id=failure.trace_id,
            detail={str(key): value for key, value in failure.detail.items()},
        )


class SdkReportedFailure(IngestionError):
    """One failure the Server SDK reported, shaped for the failure record.

    It is never raised. The journal's failure writer reads ``stage``, ``code``,
    and ``retry_classification`` off the error it is handed, and an SDK-reported
    failure carries the SDK's own vocabulary through unchanged rather than being
    remapped onto an ingestion code that would describe a different event.
    """

    stage = "authenticated"

    def __init__(self, *, code: str, retry_classification: str) -> None:
        super().__init__(f"the Server SDK reported {code}")
        self.code = code
        self.retry_classification = retry_classification
