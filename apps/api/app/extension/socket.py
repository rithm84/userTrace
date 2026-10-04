"""The single authenticated control connection the extension owns.

Authentication is a proof of possession of the installation's device key,
carried in the WebSocket subprotocol because a browser WebSocket cannot set
headers. The control plane enforces freshness and single use before accepting
the connection.

The socket carries binding, readiness, character state, controls, commands,
results, cancellation, and recovery. High-volume product telemetry never travels
here: it belongs to the shared browser observation runtime's own export path.

Every connection opens with `recovery_request`. Resumption continues from the
last acknowledged control sequence, and the frames above that point are
superseded rather than replayed: recovery re-projects the current control-plane
state, because assuming missed delivery succeeded is exactly what the contract
forbids. A sequence the extension could not have reached closes the socket.

State the extension renders changes in every control-plane process, not only in
answer to this socket's frames. The connection therefore also listens on
`app.extension.delivery.CONTROL_CHANNEL`: a committed change to its session's
binding, readiness, character state, or visual commands wakes it, and it
re-projects whatever advanced since it last sent it and routes the durable
pending commands. Every state frame goes out through that one projection, so a
revision the extension already holds is never sent twice on one connection.
"""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.auth.principals import ParticipantPrincipal
from app.db.pool import Database
from app.db.rows import as_int, as_str, as_uuid
from app.errors import AggregateNotFoundError, AuthorizationError, ControlPlaneError
from app.extension import frames
from app.extension.bindings import BoundDocument, PageProofVerifier, apply_binding_request
from app.extension.commands import (
    cancel_pending_visual_commands,
    record_visual_command_result,
    settle_unknown_outcomes,
)
from app.extension.delivery import (
    CONTROL_CHANNEL,
    claim_routable_commands,
    latest_binding,
    routed_outcomes,
)
from app.extension.installations import consume_possession_proof, load_installation
from app.extension.preferences import load_preferences, update_preferences
from app.extension.proofs import PossessionError, decode_subprotocol_proof, verify_possession
from app.extension.protocol import (
    BindingOfferFrame,
    CommandResultFrame,
    ControlAckFrame,
    ControlActionFrame,
    HeartbeatFrame,
    OutboundFrame,
    PreferencesUpdateFrame,
    ProtocolError,
    ReadinessReportFrame,
    RecoveryRequestFrame,
    parse_outbound_frame,
)
from app.extension.stream import ControlStream
from app.ingestion.control_hub import ServerControlHub
from app.interview.character import load_character
from app.interview.controls import apply_participant_control
from app.interview.readiness import (
    ExtensionReadiness,
    load_readiness,
    record_observation_facts,
    refresh_observation_readiness,
    withdraw_observation_readiness,
)
from app.lifecycle.session_controls import ControlAction
from app.logging import get_logger
from app.preview.ports import ObservationReadinessPort

__all__ = ["ExtensionControlSocket", "SocketDependencies"]

_log = get_logger(__name__)

_ACCEPTED_SUBPROTOCOL = "utrace.extension.v1"
_INSTALLATION_PREFIX = "utrace.installation."

_CLOSE_NORMAL = 1000
_CLOSE_POLICY_VIOLATION = 1008
_CLOSE_SEQUENCE_GAP = 4002
_CLOSE_INSTALLATION_NOT_LINKED = 4003

_LINKED = "linked"
_CANCELED = "canceled"

#: The state frames whose revisions belong to one Interview Session.
_SESSION_FRAMES: tuple[str, ...] = ("binding_state", "readiness", "character_state")

_CONTROL_BY_ACTION: Mapping[str, ControlAction] = {
    "mute": ControlAction.MUTE,
    "unmute": ControlAction.UNMUTE,
    "pause": ControlAction.PAUSE,
    "resume": ControlAction.RESUME,
    "end": ControlAction.END_CONFIRM,
}


class _InstallationNotLinkedError(AuthorizationError):
    """The control plane holds no linked installation under the identity presented.

    It is unknown, as after a reset, or revoked. This is the one refusal the
    extension acts on: it drops its local installation so the browser can be
    linked again.
    """

    def __init__(
        self,
        installation_id: UUID,
        reason: Literal["installation_unknown", "installation_revoked"],
    ) -> None:
        super().__init__(subject=f"extension installation {installation_id}", reason=reason)
        self.installation_id = installation_id


@dataclass(frozen=True, slots=True)
class SocketDependencies:
    """Everything the socket needs, supplied explicitly by the route."""

    database: Database
    verifier: PageProofVerifier
    observation: ObservationReadinessPort | None
    control_hub: ServerControlHub
    supported_protocol_versions: tuple[str, ...]
    socket_audience: str
    possession_max_age_seconds: int
    return_window_seconds: int
    protocol_version: str


class ExtensionControlSocket:
    """One accepted control connection for one installation."""

    def __init__(self, websocket: WebSocket, dependencies: SocketDependencies) -> None:
        self._socket = websocket
        self._deps = dependencies
        self._stream: ControlStream | None = None
        self._installation_id: UUID | None = None
        self._utrace_user_id: UUID | None = None
        self._session_id: UUID | None = None
        # One frame at a time: a sequence is allocated and written as a unit, so
        # the extension receives sequences in the order they were allocated.
        self._send_lock = asyncio.Lock()
        # One projection at a time, reading and sending under the same lock, so
        # a revision can never be overtaken by an older one on this connection.
        self._projection_lock = asyncio.Lock()
        self._resumed = asyncio.Event()
        self._woken = asyncio.Event()
        # The revision of each state frame this connection last sent.
        self._projected: dict[str, int] = {}
        # Commands routed on this connection whose outcome the extension has not
        # reported: a control-plane cancellation of one is owed a `cancel` frame.
        self._routed: set[UUID] = set()
        self._closed = False

    async def serve(self) -> None:
        """Authenticate, resume, and carry the connection until it closes."""
        try:
            installation_id, utrace_user_id = await self._authenticate()
        except _InstallationNotLinkedError as refusal:
            _log.info(
                "extension_socket_refused",
                extension_installation_id=str(refusal.installation_id),
                reason=refusal.reason,
            )
            # A browser reports a close before the handshake as a failed
            # connection, indistinguishable from a network fault, so this
            # refusal is accepted first to reach the extension as its own code.
            await self._socket.accept(subprotocol=_ACCEPTED_SUBPROTOCOL)
            await self._socket.close(
                code=_CLOSE_INSTALLATION_NOT_LINKED, reason=refusal.reason
            )
            return
        except (PossessionError, AuthorizationError, ControlPlaneError) as error:
            _log.info("extension_socket_refused", reason=_reason(error))
            await self._socket.close(
                code=_CLOSE_POLICY_VIOLATION, reason=_reason(error)[:120]
            )
            return
        self._installation_id = installation_id
        self._utrace_user_id = utrace_user_id
        await self._socket.accept(subprotocol=_ACCEPTED_SUBPROTOCOL)
        _log.info(
            "extension_socket_accepted",
            extension_installation_id=str(installation_id),
        )
        try:
            # The listener is established before the first projection, so a
            # change committed after that projection read its state wakes it.
            # A lost listener fails the group and ends the connection, and the
            # extension's reconnect re-projects on a fresh one.
            async with (
                self._deps.database.listener(CONTROL_CHANNEL) as listener,
                asyncio.TaskGroup() as group,
            ):
                watchers = (
                    group.create_task(self._listen(listener)),
                    group.create_task(self._deliver_when_woken()),
                )
                await self._converse(installation_id)
                for watcher in watchers:
                    watcher.cancel()
        finally:
            await self._on_disconnect()

    async def _converse(self, installation_id: UUID) -> None:
        """Resume, then serve frames until the extension leaves or breaks protocol."""
        try:
            await self._resume()
            await self._pump()
        except WebSocketDisconnect:
            return
        except ProtocolError as error:
            _log.info(
                "extension_socket_protocol_error",
                extension_installation_id=str(installation_id),
                reason=error.reason,
                detail=error.detail,
            )
            await self._close(code=_close_code(error.reason), reason=error.reason)

    async def _listen(self, listener: AsyncConnection[DictRow]) -> None:
        """Wake delivery for every committed change to the session this serves."""
        async for notification in listener.notifies():
            if self._session_id is not None and notification.payload == str(
                self._session_id
            ):
                self._woken.set()

    async def _deliver_when_woken(self) -> None:
        """Re-project after each wake; a burst of changes coalesces into one pass."""
        await self._resumed.wait()
        while True:
            await self._woken.wait()
            self._woken.clear()
            try:
                await self._project_current_state()
            except WebSocketDisconnect:
                # The extension left or the conversation closed the connection;
                # the conversation ends the group.
                return

    async def _authenticate(self) -> tuple[UUID, UUID]:
        """Verify the subprotocol proof of possession before accepting."""
        subprotocols = tuple(str(value) for value in self._socket.scope["subprotocols"])
        if _ACCEPTED_SUBPROTOCOL not in subprotocols:
            raise AuthorizationError(
                subject="extension control socket",
                reason="the connection did not request the utrace.extension.v1 protocol",
            )
        installation_id = _installation_from(subprotocols)
        proof = decode_subprotocol_proof(subprotocols)
        async with self._deps.database.transaction() as conn:
            try:
                installation = await load_installation(
                    conn, installation_id=installation_id
                )
            except AggregateNotFoundError as error:
                raise _InstallationNotLinkedError(
                    installation_id, "installation_unknown"
                ) from error
            if as_str(installation, "account_link_state") != _LINKED:
                raise _InstallationNotLinkedError(installation_id, "installation_revoked")
            challenge = verify_possession(
                device_public_key_spki_b64u=as_str(
                    installation, "device_public_key_spki_b64u"
                ),
                challenge_b64u=proof.challenge_b64u,
                signature_b64u=proof.signature_b64u,
                expected_audience=self._deps.socket_audience,
                expected_purpose="control_socket",
                expected_installation_id=installation_id,
                now=datetime.now(tz=UTC),
                max_age_seconds=self._deps.possession_max_age_seconds,
            )
            await consume_possession_proof(
                conn,
                challenge=challenge,
                installation_id=installation_id,
                device_public_key_spki_b64u=as_str(
                    installation, "device_public_key_spki_b64u"
                ),
            )
        self._stream = ControlStream(
            self._deps.database, installation_id=installation_id
        )
        await self._stream.mark_connected()
        return installation_id, as_uuid(installation, "utrace_user_id")

    async def _resume(self) -> None:
        """Answer the first frame, which must be `recovery_request`."""
        frame = await self._receive()
        if not isinstance(frame, RecoveryRequestFrame):
            raise ProtocolError(
                "recovery_request_expected",
                f"the first frame was {frame.kind!r}",
            )
        installation_id = self._require_installation()
        if frame.payload.installation_id != installation_id:
            raise ProtocolError(
                "installation_mismatch",
                "the recovery request names a different installation",
            )
        if frame.payload.protocol_version not in self._deps.supported_protocol_versions:
            await self._send_unsequenced_close("unsupported_protocol_version")
            return
        resume_from = frame.payload.resume_from_sequence
        superseded = await self._require_stream().truncate(resume_from=resume_from)
        self._serve_session(frame.payload.session_id)
        await settle_unknown_outcomes(
            self._deps.database, extension_installation_id=installation_id
        )
        await self._send(
            frames.recovery_state(
                disposition="resumed" if superseded == 0 else "restarted",
                next_sequence=resume_from + 2,
                session_id=frame.payload.session_id,
                protocol_version=self._deps.protocol_version,
                reason=None,
            )
        )
        await self._project_current_state()
        self._resumed.set()

    async def _pump(self) -> None:
        """Serve frames until the extension disconnects or the protocol breaks."""
        while True:
            frame = await self._receive()
            await self._handle(frame)

    async def _handle(self, frame: OutboundFrame) -> None:
        """Route one validated frame to the controller that owns it."""
        installation_id = self._require_installation()
        if isinstance(frame, HeartbeatFrame):
            await self._require_stream().acknowledge(
                frame.payload.acknowledged_sequence, strict=False
            )
            await self._send(
                frames.heartbeat(
                    acknowledged_sequence=frame.payload.acknowledged_sequence
                )
            )
            return
        if isinstance(frame, ControlAckFrame):
            await self._require_stream().acknowledge(
                frame.payload.acknowledged_sequence, strict=True
            )
            return
        if isinstance(frame, BindingOfferFrame):
            await self._bind(frame, installation_id=installation_id)
            return
        if isinstance(frame, CommandResultFrame):
            await self._record_command_result(frame)
            return
        if isinstance(frame, ControlActionFrame):
            await self._apply_control(frame)
            return
        if isinstance(frame, ReadinessReportFrame):
            await self._apply_readiness_report(frame)
            return
        if isinstance(frame, PreferencesUpdateFrame):
            await self._apply_preferences(frame)
            return
        raise ProtocolError("unhandled_frame", f"no handler for {frame.kind!r}")

    async def _bind(
        self, frame: BindingOfferFrame, *, installation_id: UUID
    ) -> None:
        """Validate and commit one binding request, then project the result."""
        async with self._deps.database.transaction() as conn:
            outcome = await apply_binding_request(
                conn,
                frame=frame,
                extension_installation_id=installation_id,
                verifier=self._deps.verifier,
                supported_protocol_versions=self._deps.supported_protocol_versions,
            )
        if outcome.status != "bound":
            # A refusal is this request's answer rather than durable state, so
            # it is sent here and never projected.
            await self._send(_binding_frame(outcome))
            return
        self._serve_session(outcome.interview_session_id)
        # Every command still pending addresses a binding this one replaced.
        await cancel_pending_visual_commands(
            self._deps.database,
            client_id=_require(outcome.client_id, "client_id"),
            interview_session_id=outcome.interview_session_id,
            reason="replaced_by_new_document",
        )
        if self._deps.observation is None:
            # The binding is real but nothing can report the correlated path, so
            # the extension half is ready and the observation half stays blocked.
            await record_observation_facts(
                self._deps.database,
                interview_session_id=outcome.interview_session_id,
                preview_allocation_ready=False,
                extension_readiness=ExtensionReadiness.READY,
                browser_sdk_ready=False,
                server_sdk_ready=False,
                trace_correlation_ready=False,
            )
        else:
            await refresh_observation_readiness(
                self._deps.database,
                client_id=_require(outcome.client_id, "client_id"),
                interview_session_id=outcome.interview_session_id,
                observation=self._deps.observation,
            )
        await self._project_current_state()

    async def _record_command_result(self, frame: CommandResultFrame) -> None:
        """Accept the extension's single canonical terminal outcome."""
        # The extension settled it itself, so no cancellation is owed to it.
        self._routed.discard(frame.payload.command_id)
        client_id = await self._client_for_binding(frame.payload.extension_binding_id)
        await record_visual_command_result(
            self._deps.database, client_id=client_id, frame=frame
        )

    async def _apply_control(self, frame: ControlActionFrame) -> None:
        """Apply one control pill and return the authoritative revision."""
        participant = await self._participant_for(frame.payload.session_id)
        action = _CONTROL_BY_ACTION[frame.payload.action]
        try:
            result = await apply_participant_control(
                self._deps.database,
                participant=participant,
                interview_session_id=frame.payload.session_id,
                action=action,
                return_window_seconds=self._deps.return_window_seconds,
                observation=self._deps.observation,
                control_hub=self._deps.control_hub,
            )
        except ControlPlaneError as error:
            await self._send(
                frames.control_action_result(
                    action_id=frame.payload.action_id,
                    session_id=frame.payload.session_id,
                    disposition="rejected",
                    session_control_revision=await self._projection_revision(
                        frame.payload.session_id
                    ),
                    mute_state="unmuted",
                    pause_state="running",
                    reason=_reason_code(error),
                )
            )
            return
        await self._send(
            frames.control_action_result(
                action_id=frame.payload.action_id,
                session_id=frame.payload.session_id,
                disposition="accepted",
                session_control_revision=result.projection.revision,
                mute_state="muted" if result.muted else "unmuted",
                pause_state=_pause_state(result.session_state, result.paused),
                reason=None,
            )
        )
        await self._project_current_state()

    async def _apply_readiness_report(self, frame: ReadinessReportFrame) -> None:
        """Record a local readiness fact; the control plane still owns readiness."""
        if frame.payload.report == "withdrawn":
            await withdraw_observation_readiness(
                self._deps.database,
                interview_session_id=frame.payload.session_id,
                reason=frame.payload.reason,
            )
            await cancel_pending_visual_commands(
                self._deps.database,
                client_id=await self._client_for_session(frame.payload.session_id),
                interview_session_id=frame.payload.session_id,
                reason=frame.payload.reason,
            )
        elif self._deps.observation is not None:
            await refresh_observation_readiness(
                self._deps.database,
                client_id=await self._client_for_session(frame.payload.session_id),
                interview_session_id=frame.payload.session_id,
                observation=self._deps.observation,
            )
        self._serve_session(frame.payload.session_id)
        await self._project_current_state()

    async def _apply_preferences(self, frame: PreferencesUpdateFrame) -> None:
        """Apply a preferences change against the revision the caller expected."""
        utrace_user_id = self._require_user()
        try:
            await update_preferences(
                self._deps.database,
                utrace_user_id=utrace_user_id,
                expected_revision=frame.payload.expected_preferences_revision,
                visual_guidance_enabled=frame.payload.visual_guidance_enabled,
                agent_cursor_enabled=frame.payload.agent_cursor_enabled,
            )
        except ControlPlaneError as refusal:
            # A stale expected revision is refused; the projection below sends
            # the current preferences if this connection has not sent them yet.
            _log.info(
                "extension_preferences_update_refused",
                utrace_user_id=str(utrace_user_id),
                expected_revision=frame.payload.expected_preferences_revision,
                reason=type(refusal).__name__,
            )
        await self._project_current_state()

    def _serve_session(self, session_id: UUID | None) -> None:
        """Serve one session's state; another session's revisions start afresh."""
        if session_id != self._session_id:
            for kind in _SESSION_FRAMES:
                self._projected.pop(kind, None)
        self._session_id = session_id

    async def _project_current_state(self) -> None:
        """Send whatever advanced since this connection last sent it.

        The binding, readiness, and character state each go out at most once
        per revision; the durable pending commands of the current binding are
        routed; and a routed command the control plane canceled is withdrawn
        from the extension. A new connection has sent nothing, so its first
        projection sends everything.
        """
        async with self._projection_lock:
            utrace_user_id = self._require_user()
            async with self._deps.database.connection() as conn:
                preferences = await load_preferences(conn, utrace_user_id=utrace_user_id)
            await self._project(
                frames.preferences_state(
                    preferences_revision=preferences.preferences_revision,
                    visual_guidance_enabled=preferences.visual_guidance_enabled,
                    agent_cursor_enabled=preferences.agent_cursor_enabled,
                ),
                revision=preferences.preferences_revision,
            )
            session_id = self._session_id
            if session_id is None:
                return
            async with self._deps.database.connection() as conn:
                binding = await latest_binding(conn, interview_session_id=session_id)
            if binding is not None:
                await self._project(
                    _binding_frame(binding), revision=binding.binding_revision
                )
            readiness = await load_readiness(
                self._deps.database, interview_session_id=session_id
            )
            await self._project(
                frames.readiness(
                    readiness_revision=readiness.readiness_revision,
                    session_id=session_id,
                    extension_readiness=readiness.extension_readiness.value,
                    combined_readiness="ready" if readiness.observation else "blocked",
                    blockers=readiness.blockers,
                ),
                revision=readiness.readiness_revision,
            )
            async with self._deps.database.connection() as conn:
                character = await load_character(conn, interview_session_id=session_id)
            await self._project(
                frames.character_state(
                    character_revision=character.character_revision,
                    session_id=session_id,
                    state=character.state.value,
                    gaze_x=character.gaze_x,
                    gaze_y=character.gaze_y,
                    speech_energy=character.speech_energy,
                    motion_scale=character.motion_scale,
                ),
                revision=character.character_revision,
            )
            await self._route_pending_commands()
            await self._withdraw_canceled_commands()

    async def _project(self, frame: frames.OutgoingFrame, *, revision: int) -> None:
        """Send one state frame unless this connection already sent its revision."""
        if self._projected.get(frame.kind, -1) >= revision:
            return
        await self._send(frame)
        self._projected[frame.kind] = revision

    async def _route_pending_commands(self) -> None:
        """Route the authorized commands recorded for this installation's binding."""
        routable = await claim_routable_commands(
            self._deps.database, extension_installation_id=self._require_installation()
        )
        for command in routable:
            self._routed.add(command.command_id)
            await self._send(
                frames.visual_command(
                    command_id=command.command_id,
                    client_id=command.client_id,
                    session_id=command.interview_session_id,
                    extension_binding_id=command.extension_binding_id,
                    document_attachment_id=command.document_attachment_id,
                    expected_lifecycle_revision=command.expected_lifecycle_revision,
                    deadline_at=command.deadline_at,
                    command=command.command,
                )
            )
            _log.info(
                "visual_command_routed",
                client_id=str(command.client_id),
                interview_session_id=str(command.interview_session_id),
                command_id=str(command.command_id),
                extension_binding_id=str(command.extension_binding_id),
            )

    async def _withdraw_canceled_commands(self) -> None:
        """Tell the extension about every routed command the control plane canceled."""
        if not self._routed:
            return
        outcomes = await routed_outcomes(
            self._deps.database, command_ids=tuple(self._routed)
        )
        for command_id, outcome in outcomes.items():
            self._routed.discard(command_id)
            if outcome.outcome != _CANCELED or outcome.reason is None:
                continue
            await self._send(frames.cancel(command_id=command_id, reason=outcome.reason))

    async def _send(self, frame: frames.OutgoingFrame) -> None:
        """Stamp the frame through the durable stream, then write it."""
        async with self._send_lock:
            if self._closed:
                raise WebSocketDisconnect(code=_CLOSE_NORMAL)
            stamped = await self._require_stream().record(
                frame, interview_session_id=self._session_id
            )
            await self._socket.send_text(
                json.dumps(
                    frame.envelope(
                        message_id=stamped.message_id,
                        sent_at=stamped.sent_at,
                        sequence=stamped.sequence,
                    )
                )
            )

    async def _close(self, *, code: int, reason: str) -> None:
        """Close the connection; nothing is sent on it afterwards."""
        async with self._send_lock:
            self._closed = True
            await self._socket.close(code=code, reason=reason[:120])

    async def _send_unsequenced_close(self, reason: str) -> None:
        """Close the socket without sending a frame the extension cannot accept."""
        await self._close(code=_CLOSE_POLICY_VIOLATION, reason=reason)

    async def _receive(self) -> OutboundFrame:
        """Read and validate the next frame the extension sent."""
        text = await self._socket.receive_text()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise ProtocolError(
                "malformed_frame", "the extension sent a frame that is not JSON"
            ) from error
        return parse_outbound_frame(payload)

    async def _on_disconnect(self) -> None:
        """Withdraw observation readiness when the socket goes.

        The binding survives: losing the control connection withdraws observation
        readiness and begins bounded recovery, and the extension reattaches with
        a fresh handshake. Document loss, tab closure, session closure, and
        installation revocation are what revoke a binding.

        A command already routed to the extension is not settled here. The page
        may have applied it, so its outcome is unknown rather than canceled, and
        the next connection's resume settles it as such. Pause, rebind,
        replacement, and session closure are the authority withdrawals that do
        cancel a pending command.
        """
        installation_id = self._installation_id
        if installation_id is None:
            return
        await self._require_stream().mark_disconnected()
        session_id = self._session_id
        if session_id is not None:
            await withdraw_observation_readiness(
                self._deps.database,
                interview_session_id=session_id,
                reason="extension_control_connection_lost",
            )
        _log.info(
            "extension_socket_closed",
            extension_installation_id=str(installation_id),
            interview_session_id=str(session_id) if session_id else None,
        )

    async def _participant_for(self, session_id: UUID) -> ParticipantPrincipal:
        """Resolve the participant a control action acts for, refusing a mismatch."""
        utrace_user_id = self._require_user()
        async with self._deps.database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select sessions.client_id, sessions.client_user_id,
                       users.id as utrace_user_id, users.auth_user_id
                from public.interview_sessions as sessions
                join public.client_users on client_users.id = sessions.client_user_id
                join public.users on users.id = client_users.utrace_user_id
                where sessions.id = %s
                """,
                (session_id,),
            )
            row = await cur.fetchone()
        if row is None:
            raise ProtocolError("unknown_session", "no such Interview Session")
        if as_uuid(row, "utrace_user_id") != utrace_user_id:
            raise ProtocolError(
                "session_not_owned",
                "this installation is not linked to the session's originating user",
            )
        return ParticipantPrincipal(
            auth_user_id=as_uuid(row, "auth_user_id"),
            utrace_user_id=utrace_user_id,
            client_user_id=as_uuid(row, "client_user_id"),
            client_id=as_uuid(row, "client_id"),
        )

    async def _client_for_session(self, session_id: UUID) -> UUID:
        """Return the client that owns one Interview Session."""
        async with self._deps.database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "select client_id from public.interview_sessions where id = %s",
                (session_id,),
            )
            row = await cur.fetchone()
        if row is None:
            raise ProtocolError("unknown_session", "no such Interview Session")
        return as_uuid(row, "client_id")

    async def _client_for_binding(self, binding_id: UUID) -> UUID:
        """Return the client that owns one binding."""
        async with self._deps.database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "select client_id from public.extension_bindings where id = %s",
                (binding_id,),
            )
            row = await cur.fetchone()
        if row is None:
            raise ProtocolError("unknown_binding", "no such extension binding")
        return as_uuid(row, "client_id")

    async def _projection_revision(self, session_id: UUID) -> int:
        """Return the current projection revision for a refused control."""
        async with self._deps.database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "select revision from public.session_projections where session_id = %s",
                (session_id,),
            )
            row = await cur.fetchone()
        return as_int(row, "revision") if row is not None else 0

    def _require_installation(self) -> UUID:
        """Return the authenticated installation, refusing an unauthenticated call."""
        if self._installation_id is None:
            raise ProtocolError("not_authenticated", "the socket has no installation")
        return self._installation_id

    def _require_stream(self) -> ControlStream:
        """Return this connection's durable stream, refusing before authentication."""
        if self._stream is None:
            raise ProtocolError("not_authenticated", "the socket has no control stream")
        return self._stream

    def _require_user(self) -> UUID:
        """Return the linked uTrace User, refusing an unauthenticated call."""
        if self._utrace_user_id is None:
            raise ProtocolError("not_authenticated", "the socket has no linked user")
        return self._utrace_user_id


def _binding_frame(outcome: BoundDocument) -> frames.OutgoingFrame:
    """Render the binding result the extension renders."""
    return frames.binding_state(
        binding_revision=outcome.binding_revision,
        session_id=outcome.interview_session_id,
        status=outcome.status,
        client_id=outcome.client_id,
        extension_binding_id=outcome.extension_binding_id,
        document_attachment_id=outcome.document_attachment_id,
        preview_runtime_allocation_id=outcome.preview_runtime_allocation_id,
        reason=outcome.reason,
    )


def _require(value: UUID | None, field: str) -> UUID:
    """Return a required identity from a bound outcome, refusing a missing one."""
    if value is None:
        raise ProtocolError("incomplete_binding", f"the binding carries no {field}")
    return value


def _installation_from(subprotocols: tuple[str, ...]) -> UUID:
    """Read the installation identity out of the subprotocol tokens."""
    for token in subprotocols:
        if token.startswith(_INSTALLATION_PREFIX):
            try:
                return UUID(token.removeprefix(_INSTALLATION_PREFIX))
            except ValueError as error:
                raise AuthorizationError(
                    subject="extension control socket",
                    reason="the installation subprotocol is not an identifier",
                ) from error
    raise AuthorizationError(
        subject="extension control socket",
        reason="the connection carried no utrace.installation.<id> token",
    )


def _pause_state(session_state: str, paused: bool) -> str:
    """Map the durable session state onto the pills' pause vocabulary."""
    if session_state == "closed":
        return "ended"
    return "paused" if paused else "running"


def _close_code(reason: str) -> int:
    """A sequence gap is an integrity failure with its own close code."""
    return _CLOSE_SEQUENCE_GAP if reason == "sequence_gap" else _CLOSE_POLICY_VIOLATION


def _reason(error: Exception) -> str:
    """Return a redacted, log-safe reason for a refusal."""
    if isinstance(error, PossessionError):
        return error.reason
    return type(error).__name__


def _reason_code(error: Exception) -> str:
    """Return the reason code a refused control action reports."""
    return _snake(type(error).__name__)


def _snake(name: str) -> str:
    """Render a class name as the protocol's lowercase reason-code form."""
    letters: list[str] = []
    for index, character in enumerate(name):
        if character.isupper() and index:
            letters.append("_")
        letters.append(character.lower())
    return "".join(letters)[:64]
