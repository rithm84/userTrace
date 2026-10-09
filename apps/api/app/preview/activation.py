"""Ending a prepared runtime's Server SDK dormancy, and ending its stream again.

A warm runtime has no Interview Session and no preview origin, so it cannot hold
a bootstrap credential: that credential's subject is the allocation and its
binding is the public preview origin, and the activation it opens names the
session. The packaged SDK therefore stays dormant in the pool by its own
contract, and this module is the claim-time step that ends that dormancy for one
exact allocation.

Injection writes the activation variables into the runtime's
``.utrace-activation`` file and the caller restarts the supervisor. A sandbox's
environment is fixed when it is created, before any session exists, so the file
is the mechanism: the pinned template's supervisor sources it before it starts
the application.

Termination is the mirror. The Server SDK is told to end its stream and flush
before the sandbox is destroyed, so the last export batch is acknowledged
instead of disappearing with the container. The command has to reach the
control-plane instance that holds that stream's socket, which is never the
worker running teardown, so it goes over the control plane's own service route.
A supervisor restart ends the stream the same way first, because the restarted
process opens a new activation that supersedes it.

Contract: docs/arch-components/server-sdk.md#configuration-identity-and-credentials
"""

from __future__ import annotations

import asyncio
from base64 import b64encode, urlsafe_b64encode
from dataclasses import dataclass
from json import dumps
from typing import Final
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict

from app.db.pool import Database
from app.db.rows import as_uuid
from app.errors import GuardRejectedError
from app.ingestion.bootstrap import (
    ServerBootstrapCredential,
    mint_server_bootstrap_credential,
)
from app.logging import get_logger
from app.preview.config import PreviewSettings
from app.preview.runtime import PreviewRuntimeHandle, PreviewRuntimeProvider

__all__ = [
    "ACTIVATION_FILE_NAME",
    "STREAM_END_SECONDS",
    "ControlPlaneCommands",
    "ServerActivationContext",
    "ServerActivationEndpoints",
    "ServerTermination",
    "active_server_activation_id",
    "await_stream_end",
    "install_server_activation",
    "manifest_trust_anchor_value",
    "observation_hosts",
    "restart_observed_runtime",
    "terminate_server_activation",
]

_log = get_logger(__name__)

#: The file the pinned template's supervisor sources before it starts the
#: application. It sits in the recipe's application directory.
ACTIVATION_FILE_NAME = ".utrace-activation"

#: DER prefix of a SubjectPublicKeyInfo holding an Ed25519 key (RFC 8410): the
#: algorithm identifier ``1.3.101.112`` and the 32-byte BIT STRING header. The
#: control plane stores the raw public key, and the SDK's trust anchor wants the
#: SPKI form, so the two differ only by these twelve bytes.
_ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")

_COMMAND_SCHEMA_VERSION = "utrace-server-control-command-request-v1"
_COMMAND_TIMEOUT_SECONDS = 15.0

STREAM_END_SECONDS: Final[float] = 15.0
"""How long a runtime is kept running for the Server SDK's terminal stream report."""
_STREAM_END_POLL_SECONDS: Final[float] = 0.5


class ServerActivationEndpoints(BaseModel):
    """Where a prepared runtime's Server SDK reports, and as what.

    Identity is not here. The credential carries the client, the Interview
    Session, and the allocation; these four values only say which control plane
    to reach and which manifest service the process claims.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    control_url: str
    """``wss://`` base URL of the control plane; it may include a path."""
    ingest_url: str
    """``https://`` base URL of telemetry ingestion; it may include a path."""
    service_name: str
    """The signed manifest's ``service_key`` this process claims."""
    manifest_trust_anchor: str
    """Base64url JSON ``{key_id, algorithm, public_key_spki_base64}``."""

    @staticmethod
    def from_settings(
        settings: PreviewSettings, *, trust_anchor: str
    ) -> ServerActivationEndpoints:
        """Read the endpoints from preview policy and the control plane's key."""
        return ServerActivationEndpoints(
            control_url=settings.server_control_url,
            ingest_url=settings.server_ingest_url,
            service_name=settings.server_service_name,
            manifest_trust_anchor=trust_anchor,
        )

    def hosts(self) -> tuple[str, ...]:
        """Return the hostnames this runtime's Server SDK has to reach."""
        return observation_hosts(
            control_url=self.control_url, ingest_url=self.ingest_url
        )

    def command_url(self) -> str:
        """Return the HTTP route that puts a control command on a live stream.

        The control plane serves the SDK's socket and this route, so the base
        URL the runtime was told to connect to, path included, is also where a
        command for that stream is accepted. Deriving it keeps one base URL
        rather than two variables that can disagree about which control plane
        is in play.
        """
        origin = self.control_url.rstrip("/")
        for websocket, http in (("wss://", "https://"), ("ws://", "http://")):
            if origin.startswith(websocket):
                return f"{http}{origin.removeprefix(websocket)}/v1/server-sdk/control-commands"
        raise GuardRejectedError(
            machine="preview_runtime_allocation",
            trigger="server_control_command",
            reason=(
                "the configured Server SDK control URL is not a WebSocket origin, "
                f"so no control-command route follows from it: {origin}"
            ),
        )


class ServerTermination(BaseModel):
    """What terminating one allocation's server stream achieved."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    delivered: bool
    activation_id: UUID | None
    command_id: UUID | None
    detail: str
    """Why nothing was delivered, when ``delivered`` is false. Empty otherwise."""


def observation_hosts(*, control_url: str, ingest_url: str) -> tuple[str, ...]:
    """Return the hostnames a prepared runtime has to reach, deduplicated.

    A preview runtime's outbound wall is what makes its side effects
    unreachable, and the Server SDK's control connection and exporter are the
    only outbound traffic a prepared preview is allowed to have. Reading the
    hosts from the configured endpoints keeps the wall and the destinations from
    drifting apart.
    """
    names: list[str] = []
    for url in (control_url, ingest_url):
        host = urlsplit(url).hostname
        if host is None:
            raise GuardRejectedError(
                machine="preview_runtime_allocation",
                trigger="server_activation_hosts",
                reason=f"the configured Server SDK endpoint names no host: {url}",
            )
        names.append(host)
    return tuple(dict.fromkeys(names))


def manifest_trust_anchor_value(*, key_id: str, public_key: bytes) -> str:
    """Encode the control plane's manifest key the way the SDK's variable wants."""
    if len(public_key) != 32:
        raise ValueError("an Ed25519 public key is 32 bytes")
    anchor = dumps(
        {
            "key_id": key_id,
            "algorithm": "ed25519",
            "public_key_spki_base64": b64encode(
                _ED25519_SPKI_PREFIX + public_key
            ).decode("ascii"),
        }
    )
    return urlsafe_b64encode(anchor.encode("utf-8")).decode("ascii").rstrip("=")


def _activation_file(
    *, endpoints: ServerActivationEndpoints, credential: ServerBootstrapCredential
) -> str:
    """Render the shell-sourceable activation file for one claimed runtime.

    Every value is single-quoted because the supervisor sources this file, and a
    signed credential is base64url text that must reach the SDK byte for byte.
    """
    values = {
        "UTRACE_SERVER_BOOTSTRAP_CREDENTIAL": credential.token,
        "UTRACE_SERVER_CONTROL_URL": endpoints.control_url,
        "UTRACE_SERVER_INGEST_URL": endpoints.ingest_url,
        "UTRACE_SERVER_SERVICE_NAME": endpoints.service_name,
        "UTRACE_SERVER_MANIFEST_TRUST_ANCHOR": endpoints.manifest_trust_anchor,
    }
    for name, value in values.items():
        if "'" in value or "\n" in value:
            raise ValueError(f"{name} carries a quote or newline and cannot be sourced")
    lines = [
        "# Written by the uTrace control plane when it claimed this runtime.",
        "# The supervisor sources it; the Server SDK reads it and activates.",
    ]
    lines.extend(f"{name}='{value}'" for name, value in values.items())
    return "\n".join(lines) + "\n"


async def install_server_activation(
    *,
    provider: PreviewRuntimeProvider,
    handle: PreviewRuntimeHandle,
    endpoints: ServerActivationEndpoints,
    application_directory: str,
    lane_signing_key: str,
    client_id: UUID,
    interview_session_id: UUID,
    preview_runtime_allocation_id: UUID,
    public_preview_host: str,
) -> ServerBootstrapCredential:
    """Bind a fresh bootstrap credential to one allocation and install it.

    The caller restarts the runtime afterwards: the supervisor is the container
    entrypoint, so the file only takes effect on the next start, and that start
    is what turns the packaged SDK from dormant into activating.
    """
    credential = mint_server_bootstrap_credential(
        signing_key=lane_signing_key,
        client_id=client_id,
        interview_session_id=interview_session_id,
        preview_runtime_allocation_id=preview_runtime_allocation_id,
        public_preview_host=public_preview_host,
    )
    await provider.write_file(
        handle=handle,
        path=f"{application_directory.rstrip('/')}/{ACTIVATION_FILE_NAME}",
        content=_activation_file(endpoints=endpoints, credential=credential).encode(
            "utf-8"
        ),
    )
    _log.info(
        "preview_server_activation_installed",
        client_id=str(client_id),
        interview_session_id=str(interview_session_id),
        preview_runtime_allocation_id=str(preview_runtime_allocation_id),
        public_preview_host=public_preview_host,
        service_name=endpoints.service_name,
        expires_at=credential.expires_at.isoformat(),
    )
    return credential


async def active_server_activation_id(
    database: Database, *, client_id: UUID, preview_runtime_allocation_id: UUID
) -> UUID | None:
    """Return the live server activation bound to one allocation, if there is one.

    Teardown needs the activation's identity to address its stream, and the
    telemetry-ingestion stream owns that row. It is read here rather than
    inferred, so an allocation whose Server SDK never activated terminates
    without pretending a command was delivered.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.telemetry_activations
            where kind = 'server' and state = 'active'
              and client_id = %s and preview_runtime_allocation_id = %s
            order by created_at desc limit 1
            """,
            (client_id, preview_runtime_allocation_id),
        )
        row = await cur.fetchone()
    return None if row is None else as_uuid(row, "id")


class ControlPlaneCommands:
    """Puts one Server SDK control command on a live stream, over HTTP.

    The hub that holds a stream's socket lives in the API process. A worker
    running teardown has no socket of its own, so it asks the control plane the
    runtime was told to connect to. A stream with no socket on the instance that
    answers is refused there, and that refusal is recorded rather than swallowed.
    """

    def __init__(self, *, command_url: str, service_token: str) -> None:
        self._command_url = command_url
        self._service_token = service_token

    async def terminate(self, *, activation_id: UUID, reason: str) -> UUID:
        """Deliver ``terminate`` and return the recorded command's identity."""
        async with httpx.AsyncClient(timeout=_COMMAND_TIMEOUT_SECONDS) as client:
            response = await client.post(
                self._command_url,
                headers={"Authorization": f"Bearer {self._service_token}"},
                json={
                    "schema_version": _COMMAND_SCHEMA_VERSION,
                    "activation_id": str(activation_id),
                    "kind": "terminate",
                    "reason": reason,
                },
            )
        if response.status_code != 200:
            raise GuardRejectedError(
                machine="preview_runtime_allocation",
                trigger="terminate_server_activation",
                reason=(
                    f"{self._command_url} answered {response.status_code} for "
                    f"activation {activation_id}: {response.text[:500]}"
                ),
            )
        return UUID(str(response.json()["command_id"]))


@dataclass(frozen=True, slots=True)
class ServerActivationContext:
    """Everything a preview operation needs to start and end one observation.

    It is assembled once per Job Run or request from process configuration, so
    an operation that claims, publishes, or destroys an allocation carries the
    same control plane, the same lane key, and the same command route rather
    than reading them again at each step.
    """

    endpoints: ServerActivationEndpoints
    lane_signing_key: str
    commands: ControlPlaneCommands

    @staticmethod
    def build(
        *,
        settings: PreviewSettings,
        lane_signing_key: str,
        service_token: str,
        manifest_key_id: str,
        manifest_public_key: bytes,
    ) -> ServerActivationContext:
        """Assemble the context from preview policy and the control plane's keys.

        The trust anchor is derived from the key the control plane signs
        manifests with rather than configured separately: a runtime that trusted
        a different key would refuse every manifest the control plane issues,
        and two variables that can disagree is exactly how that happens.
        """
        endpoints = ServerActivationEndpoints.from_settings(
            settings,
            trust_anchor=manifest_trust_anchor_value(
                key_id=manifest_key_id, public_key=manifest_public_key
            ),
        )
        return ServerActivationContext(
            endpoints=endpoints,
            lane_signing_key=lane_signing_key,
            commands=ControlPlaneCommands(
                command_url=endpoints.command_url(), service_token=service_token
            ),
        )


async def terminate_server_activation(
    database: Database,
    *,
    commands: ControlPlaneCommands,
    client_id: UUID,
    preview_runtime_allocation_id: UUID,
    reason: str,
) -> ServerTermination:
    """End the allocation's server stream before its sandbox is destroyed.

    A delivery failure is reported, not raised: the sandbox still has to go, and
    a runtime left running because its terminal report could not be delivered
    would hold customer-shaped data for the sake of an acknowledgement. The
    outcome is recorded with the release evidence so the gap is visible.
    """
    activation_id = await active_server_activation_id(
        database,
        client_id=client_id,
        preview_runtime_allocation_id=preview_runtime_allocation_id,
    )
    if activation_id is None:
        return ServerTermination(
            delivered=False,
            activation_id=None,
            command_id=None,
            detail="the allocation has no active server activation to terminate",
        )
    try:
        command_id = await commands.terminate(
            activation_id=activation_id, reason=reason
        )
    except (GuardRejectedError, httpx.HTTPError) as error:
        _log.warning(
            "preview_server_termination_undelivered",
            client_id=str(client_id),
            preview_runtime_allocation_id=str(preview_runtime_allocation_id),
            activation_id=str(activation_id),
            detail=str(error),
        )
        return ServerTermination(
            delivered=False,
            activation_id=activation_id,
            command_id=None,
            detail=f"the terminate command was not delivered: {error}",
        )
    _log.info(
        "preview_server_activation_terminated",
        client_id=str(client_id),
        preview_runtime_allocation_id=str(preview_runtime_allocation_id),
        activation_id=str(activation_id),
        command_id=str(command_id),
        reason=reason,
    )
    return ServerTermination(
        delivered=True, activation_id=activation_id, command_id=command_id, detail=""
    )


async def await_stream_end(database: Database, *, activation_id: UUID) -> bool:
    """Wait, bounded, for the Server SDK's terminal report on the activation's streams.

    The report is sent after the SDK flushes, so it can trail the terminate
    command's delivery; stopping the runtime first would lose it.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + STREAM_END_SECONDS
    while True:
        async with database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select count(*) filter (where state = 'open') as open_streams,
                       count(*) as streams
                from public.server_streams where activation_id = %s
                """,
                (activation_id,),
            )
            row = await cur.fetchone()
        if row is not None and int(row["streams"]) > 0 and int(row["open_streams"]) == 0:
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(_STREAM_END_POLL_SECONDS)


async def restart_observed_runtime(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    commands: ControlPlaneCommands,
    client_id: UUID,
    preview_runtime_allocation_id: UUID,
    handle: PreviewRuntimeHandle,
    supervisor_command: str,
    application_directory: str,
) -> None:
    """Restart the runtime's supervisor, ending its live server stream first.

    The restarted process opens a new activation that supersedes the current one,
    so the current stream is ended the way release ends it: terminate, then wait
    for the terminal report. The SDK's own SIGTERM shutdown cannot carry that
    report, because the client's framework may exit the process on the same signal
    before the SDK's flush finishes, as Next.js does. A report that does not
    arrive is logged and leaves the stream open, a visible evidence gap, and the
    restart goes ahead: the runtime has to serve again.
    """
    termination = await terminate_server_activation(
        database,
        commands=commands,
        client_id=client_id,
        preview_runtime_allocation_id=preview_runtime_allocation_id,
        reason="activation_superseded",
    )
    if (
        termination.activation_id is not None
        and termination.delivered
        and not await await_stream_end(database, activation_id=termination.activation_id)
    ):
        _log.warning(
            "preview_server_stream_end_unconfirmed",
            client_id=str(client_id),
            preview_runtime_allocation_id=str(preview_runtime_allocation_id),
            activation_id=str(termination.activation_id),
            waited_seconds=STREAM_END_SECONDS,
        )
    await provider.restart_runtime(
        handle=handle,
        supervisor_command=supervisor_command,
        application_directory=application_directory,
    )
