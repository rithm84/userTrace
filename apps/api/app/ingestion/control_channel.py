"""The authenticated Server SDK control connection.

One WebSocket per service process carries activation, credential rotation,
health, pause, resume, and termination. It never carries product telemetry:
spans go to the OTLP ingestion edge only.

The activation outlives the socket. A dropped control connection reconnects with
the same bootstrap credential and rotates its credential; ``activate`` is the
one-time exchange, and the unique index on ``server_stream_id`` is what makes it
one-time. A frame naming a stream that belongs to another allocation, or a stream
whose activation is no longer active, is refused rather than served.

Contracts: docs/arch-components/server-sdk.md#configuration-identity-and-credentials
           docs/arch-components/server-sdk.md#readiness-health-and-failure
"""

from datetime import UTC, datetime
from hmac import compare_digest
from json import JSONDecodeError, loads
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import ValidationError

from app.db.pool import Database
from app.db.rows import as_uuid
from app.errors import AggregateNotFoundError
from app.ingestion.bootstrap import (
    ServerBootstrapScope,
    verify_server_bootstrap_credential,
)
from app.ingestion.control_hub import ServerControlHub
from app.ingestion.control_protocol import (
    ActivatedMessage,
    ActivateMessage,
    ClientMessage,
    CommandAckMessage,
    ControlErrorMessage,
    HealthAckMessage,
    HealthMessage,
    IngestEndpoint,
    PropagationProbeSpec,
    ResourceIdentity,
    RotatedMessage,
    RotateMessage,
    ServerActivationRecord,
    ServerMessage,
    ServerSdkFailure,
    ServiceCredential,
    StreamReportMessage,
    decode_client_message,
)
from app.ingestion.errors import (
    IngestionError,
    IntegrityViolationError,
    SourceValidationError,
    TelemetryAuthenticationError,
)
from app.ingestion.manifests import ManifestService, load_signed_server_manifest
from app.ingestion.scheduling import schedule_readiness
from app.ingestion.server_activation import (
    ServerActivation,
    ServerActivationRequest,
    create_server_activation,
    load_server_activation,
    rotate_server_credential,
)
from app.ingestion.server_health import (
    acknowledge_command,
    record_health_report,
    record_stream_report,
    report_stream_failures,
)
from app.ingestion.settings import get_ingestion_settings
from app.logging import get_logger

__all__ = ["ServerControlSession"]

_log = get_logger(__name__)

_BEARER = "bearer"
_CLOSE_CREDENTIAL_REJECTED = 4401

#: Which probe hop each manifest adapter has to produce beneath the server span.
_HOP_BY_ADAPTER = {
    "node_http": "server",
    "express": "server",
    "fastify": "server",
    "next_server": "server",
    "undici": "downstream_http",
    "pg": "database",
}

#: Failure codes the SDK matches on, chosen per refusal rather than generalized.
#: ``duplicate_conflict`` is what an integrity violation raises, and to the SDK
#: a frame claiming a scope this connection does not hold is an invalid
#: activation.
_CODE_BY_ERROR = {
    "credential_rejected": "credential_rejected",
    "schema_incompatible": "incompatible_runtime_or_contract",
    "duplicate_conflict": "invalid_activation",
}


def _bearer_token(authorization: str | None) -> str:
    """Read the bootstrap credential from the handshake's Authorization header."""
    if authorization is None:
        raise TelemetryAuthenticationError("the Authorization header is missing")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != _BEARER or len(token) == 0:
        raise TelemetryAuthenticationError("expected a Bearer bootstrap credential")
    return token


def _rfc3339(moment: datetime) -> str:
    """Render one instant the way the wire contract does."""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _request_id_of(message: ClientMessage) -> UUID | None:
    """Return the request one frame expects a reply to, when it names one.

    A ``command_ack`` answers a command rather than asking a question, so a
    refusal of it correlates to no request and is reported unsolicited.
    """
    match message:
        case CommandAckMessage():
            return None
        case _:
            return message.request_id


def _required_hops(service: ManifestService) -> tuple[str, ...]:
    """Derive the probe's required hops from the service's required adapters.

    The probe has to prove browser ``CLIENT`` to server ``SERVER`` parentage plus
    every client-declared hop the smoke workflow needs. The manifest states which
    adapters that workflow requires, so the hop set follows from it rather than
    from a second, separately maintained list that could disagree with it.
    """
    hops = {
        _HOP_BY_ADAPTER[adapter]
        for adapter in service.required_adapters
        if adapter in _HOP_BY_ADAPTER
    }
    if "server" not in hops:
        raise SourceValidationError(
            field="server_instrumentation_manifest.services.required_adapters",
            reason=(
                "declares no adapter that produces the authoritative server span, "
                "so no propagation probe can prove the request path"
            ),
        )
    return tuple(sorted(hops))


class ServerControlSession:
    """One control socket, from the handshake to the disconnect."""

    def __init__(
        self,
        websocket: WebSocket,
        *,
        database: Database,
        hub: ServerControlHub,
        lane_signing_key: str,
    ) -> None:
        self._websocket = websocket
        self._database = database
        self._hub = hub
        self._lane_signing_key = lane_signing_key
        self._scope: ServerBootstrapScope | None = None
        self._bootstrap_token = ""
        self._activation: ServerActivation | None = None

    async def run(self) -> None:
        """Authenticate the handshake, then serve frames until the socket ends."""
        try:
            self._bootstrap_token = _bearer_token(
                self._websocket.headers.get("authorization")
            )
            self._scope = await verify_server_bootstrap_credential(
                self._database,
                signing_key=self._lane_signing_key,
                token=self._bootstrap_token,
            )
        except TelemetryAuthenticationError as error:
            _log.warning("server_control_credential_rejected", reason=error.reason)
            await self._websocket.close(
                code=_CLOSE_CREDENTIAL_REJECTED, reason="credential_rejected"
            )
            return
        await self._websocket.accept()
        _log.info(
            "server_control_connected",
            client_id=str(self._scope.client_id),
            interview_session_id=str(self._scope.interview_session_id),
            preview_runtime_allocation_id=str(
                self._scope.preview_runtime_allocation_id
            ),
        )
        try:
            await self._serve()
        except WebSocketDisconnect:
            _log.info(
                "server_control_disconnected",
                server_stream_id=(
                    str(self._activation.server_stream_id)
                    if self._activation is not None
                    else None
                ),
            )
        finally:
            if self._activation is not None:
                self._hub.unregister(self._activation.server_stream_id, self)

    async def _serve(self) -> None:
        """Read, validate, and dispatch one frame at a time."""
        while True:
            text = await self._websocket.receive_text()
            try:
                message = decode_client_message(loads(text))
            except (JSONDecodeError, ValidationError) as error:
                await self._refuse(
                    request_id=None,
                    code="incompatible_runtime_or_contract",
                    reason="client_frame_schema_violation",
                    consequence="fail_stream",
                )
                _log.warning(
                    "server_control_frame_rejected", reason=type(error).__name__
                )
                continue
            await self._dispatch(message)

    async def _dispatch(self, message: ClientMessage) -> None:
        """Apply one validated frame, or answer with its typed refusal."""
        request_id = _request_id_of(message)
        try:
            match message:
                case ActivateMessage():
                    await self._activate(message)
                case RotateMessage():
                    await self._rotate(message)
                case HealthMessage():
                    await self._health(message)
                case CommandAckMessage():
                    await self._command_ack(message)
                case StreamReportMessage():
                    await self._stream_report(message)
        except IngestionError as error:
            await self._refuse(
                request_id=request_id,
                code=_CODE_BY_ERROR.get(error.code, "invalid_activation"),
                reason=error.code,
                consequence="fail_stream",
            )
            _log.warning(
                "server_control_request_refused",
                message_type=message.type,
                code=error.code,
                detail=error.reason,
            )
        except AggregateNotFoundError as error:
            await self._refuse(
                request_id=request_id,
                code="invalid_activation",
                reason="unknown_aggregate",
                consequence="fail_stream",
            )
            _log.warning("server_control_request_refused", detail=str(error))

    def _require_scope(self) -> ServerBootstrapScope:
        """Return the allocation this socket authenticated for."""
        if self._scope is None:
            raise TelemetryAuthenticationError("the control socket is unauthenticated")
        return self._scope

    async def _activate(self, message: ActivateMessage) -> None:
        """Exchange the bootstrap credential for one observation activation."""
        scope = self._require_scope()
        if not compare_digest(message.bootstrap_credential, self._bootstrap_token):
            raise TelemetryAuthenticationError(
                "the activate frame presents a different credential than the handshake"
            )
        async with self._database.connection() as conn:
            release_id = await _pinned_release(
                conn, preview_runtime_allocation_id=scope.preview_runtime_allocation_id
            )
            pinned = await load_signed_server_manifest(
                conn, configuration_release_id=release_id
            )
        service = pinned.service(message.service_name)
        ingestion = get_ingestion_settings()
        activation, credential, entry_token = await create_server_activation(
            self._database,
            request=ServerActivationRequest(
                preview_runtime_allocation_id=scope.preview_runtime_allocation_id,
                public_preview_host=scope.public_preview_host,
                server_stream_id=message.server_stream_id,
                service_instance_id=message.service_instance_id,
                service_key=service.service_key,
                signed_manifest=pinned.signed,
                required_hops=_required_hops(service),
            ),
            trust_anchor=ingestion.manifest_trust_anchor_key(),
            trust_anchor_key_id=ingestion.server_manifest_trust_anchor_key_id,
        )
        self._bind(activation)
        await self._send(
            ActivatedMessage(
                request_id=message.request_id,
                activation=ServerActivationRecord(
                    activation_id=activation.activation_id,
                    client_id=activation.client_id,
                    client_user_id=activation.client_user_id,
                    customer_account_id=activation.customer_account_id,
                    session_id=activation.interview_session_id,
                    preview_environment_id=activation.preview_environment_id,
                    preview_runtime_allocation_id=(
                        activation.preview_runtime_allocation_id
                    ),
                    preview_version_id=activation.preview_version_id,
                    server_stream_id=activation.server_stream_id,
                    service_instance_id=activation.service_instance_id,
                    service_key=activation.service_key,
                    resource=ResourceIdentity(
                        service_name=service.service_name,
                        service_namespace=service.service_namespace,
                        service_version=service.service_version,
                        deployment_environment_name="preview",
                    ),
                    credential=ServiceCredential(
                        token=credential.token,
                        expires_at=_rfc3339(credential.expires_at),
                    ),
                    ingest=IngestEndpoint(preview_entry_token=entry_token),
                    limits=activation.limits.model_dump(),
                    probe=PropagationProbeSpec.model_validate(
                        {
                            "probe_id": activation.probe_id,
                            "route_template": service.probe_route_template,
                            "method": service.probe_method,
                            "required_hops": activation.required_hops,
                        }
                    ),
                    signed_manifest=pinned.signed,
                ),
            )
        )

    async def _rotate(self, message: RotateMessage) -> None:
        """Issue a fresh credential without disturbing the activation."""
        activation = await self._resolve_stream(message.server_stream_id)
        credential = await rotate_server_credential(
            self._database,
            activation_id=activation.activation_id,
            server_stream_id=message.server_stream_id,
        )
        await self._send(
            RotatedMessage(
                request_id=message.request_id,
                credential=ServiceCredential(
                    token=credential.token,
                    expires_at=_rfc3339(credential.expires_at),
                ),
            )
        )

    async def _health(self, message: HealthMessage) -> None:
        """Persist one health report and let it drive readiness."""
        activation = await self._resolve_stream(message.report.server_stream_id)
        async with self._database.transaction() as conn:
            await record_health_report(
                conn, activation=activation, report=message.report
            )
            await schedule_readiness(
                conn,
                client_id=activation.client_id,
                activation_id=activation.activation_id,
                interview_session_id=activation.interview_session_id,
                trigger=f"server_health:{message.report.lifecycle_state}",
            )
        await self._send(HealthAckMessage(request_id=message.request_id))

    async def _command_ack(self, message: CommandAckMessage) -> None:
        """Record the lifecycle state one control command reached."""
        activation = self._require_activation()
        await acknowledge_command(
            self._database,
            command_id=message.command_id,
            lifecycle_state=message.lifecycle_state,
            activation_id=activation.activation_id,
        )

    async def _stream_report(self, message: StreamReportMessage) -> None:
        """Close the stream on its terminal report and re-evaluate readiness."""
        activation = await self._resolve_stream(message.server_stream_id)
        async with self._database.transaction() as conn:
            await record_stream_report(
                conn, activation=activation, report=message
            )
            await schedule_readiness(
                conn,
                client_id=activation.client_id,
                activation_id=activation.activation_id,
                interview_session_id=activation.interview_session_id,
                trigger="server_stream_report",
            )
        await report_stream_failures(
            self._database,
            activation=activation,
            failures=message.failures,
            readiness_consequence=(
                "withdraw_readiness" if message.clean else "fail_stream"
            ),
        )

    def _require_activation(self) -> ServerActivation:
        """Return the activation this socket is bound to, or refuse."""
        if self._activation is None:
            raise TelemetryAuthenticationError(
                "this control connection holds no observation activation"
            )
        return self._activation

    async def _resolve_stream(self, server_stream_id: UUID) -> ServerActivation:
        """Bind, or re-bind after a reconnect, the activation of one stream.

        A reconnect presents the same bootstrap credential and rotates against
        the stream it already created, so the activation is resolved from the
        stream rather than from connection memory. A stream belonging to another
        allocation, or to another client, is refused.
        """
        bound = self._activation
        if bound is not None:
            if bound.server_stream_id != server_stream_id:
                raise IntegrityViolationError(
                    identity_kind="server_stream_id",
                    identity_value=str(server_stream_id),
                    reason="is not the stream this control connection activated",
                )
            return bound
        scope = self._require_scope()
        activation = await _activation_for_stream(
            self._database, server_stream_id=server_stream_id
        )
        if (
            activation.client_id != scope.client_id
            or activation.preview_runtime_allocation_id
            != scope.preview_runtime_allocation_id
        ):
            raise IntegrityViolationError(
                identity_kind="server_stream_id",
                identity_value=str(server_stream_id),
                reason="belongs to another preview runtime allocation",
            )
        self._bind(activation)
        return activation

    def _bind(self, activation: ServerActivation) -> None:
        """Hold the activation and make this socket reachable for commands."""
        self._activation = activation
        self._hub.register(activation.server_stream_id, self)

    async def _send(self, message: ServerMessage) -> None:
        """Send one server frame."""
        await self._websocket.send_text(message.model_dump_json(exclude_none=True))

    async def deliver(self, message: ServerMessage) -> None:
        """Send one control command over this socket, from the command route."""
        await self._send(message)

    async def _refuse(
        self, *, request_id: UUID | None, code: str, reason: str, consequence: str
    ) -> None:
        """Answer one frame with a typed failure that carries no product value."""
        failure = ServerSdkFailure.model_validate(
            {
                "code": code,
                "detected_at": datetime.now(tz=UTC),
                "retry": "never",
                "readiness_consequence": consequence,
                "detail": {"reason": reason},
            }
        )
        await self._send(
            ControlErrorMessage(request_id=request_id, failure=failure)
        )


async def _pinned_release(
    conn: AsyncConnection[DictRow], *, preview_runtime_allocation_id: UUID
) -> UUID:
    """Return the configuration release the allocation's Product Signal pinned."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select signals.pinned_configuration_release_id
            from public.preview_runtime_allocations as allocations
            join public.product_signals as signals
              on signals.id = allocations.product_signal_id
            where allocations.id = %s
            """,
            (preview_runtime_allocation_id,),
        )
        row = await cur.fetchone()
    if row is None or row["pinned_configuration_release_id"] is None:
        raise SourceValidationError(
            field="product_signals.pinned_configuration_release_id",
            reason=(
                "the allocation's Product Signal pins no configuration release, so "
                "no server instrumentation manifest applies to it"
            ),
        )
    return as_uuid(row, "pinned_configuration_release_id")


async def _activation_for_stream(
    database: Database, *, server_stream_id: UUID
) -> ServerActivation:
    """Load the active server activation that owns one stream."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.telemetry_activations
            where kind = 'server' and server_stream_id = %s and state = 'active'
            """,
            (server_stream_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise IntegrityViolationError(
            identity_kind="server_stream_id",
            identity_value=str(server_stream_id),
            reason="names no active observation activation",
        )
    return await load_server_activation(database, activation_id=as_uuid(row, "id"))
