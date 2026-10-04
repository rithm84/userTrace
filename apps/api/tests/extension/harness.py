"""The extension client harness the control-socket tests drive.

An ordinary WebSocket client that speaks the protocol exactly as the service
worker does: it authenticates with a real ES256 proof of possession carried in
the subprotocol, holds every inbound frame to the shared `inbound-frame.v1`
contract, enforces the same inbound sequence-gap rule, and acknowledges what it
processed. Nothing on the control-plane side is stubbed.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import websockets
from utrace_contracts.domain.lifecycle import InterviewPhase, ProductSignalState
from utrace_contracts.registry import validate_against_schema
from websockets.asyncio.client import ClientConnection

from app.db.pool import Database
from app.extension.bindings import mint_binding_offer
from app.ids import uuid7
from app.ingestion.signing import Signer
from app.interview.readiness import (
    record_conversational_facts,
)
from tests.extension.conftest import SOCKET_AUDIENCE, SOCKET_PORT
from tests.extension.keys import DeviceKey
from tests.factories import CaseFixture, create_case
from tests.interview.fixtures import (
    create_live_session,
    create_preview_allocation,
    link_installation,
)
from tests.supabase_auth import SignedInUser

_CONTROL_URI = f"ws://127.0.0.1:{SOCKET_PORT}/v1/extension/control"
PROTOCOL_VERSION = "1.0"
ORIGIN = "https://preview.utrace.test"
_TIMEOUT_SECONDS = 10.0
SEQUENCE_GAP_CLOSE_CODE = 4002
INSTALLATION_NOT_LINKED_CLOSE_CODE = 4003
POLICY_VIOLATION_CLOSE_CODE = 1008


class Client:
    """One connected extension, speaking the protocol as the service worker does."""

    def __init__(self, connection: ClientConnection) -> None:
        self._connection = connection
        self.acknowledged = 0

    async def send(self, kind: str, payload: Mapping[str, Any]) -> None:
        """Send one unsequenced outbound frame."""
        await self._connection.send(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": kind,
                    "message_id": str(uuid7()),
                    "sent_at": now(),
                    "payload": dict(payload),
                }
            )
        )

    async def receive(self) -> Mapping[str, Any]:
        """Read the next inbound frame, enforcing the contract and the gap rule."""
        raw = await asyncio.wait_for(self._connection.recv(), timeout=_TIMEOUT_SECONDS)
        frame = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        validate_against_schema("extension-protocol", "inbound-frame.v1", frame)
        if frame["kind"] == "heartbeat":
            return frame
        sequence = int(frame["sequence"])
        if sequence != self.acknowledged + 1:
            raise AssertionError(
                f"sequence {sequence} arrived after {self.acknowledged}"
            )
        self.acknowledged = sequence
        return frame

    async def acknowledge(self) -> None:
        """Acknowledge the highest contiguous sequence processed so far."""
        await self.send("control_ack", {"acknowledged_sequence": self.acknowledged})

    async def synchronize(self) -> None:
        """Wait until the control plane has processed everything sent so far.

        Frames are processed in order on one socket, so a heartbeat's reply
        proves the acknowledgements before it were applied.
        """
        await self.send("heartbeat", {"acknowledged_sequence": self.acknowledged})
        reply = await self.receive()
        assert reply["kind"] == "heartbeat"

    async def drain(self, count: int) -> list[Mapping[str, Any]]:
        """Read and acknowledge the next `count` sequenced frames."""
        frames: list[Mapping[str, Any]] = []
        for _ in range(count):
            frame = await self.receive()
            frames.append(frame)
            await self.acknowledge()
        return frames

    async def until(
        self, done: Callable[[Sequence[Mapping[str, Any]]], bool]
    ) -> list[Mapping[str, Any]]:
        """Read and acknowledge frames until ``done`` holds for everything read.

        Pushed state arrives whenever its commit wakes the socket, so a test
        states what it must eventually hold rather than an exact interleaving.
        """
        frames: list[Mapping[str, Any]] = []
        while not done(frames):
            frames.append(await self.receive())
            await self.acknowledge()
        return frames


def kinds(frames: Sequence[Mapping[str, Any]]) -> list[str]:
    """The kinds of a run of frames, in arrival order."""
    return [str(frame["kind"]) for frame in frames]


def last_of(frames: Sequence[Mapping[str, Any]], kind: str) -> Mapping[str, Any]:
    """The newest frame of one kind, which is the state the extension holds."""
    matching = [frame for frame in frames if frame["kind"] == kind]
    if not matching:
        raise AssertionError(f"no {kind} frame among {kinds(frames)}")
    return matching[-1]


@asynccontextmanager
async def connect(
    *, device: DeviceKey, installation_id: UUID
) -> AsyncIterator[Client]:
    """Open one authenticated control connection as the extension would."""
    challenge, signature = device.prove(
        audience=SOCKET_AUDIENCE,
        purpose="control_socket",
        installation_id=installation_id,
    )
    async with websockets.connect(
        _CONTROL_URI,
        subprotocols=[
            "utrace.extension.v1",  # type: ignore[list-item]
            f"utrace.installation.{installation_id}",  # type: ignore[list-item]
            f"utrace.pop.{challenge}.{signature}",  # type: ignore[list-item]
        ],
        open_timeout=_TIMEOUT_SECONDS,
    ) as connection:
        yield Client(connection)


def now() -> str:
    """An RFC 3339 UTC instant in the form the protocol requires."""
    from datetime import UTC, datetime

    return datetime.now(tz=UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def recovery_payload(
    *,
    installation_id: UUID,
    resume_from: int,
    pending: tuple[UUID, ...] = (),
    session_id: UUID | None = None,
) -> dict[str, Any]:
    """The first frame every connection sends.

    A reconnecting extension names the session it was serving, which is what
    lets the control plane re-project that session's current state.
    """
    payload: dict[str, Any] = {
        "installation_id": str(installation_id),
        "protocol_version": PROTOCOL_VERSION,
        "extension_version": "1.0.0",
        "resume_from_sequence": resume_from,
        "pending_command_ids": [str(identifier) for identifier in pending],
    }
    if session_id is not None:
        payload["session_id"] = str(session_id)
    return payload


class PreparedSession:
    """One prepared case with everything a binding needs."""

    def __init__(
        self,
        *,
        case: CaseFixture,
        session_id: UUID,
        allocation_id: UUID,
        installation_id: UUID,
        device: DeviceKey,
    ) -> None:
        self.case = case
        self.session_id = session_id
        self.allocation_id = allocation_id
        self.installation_id = installation_id
        self.device = device


async def prepare_session(
    database: Database, auth_users: dict[str, SignedInUser], name: str
) -> PreparedSession:
    """Create a live session, its allocation, and one linked installation."""
    case = await create_case(
        database,
        name=name,
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=auth_users["participant"].auth_user_id,
    )
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )
    allocation_id = await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )
    device = DeviceKey.generate()
    installation_id = await link_installation(
        database,
        utrace_user_id=case.utrace_user_id,
        device_public_key_spki_b64u=device.public_key_spki_b64u,
        protocol_version=PROTOCOL_VERSION,
    )
    await record_conversational_facts(
        database,
        interview_session_id=session_id,
        participant_authenticated=True,
        voice_transport_ready=True,
        sideband_attached=True,
        backend_context_current=True,
    )
    return PreparedSession(
        case=case,
        session_id=session_id,
        allocation_id=allocation_id,
        installation_id=installation_id,
        device=device,
    )


async def binding_offer_payload(
    *,
    database: Database,
    prepared: PreparedSession,
    signer: Signer,
    document_attachment_id: UUID,
) -> dict[str, Any]:
    """Build the binding request the session tab and the SDK jointly produce.

    The attachment identity is the caller's because document activation issues
    it: the page proof carries it and the binding adopts it unchanged.
    """
    offer = await mint_binding_offer(
        database,
        client_id=prepared.case.client_id,
        client_user_id=prepared.case.client_user_id,
        interview_session_id=prepared.session_id,
        extension_installation_id=prepared.installation_id,
        preview_runtime_allocation_id=prepared.allocation_id,
        protocol_version=PROTOCOL_VERSION,
        lifetime_seconds=300,
    )
    return {
        "request": {
            "kind": "offer",
            "offer": {
                "schema_version": 1,
                "offer_token": offer.offer_token,
                "session_id": str(prepared.session_id),
                "client_id": str(prepared.case.client_id),
                "extension_installation_id": str(prepared.installation_id),
                "preview_runtime_allocation_id": str(prepared.allocation_id),
                "protocol_version": PROTOCOL_VERSION,
                "expires_at": offer.expires_at.isoformat().replace("+00:00", "Z"),
            },
        },
        "page_proof": page_proof(
            signer=signer,
            client_id=prepared.case.client_id,
            session_id=prepared.session_id,
            preview_runtime_allocation_id=prepared.allocation_id,
            document_attachment_id=document_attachment_id,
            origin=ORIGIN,
            lifetime_seconds=300,
        ),
        "tab_locator": {"tab_id": 11, "frame_id": 0},
        "document_origin": ORIGIN,
    }


async def bind_document(
    client: Client,
    *,
    database: Database,
    prepared: PreparedSession,
    signer: Signer,
    document_attachment_id: UUID,
) -> list[Mapping[str, Any]]:
    """Resume a fresh stream, bind one document, and read until it is projected.

    Returns the frames that followed the binding request, which end once the
    bound binding, the session's readiness, and its character state all
    arrived.
    """
    await client.send(
        "recovery_request",
        recovery_payload(installation_id=prepared.installation_id, resume_from=0),
    )
    await client.drain(2)
    await client.send(
        "binding_offer",
        await binding_offer_payload(
            database=database,
            prepared=prepared,
            signer=signer,
            document_attachment_id=document_attachment_id,
        ),
    )
    return await client.until(
        lambda frames: {"binding_state", "readiness", "character_state"} <= set(kinds(frames))
        and last_of(frames, "binding_state")["payload"]["status"] == "bound"
    )


async def await_disconnect(database: Database, *, installation_id: UUID) -> None:
    """Wait until the control plane finished tearing one connection down.

    Disconnect withdraws observation readiness, so a test that states readiness
    afterwards has to know the withdrawal already happened.
    """
    for _ in range(200):
        async with database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select disconnected_at from public.extension_control_streams
                where extension_installation_id = %s
                """,
                (installation_id,),
            )
            row = await cur.fetchone()
        if row is not None and row["disconnected_at"] is not None:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the control plane never recorded the disconnect")


def page_proof(
    *,
    signer: Signer,
    client_id: UUID,
    session_id: UUID,
    preview_runtime_allocation_id: UUID,
    document_attachment_id: UUID,
    origin: str,
    lifetime_seconds: int,
) -> dict[str, Any]:
    """Sign one page proof exactly as document activation issues it.

    Ingestion signs the claim set with this process's attestation key, so the
    control plane verifies the forwarded envelope with the same signer.
    """
    issued_at = datetime.now(tz=UTC).replace(microsecond=0)
    expires_at = issued_at + timedelta(seconds=lifetime_seconds)
    envelope = signer.sign(
        {
            "client_id": str(client_id),
            "session_id": str(session_id),
            "preview_environment_id": str(uuid7()),
            "preview_runtime_allocation_id": str(preview_runtime_allocation_id),
            "origin": origin,
            "document_attachment_id": str(document_attachment_id),
            "document_nonce": f"nonce-{uuid7().hex[:16]}",
            "sdk_protocol_version": PROTOCOL_VERSION,
            "issued_at": _rfc3339(issued_at),
            "expires_at": _rfc3339(expires_at),
        }
    )
    return envelope.model_dump(mode="json")


def _rfc3339(value: datetime) -> str:
    """Render an instant in the RFC 3339 UTC form the protocol requires."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
