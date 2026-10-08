"""A real GPT-Live call over WebRTC for the opt-in live interview tests.

The call runs through the production path: the voice connection route, the
sideband Job with its activity feed, GPT-6.1 Sol delegations, Jev judgements, and
visual-command dispatch, against the test database. `aiortc` plays the session
tab and speaks lines synthesized with OpenAI's text-to-speech; the helpers commit
the browser activity a participant produces and read back what the agent said,
delegated, and showed.
"""


from __future__ import annotations

import asyncio
import fractions
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import pytest
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaBlackhole
from aiortc.mediastreams import AUDIO_PTIME, MediaStreamError, MediaStreamTrack
from av import AudioFrame
from openai import AsyncOpenAI
from utrace_contracts.domain.lifecycle import (
    InterviewPhase,
    InterviewSessionState,
    ProductSignalState,
)

from app.auth.principals import ParticipantPrincipal
from app.connectors.openai_live import SidebandConnection
from app.connectors.openai_roles import OpenAIRole, openai_gateway_for
from app.connectors.typesafe_jev import JevClient
from app.db.pool import Database
from app.execution.jobs import ClaimedJob, claim_job
from app.ids import uuid7
from app.ingestion.target_registry import IngestionTargetRegistry
from app.interview.handlers import run_voice_sideband_with
from app.interview.ports import RegisteredTarget
from app.interview.settings import (
    get_interview_settings,
    get_jev_settings,
    get_openai_voice_settings,
)
from app.interview.voice.adapter import build_voice_adapter
from app.interview.voice.connections import VOICE_SIDEBAND, open_voice_connection
from app.interview.voice.delegation import DelegationRunner
from tests.factories import CaseFixture, create_case, create_interview_session
from tests.helpers import record_session_readiness
from tests.interview.fixtures import (
    ObservedDocument,
    create_binding,
    create_preview_allocation,
    link_installation,
    observe_document,
    record_observation,
    record_registry_change,
)
from tests.supabase_auth import SignedInUser

_SAMPLE_RATE: Final[int] = 24_000
_FRAME_SAMPLES: Final[int] = int(AUDIO_PTIME * _SAMPLE_RATE)
_FRAME_BYTES: Final[int] = _FRAME_SAMPLES * 2
_DEVICE_KEY: Final[str] = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEtoolstestkeyfortheinstallationrecord"
_QUIET_SECONDS: Final[float] = 2.0
REPLY_DEADLINE_SECONDS: Final[float] = 45.0
#: How long a participant waits in silence before taking the agent's turn as over.
_TURN_OVER_SECONDS: Final[float] = 6.0
INTRO_REPLY_SECONDS: Final[float] = 6.0

FILTERS: Final[Mapping[str, str]] = {
    "date_range": "last_30_days",
    "channels": "TikTok",
    "segments": "Women 25-34",
    "review_topics": "Sizing",
    "sentiments": "negative",
    "products": "Aero High-Rise Legging",
}
#: What each filter control shows once the filters are applied.
APPLIED_LABELS: Final[Mapping[str, str]] = {
    "date_range": "Last 30 days",
    "channel": "TikTok",
    "segment": "Women 25-34",
    "review_topic": "Sizing",
    "sentiment": "Negative",
    "product": "Aero High-Rise Legging",
}


class Microphone(MediaStreamTrack):
    """Sends silence, and each queued line in turn, paced to real time."""

    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._queue: list[bytes] = []
        self._current = b""
        self._offset = 0
        self._start = 0.0
        self._timestamp: int | None = None
        self.finished: asyncio.Event = asyncio.Event()
        self.started_at = 0.0

    def say(self, pcm: bytes) -> None:
        """Queue one line; `finished` is set once its last frame is sent."""
        self.finished.clear()
        self._queue.append(pcm)

    async def recv(self) -> AudioFrame:
        if self.readyState != "live":
            raise MediaStreamError
        if self._timestamp is None:
            self._start = time.time()
            self._timestamp = 0
        else:
            self._timestamp += _FRAME_SAMPLES
            await asyncio.sleep(self._start + self._timestamp / _SAMPLE_RATE - time.time())
        if self._offset >= len(self._current) and self._queue:
            self._current, self._offset = self._queue.pop(0), 0
            self.started_at = time.time()
        chunk = b""
        if self._offset < len(self._current):
            chunk = self._current[self._offset : self._offset + _FRAME_BYTES]
            self._offset += _FRAME_BYTES
            if self._offset >= len(self._current):
                self.finished.set()
        frame = AudioFrame(format="s16", layout="mono", samples=_FRAME_SAMPLES)
        frame.planes[0].update(chunk.ljust(frame.planes[0].buffer_size, b"\0"))
        frame.pts = self._timestamp
        frame.sample_rate = _SAMPLE_RATE
        frame.time_base = fractions.Fraction(1, _SAMPLE_RATE)
        return frame


@dataclass
class Report:
    """What each hop took, in seconds, and what the agent said."""

    appends: list[tuple[float, str, str]] = field(default_factory=list)
    character: list[tuple[float, str]] = field(default_factory=list)
    lines: dict[str, float] = field(default_factory=dict)

    def first_append_after(self, kind: str, after: float) -> float | None:
        return next((at for at, k, _ in self.appends if k == kind and at >= after), None)

    def first_activity_after(self, after: float) -> float | None:
        """When product activity first reached the agent, as thinking or, when it
        answers an awaited acknowledgement, as commentary."""
        return next(
            (
                at
                for at, _, content in self.appends
                if at >= after and content.startswith("In the preview, the person just did this")
            ),
            None,
        )

    def first_character_after(self, state: str, after: float) -> float | None:
        return next((at for at, s in self.character if s == state and at >= after), None)


async def speech(client: AsyncOpenAI, text: str) -> bytes:
    speech = await client.audio.speech.create(
        model="gpt-4o-mini-tts", voice="coral", input=text, response_format="pcm"
    )
    return speech.content


async def agent_reply(
    database: Database, *, session_id: UUID, after: float, deadline_seconds: float
) -> tuple[float, str] | None:
    """Wait for the agent's next reply after a moment: when it began and its text.

    Returns `None` when the agent stays silent until the deadline.
    """
    deadline = time.time() + deadline_seconds
    first: float | None = None
    last_count = 0
    last_change = time.time()
    text = ""
    # The deadline bounds when a reply must start; one that has started may finish.
    while time.time() < (deadline if first is None else first + REPLY_DEADLINE_SECONDS):
        async with database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select fragment_text, recorded_at from public.voice_transcript_fragments
                where interview_session_id = %s and speaker = 'agent' and recorded_at > %s
                order by start_ms
                """,
                (session_id, datetime.fromtimestamp(after, tz=UTC)),
            )
            rows = await cur.fetchall()
        if rows:
            if first is None:
                first = min(row["recorded_at"].timestamp() for row in rows)
            if len(rows) != last_count:
                last_count, last_change = len(rows), time.time()
                text = "".join(str(row["fragment_text"]) for row in rows)
            elif time.time() - last_change >= _QUIET_SECONDS:
                return first, text
        await asyncio.sleep(0.1)
    if first is None:
        return None
    raise AssertionError(f"the agent did not finish replying; heard so far {text!r}")


async def required_reply(
    database: Database, session_id: UUID, *, after: float
) -> tuple[float, str]:
    reply = await agent_reply(
        database, session_id=session_id, after=after, deadline_seconds=REPLY_DEADLINE_SECONDS
    )
    if reply is None:
        raise AssertionError("the agent never answered")
    return reply


async def whole_turn(database: Database, session_id: UUID, *, after: float) -> tuple[float, str]:
    """Wait for the agent's whole turn, through any filler before a delegated answer, as
    a participant would: when it began and everything it said."""
    started, text = await required_reply(database, session_id, after=after)
    while True:
        more = await agent_reply(
            database, session_id=session_id, after=time.time() - 0.5, deadline_seconds=_TURN_OVER_SECONDS
        )
        if more is None:
            return started, text
        text += more[1]


async def watch_character(database: Database, session_id: UUID, report: Report) -> None:
    """Record each change of the published character state with when it was seen."""
    previous = None
    while True:
        async with database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "select projection->'character'->>'state' as state "
                "from public.session_projections where session_id = %s",
                (session_id,),
            )
            row = await cur.fetchone()
        state = None if row is None else row["state"]
        if state != previous:
            report.character.append((time.time(), str(state)))
            previous = state
        await asyncio.sleep(0.05)


async def set_progress(database: Database, session_id: UUID, progress: str) -> None:
    """Move the session's implementation progress, as an Implementation Attempt would."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.interview_sessions set implementation_progress = %s where id = %s",
            (progress, session_id),
        )


async def claim_sideband_job(database: Database) -> ClaimedJob:
    for _ in range(50):
        claimed = await claim_job(
            database, job_types=[VOICE_SIDEBAND], worker_id="realtime-e2e", lease_seconds=300
        )
        if claimed is not None:
            return claimed
        await asyncio.sleep(0.1)
    raise AssertionError("the voice connection queued no sideband Job")


async def dispatches(database: Database, session_id: UUID) -> list[Mapping[str, object]]:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select dispatches.created_at, dispatches.targets,
                   commands.payload->'scene'->'steps'->0->>'step' as step
            from public.visual_command_dispatches as dispatches
            join public.commands on commands.id = dispatches.command_id
            where dispatches.interview_session_id = %s
            order by dispatches.created_at
            """,
            (session_id,),
        )
        return [dict(row) for row in await cur.fetchall()]


async def words(
    database: Database, session_id: UUID, *, after: float, before: float
) -> list[tuple[float, str, str]]:
    """Both speakers' transcript fragments between two moments, with when each arrived."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select recorded_at, speaker, fragment_text from public.voice_transcript_fragments
            where interview_session_id = %s and recorded_at between %s and %s
            order by recorded_at
            """,
            (
                session_id,
                datetime.fromtimestamp(after, tz=UTC),
                datetime.fromtimestamp(before, tz=UTC),
            ),
        )
        return [
            (row["recorded_at"].timestamp(), str(row["speaker"]), str(row["fragment_text"]))
            for row in await cur.fetchall()
        ]


async def delegations(database: Database, session_id: UUID) -> list[Mapping[str, object]]:
    """Each delegation: when it arrived, when it settled, and what answered it."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select created_at, settled_at, state, finding, refusal_reason
            from public.voice_delegations where interview_session_id = %s
            order by created_at
            """,
            (session_id,),
        )
        return [dict(row) for row in await cur.fetchall()]


async def prepare_case(
    database: Database, auth_users: dict[str, SignedInUser]
) -> tuple[CaseFixture, UUID, ObservedDocument]:
    """A live session in its greeting, with the preview open and its filters registered."""
    case = await create_case(
        database,
        name=f"realtime-{uuid7().hex[:8]}",
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.interview_sessions set phase = %s where id = %s",
            (InterviewPhase.GREETING.value, session_id),
        )
    allocation_id = await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )
    installation_id = await link_installation(
        database,
        utrace_user_id=case.utrace_user_id,
        device_public_key_spki_b64u=_DEVICE_KEY,
        protocol_version="1.0",
    )
    _, attachment_id = await create_binding(
        database,
        case=case,
        interview_session_id=session_id,
        extension_installation_id=installation_id,
        preview_runtime_allocation_id=allocation_id,
    )
    await record_session_readiness(
        database, interview_session_id=session_id, conversational=True, observation=True
    )
    targets = tuple(
        RegisteredTarget(
            target_id=f"filter-{name}",
            target_revision=1,
            description=f'conversion_filter_{name} showing "All"',
            placement="top left of the screen",
            role="button",
            visible=True,
        )
        for name in ("date_range", "channel", "segment", "review_topic", "sentiment", "product")
    )
    document = await observe_document(
        database,
        case=case,
        interview_session_id=session_id,
        preview_runtime_allocation_id=allocation_id,
        document_attachment_id=attachment_id,
        targets=targets,
    )
    return case, session_id, document


async def apply_filters(database: Database, document: ObservedDocument) -> None:
    """Commit what applying the demo's filters produces: a click, the milestone, and
    each filter control now showing its applied value."""
    channel = RegisteredTarget(
        target_id="filter-channel",
        target_revision=1,
        description='conversion_filter_channel showing "All"',
        placement="top left of the screen",
        role="button",
        visible=True,
    )
    await record_observation(
        database,
        document=document,
        export_sequence=2,
        observation_type="interaction.activation",
        target=channel,
        payload={"role": "button", "actor": "user", "activation_kind": "pointer"},
    )
    await record_observation(
        database,
        document=document,
        export_sequence=3,
        observation_type="product.milestone",
        target=None,
        payload={
            "milestone_id": "conversion_filters_applied",
            "attributes": [{"name": name, "value": {"value": value}} for name, value in FILTERS.items()],
        },
    )
    for sequence, (name, label) in enumerate(APPLIED_LABELS.items(), start=4):
        await record_registry_change(
            database,
            document=document,
            export_sequence=sequence,
            change="updated",
            target=RegisteredTarget(
                target_id=f"filter-{name}",
                target_revision=1,
                description=f'conversion_filter_{name} showing "{label}"',
                placement="top left of the screen",
                role="button",
                visible=True,
            ),
        )


async def apply_filter(
    database: Database,
    document: ObservedDocument,
    *,
    export_sequence: int,
    control: str,
    label: str,
    applied: Mapping[str, str],
) -> int:
    """Commit one filter choice: the click, the milestone with every filter applied so
    far, and the control now showing its value. Returns the next export sequence."""
    target = RegisteredTarget(
        target_id=f"filter-{control}",
        target_revision=1,
        description=f'conversion_filter_{control} showing "All"',
        placement="top left of the screen",
        role="button",
        visible=True,
    )
    await record_observation(
        database,
        document=document,
        export_sequence=export_sequence,
        observation_type="interaction.activation",
        target=target,
        payload={"role": "button", "actor": "user", "activation_kind": "pointer"},
    )
    await record_observation(
        database,
        document=document,
        export_sequence=export_sequence + 1,
        observation_type="product.milestone",
        target=None,
        payload={
            "milestone_id": "conversion_filters_applied",
            "attributes": [
                {"name": name, "value": {"value": value}} for name, value in applied.items()
            ],
        },
    )
    await record_registry_change(
        database,
        document=document,
        export_sequence=export_sequence + 2,
        change="updated",
        target=target.model_copy(
            update={"description": f'conversion_filter_{control} showing "{label}"'}
        ),
    )
    return export_sequence + 3


@dataclass(frozen=True)
class LiveCall:
    """A connected call: the case, its live session, the preview page, and the microphone."""

    case: CaseFixture
    session_id: UUID
    document: ObservedDocument
    microphone: Microphone
    connected_at: float


async def say(microphone: Microphone, pcm: bytes) -> tuple[float, float]:
    """Speak one line and return when it started and ended."""
    microphone.say(pcm)
    await asyncio.sleep(0.2)
    started = microphone.started_at
    await microphone.finished.wait()
    return started, time.time()


@asynccontextmanager
async def live_call(
    database: Database,
    auth_users: dict[str, SignedInUser],
    *,
    report: Report,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[LiveCall]:
    """Open a live session's call and run its sideband until the block exits.

    Every append the application sends the provider is recorded in `report`.
    """
    for name in ("append_thinking", "append_commentary", "append_instructions"):
        original = getattr(SidebandConnection, name)

        def recorder(original=original, name=name):
            async def record(
                self: SidebandConnection, *, content: str, delegation_id: str | None
            ) -> None:
                report.appends.append((time.time(), name, content))
                await original(self, content=content, delegation_id=delegation_id)

            return record

        monkeypatch.setattr(SidebandConnection, name, recorder())

    case, session_id, document = await prepare_case(database, auth_users)
    microphone = Microphone()
    peer = RTCPeerConnection()
    blackhole = MediaBlackhole()
    connected = asyncio.Event()
    peer.addTrack(microphone)
    peer.createDataChannel("oai-events")

    @peer.on("track")
    def on_track(remote: MediaStreamTrack) -> None:
        blackhole.addTrack(remote)

    @peer.on("connectionstatechange")
    def on_state() -> None:
        if peer.connectionState == "connected":
            connected.set()

    jev_settings = get_jev_settings()
    adapter = build_voice_adapter(settings=get_openai_voice_settings())
    reasoning = openai_gateway_for(OpenAIRole.VOICE_DELEGATION)
    jev = JevClient(
        api_key=jev_settings.api_key,
        model_id=jev_settings.model_id,
        timeout_seconds=jev_settings.timeout_seconds,
    )
    watcher = asyncio.create_task(watch_character(database, session_id, report))
    try:
        await peer.setLocalDescription(await peer.createOffer())
        async with adapter, reasoning, jev:
            opened = await open_voice_connection(
                database,
                adapter,
                participant=ParticipantPrincipal(
                    auth_user_id=uuid7(),
                    utrace_user_id=case.utrace_user_id,
                    client_user_id=case.client_user_id,
                    client_id=case.client_id,
                ),
                interview_session_id=session_id,
                sdp_offer=peer.localDescription.sdp,
            )
            await peer.setRemoteDescription(
                RTCSessionDescription(sdp=opened.sdp_answer, type="answer")
            )
            await blackhole.start()
            claimed = await claim_sideband_job(database)
            sideband = asyncio.create_task(
                run_voice_sideband_with(
                    database,
                    claimed,
                    adapter=adapter,
                    delegations=DelegationRunner(
                        database=database,
                        reasoning=reasoning,
                        jev=jev,
                        targets=IngestionTargetRegistry(database),
                        settings=get_interview_settings(),
                    ),
                )
            )
            await asyncio.wait_for(connected.wait(), timeout=20)
            yield LiveCall(
                case=case,
                session_id=session_id,
                document=document,
                microphone=microphone,
                connected_at=time.time(),
            )
            await adapter.sessions.close_session(provider_session_id=opened.provider_session_ref)
            await asyncio.wait_for(sideband, timeout=30)
    finally:
        watcher.cancel()
        await blackhole.stop()
        await peer.close()
