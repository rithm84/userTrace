"""A real voice call over Daily for the opt-in live interview tests.

The call runs through the production path: the voice connection route, the voice
agent Job with its activity feed, cues, tools, and Jev judgements, and
visual-command dispatch, against the test database. A Daily participant plays the
session tab and speaks lines synthesized with OpenAI's text-to-speech; the helpers
commit the browser activity a participant produces and read back what the agent
said, was told, and showed.
"""


from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import pytest
from daily import CallClient, CustomAudioSource, CustomAudioTrack, Daily
from openai import AsyncOpenAI
from pipecat.transports.daily.transport import DailyTransportClient
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    ImplementationAttemptState,
    InterviewPhase,
    InterviewSessionState,
    ProductSignalState,
)

from app.auth.principals import ParticipantPrincipal
from app.connectors.typesafe_jev import JevClient
from app.db.pool import Database
from app.db.rows import as_uuid
from app.execution.jobs import ClaimedJob, claim_job
from app.ids import uuid7
from app.ingestion.target_registry import IngestionTargetRegistry
from app.interview.ports import RegisteredTarget
from app.interview.readiness import ExtensionReadiness, record_observation_facts
from app.interview.settings import get_interview_settings, get_jev_settings, get_voice_settings
from app.interview.voice import agent as voice_agent
from app.interview.voice.adapter import build_voice_adapter
from app.interview.voice.call_record import subject_from_job
from app.interview.voice.connections import VOICE_AGENT, open_voice_connection
from app.interview.voice.judge import VoiceJudge
from app.interview.voice.turn_taking import PipelineChannel
from tests.factories import CaseFixture, create_case, create_interview_session
from tests.helpers import record_session_readiness
from tests.interview.fixtures import (
    ObservedDocument,
    create_binding,
    create_preview_allocation,
    link_installation,
    observe_document,
    observe_next_document,
    record_observation,
    record_registry_change,
)
from tests.supabase_auth import SignedInUser

_SAMPLE_RATE: Final[int] = 24_000
_FRAME_SECONDS: Final[float] = 0.02
_FRAME_BYTES: Final[int] = int(_FRAME_SECONDS * _SAMPLE_RATE) * 2
_JOIN_SECONDS: Final[float] = 30.0
#: Above any revision the conversation reaches, so the agreement's bundle is never stale.
_AGREEMENT_BUNDLE_REVISION: Final[int] = 1000
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


class Microphone:
    """The participant's microphone in the call: each queued line in turn, in real time.

    Between lines Daily sends silence by itself (`auto_silence`).
    """

    def __init__(self) -> None:
        self.source = CustomAudioSource(_SAMPLE_RATE, 1, True)
        self.track = CustomAudioTrack(self.source)
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.finished: asyncio.Event = asyncio.Event()
        self.started_at = 0.0

    def say(self, pcm: bytes) -> None:
        """Queue one line; `finished` is set once its last frame is sent."""
        self.finished.clear()
        self._queue.put_nowait(pcm)

    async def run(self) -> None:
        """Send each queued line as it comes, paced to real time."""
        while True:
            pcm = await self._queue.get()
            self.started_at = start = time.time()
            for index, offset in enumerate(range(0, len(pcm), _FRAME_BYTES)):
                await asyncio.sleep(max(0.0, start + index * _FRAME_SECONDS - time.time()))
                chunk = pcm[offset : offset + _FRAME_BYTES]
                self.source.write_frames(
                    chunk.ljust(_FRAME_BYTES, b"\0"), completion=lambda _: None
                )
            if self._queue.empty():
                self.finished.set()


@dataclass
class Report:
    """What each hop took, in seconds, and what the agent said."""

    appends: list[tuple[float, str, str]] = field(default_factory=list)
    character: list[tuple[float, str]] = field(default_factory=list)
    lines: dict[str, float] = field(default_factory=dict)

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
    """Wait for the agent's whole turn, through any pause mid-turn, as a participant
    would: when it began and everything it said."""
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


async def claim_agent_job(database: Database) -> ClaimedJob:
    for _ in range(50):
        claimed = await claim_job(
            database, job_types=[VOICE_AGENT], worker_id="realtime-e2e", lease_seconds=300
        )
        if claimed is not None:
            return claimed
        await asyncio.sleep(0.1)
    raise AssertionError("the voice connection queued no voice agent Job")


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
    """Both speakers' transcript fragments between two moments, with when each arrived.

    Fragments come in the call's order: words recorded in the same instant can
    commit out of order, so they are ordered by when they played.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select recorded_at, speaker, fragment_text from public.voice_transcript_fragments
            where interview_session_id = %s and recorded_at between %s and %s
            order by start_ms, recorded_at
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


async def prepare_case(
    database: Database, auth_users: dict[str, SignedInUser]
) -> tuple[CaseFixture, UUID, ObservedDocument]:
    """A live session in its greeting, with the preview open and its filters registered."""
    case = await create_case(
        database,
        # The scenario's client, so the agent names it as it would in the demo.
        name="Shopalytics",
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
    # The dashboard opens on Last 30 days, with every other filter at All.
    targets = tuple(
        RegisteredTarget(
            target_id=f"filter-{name}",
            target_revision=1,
            description=f'conversion_filter_{name} showing '
            f'"{"Last 30 days" if name == "date_range" else "All"}"',
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


def _daily_initialized() -> None:
    """Initialize daily-python once per process, as the voice agent's transport does."""
    if not DailyTransportClient._daily_initialized:
        DailyTransportClient._daily_initialized = True
        Daily.init()


async def _completed(start: Callable[[Callable[..., None]], None]) -> tuple[object, ...]:
    """Run one daily-python call whose `completion` fires on Daily's own thread."""
    loop = asyncio.get_running_loop()
    done: asyncio.Future[tuple[object, ...]] = loop.create_future()

    def complete(*results: object) -> None:
        loop.call_soon_threadsafe(done.set_result, results)

    start(complete)
    return await done


@asynccontextmanager
async def live_call(
    database: Database,
    auth_users: dict[str, SignedInUser],
    *,
    report: Report,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[LiveCall]:
    """Open a live session's call, join it as the participant, and run the voice agent.

    Everything the application tells the agent is recorded in `report` as `say` or
    `note`.
    """
    original = PipelineChannel._append

    async def record(self: PipelineChannel, content: str, *, speak: bool) -> None:
        report.appends.append((time.time(), "say" if speak else "note", content))
        await original(self, content, speak=speak)

    monkeypatch.setattr(PipelineChannel, "_append", record)

    case, session_id, document = await prepare_case(database, auth_users)
    voice = get_voice_settings()
    jev_settings = get_jev_settings()
    adapter = build_voice_adapter(settings=voice)
    jev = JevClient(
        api_key=jev_settings.api_key,
        model_id=jev_settings.model_id,
        timeout_seconds=jev_settings.timeout_seconds,
    )
    _daily_initialized()
    microphone = Microphone()
    client = CallClient()
    watcher = asyncio.create_task(watch_character(database, session_id, report))
    sending = asyncio.create_task(microphone.run())
    agent: asyncio.Task[None] | None = None
    try:
        async with adapter, jev:
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
            )
            claimed = await claim_agent_job(database)
            agent = asyncio.create_task(
                voice_agent.run_voice_agent(
                    database,
                    rooms=adapter.rooms,
                    judge=VoiceJudge(
                        database=database, jev=jev, targets=IngestionTargetRegistry(database)
                    ),
                    voice=voice,
                    interview=get_interview_settings(),
                    subject=await subject_from_job(database, claimed),
                )
            )
            joined = await asyncio.wait_for(
                _completed(
                    lambda complete: client.join(
                        opened.room_url,
                        opened.participant_token,
                        client_settings={
                            "inputs": {
                                "camera": {"isEnabled": False},
                                "microphone": {
                                    "isEnabled": True,
                                    "settings": {"customTrack": {"id": microphone.track.id}},
                                },
                            },
                            "publishing": {"microphone": {"isPublishing": True}},
                        },
                        completion=complete,
                    )
                ),
                timeout=_JOIN_SECONDS,
            )
            if joined[-1] is not None:
                raise AssertionError(f"the participant could not join the call: {joined!r}")
            yield LiveCall(
                case=case,
                session_id=session_id,
                document=document,
                microphone=microphone,
                connected_at=time.time(),
            )
            await _completed(lambda complete: client.leave(completion=complete))
            await asyncio.wait_for(agent, timeout=30)
    finally:
        client.release()
        for task in (watcher, sending, *(() if agent is None else (agent,))):
            task.cancel()
        await asyncio.gather(
            watcher, sending, *(() if agent is None else (agent,)), return_exceptions=True
        )


async def record_agreement(
    database: Database,
    *,
    case: CaseFixture,
    session_id: UUID,
    outcome: str,
    criteria: Sequence[str],
) -> None:
    """Commit what the agent recording the agreement leaves: the criteria and the agreement."""
    bundle_id = uuid7()
    criterion_ids = [uuid7() for _ in criteria]
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.context_bundles (
                id, client_id, product_signal_id, interview_session_id, purpose,
                context_revision, cutoff_at, budget_items, included_items,
                omitted_items, dependencies, digest
            )
            values (%s, %s, %s, %s, 'voice_delegation', %s, now(), 1, 0, 0, %s, 'digest')
            """,
            (
                bundle_id,
                case.client_id,
                case.product_signal_id,
                session_id,
                _AGREEMENT_BUNDLE_REVISION,
                Jsonb({}),
            ),
        )
        for ordinal, (criterion_id, statement) in enumerate(
            zip(criterion_ids, criteria, strict=True)
        ):
            await cur.execute(
                """
                insert into public.validation_criteria (
                    id, client_id, product_signal_id, criteria_revision, ordinal,
                    statement, observability
                )
                values (%s, %s, %s, 1, %s, %s, 'observed')
                """,
                (criterion_id, case.client_id, case.product_signal_id, ordinal, statement),
            )
        await cur.execute(
            """
            insert into public.acceptance_criteria_agreements (
                id, client_id, product_signal_id, interview_session_id,
                acceptance_criteria_revision, agreed_scope, criterion_ids,
                supporting_evidence_ids, context_bundle_id
            )
            values (%s, %s, %s, %s, 1, %s, %s, '{}', %s)
            """,
            (
                uuid7(),
                case.client_id,
                case.product_signal_id,
                session_id,
                Jsonb({"intended_outcome": outcome, "workflow_ref": "saved_conversion_views"}),
                criterion_ids,
                bundle_id,
            ),
        )


FILTER_CONTROLS: Final[tuple[str, ...]] = (
    "date_range",
    "channel",
    "segment",
    "review_topic",
    "sentiment",
    "product",
)
#: The controls the new version adds, by declared identity, and what each shows.
NEW_CONTROLS: Final[Mapping[str, str]] = {
    "saved_view_save_button": "Save view",
    "saved_view_selector": "Saved views",
}


async def session_allocation(database: Database, session_id: UUID) -> UUID:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select preview_runtime_allocation_id from public.interview_sessions where id = %s",
            (session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AssertionError("the live session has no preview allocation")
    return as_uuid(row, "preview_runtime_allocation_id")


async def open_page(
    database: Database,
    call: LiveCall,
    *,
    allocation_id: UUID,
    controls: Sequence[str],
    binding_allocation: UUID | None,
) -> ObservedDocument:
    """Commit a newly loaded conversion dashboard: its page load, controls, and first render.

    The extension's binding moves to the new document, as it does when a page loads.
    """
    attachment_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.extension_bindings
            set document_attachment_id = %s,
                preview_runtime_allocation_id = coalesce(%s, preview_runtime_allocation_id)
            where interview_session_id = %s and status = 'bound'
            """,
            (attachment_id, binding_allocation, call.session_id),
        )
    document = await observe_next_document(
        database,
        case=call.case,
        earlier=call.document,
        preview_runtime_allocation_id=allocation_id,
        document_attachment_id=attachment_id,
        targets=tuple(
            RegisteredTarget(
                target_id=f"filter-{name}" if name in FILTER_CONTROLS else name,
                target_revision=1,
                description=(
                    f'conversion_filter_{name} showing "All"'
                    if name in FILTER_CONTROLS
                    else f'{name} showing "{NEW_CONTROLS[name]}"'
                ),
                placement=(
                    "top left of the screen" if name in FILTER_CONTROLS else "top right of the screen"
                ),
                role="button",
                visible=True,
            )
            for name in controls
        ),
    )
    milestones: tuple[Mapping[str, object], ...] = (
        {
            "milestone_id": "conversion_filters_applied",
            "attributes": [
                {"name": name, "value": {"value": value}}
                for name, value in (
                    ("date_range", "last_30_days"),
                    ("channels", "all"),
                    ("segments", "all"),
                    ("review_topics", "all"),
                    ("sentiments", "all"),
                    ("products", "all"),
                )
            ],
        },
        {
            "milestone_id": "conversion_render_completed",
            "attributes": [
                {"name": "surface", "value": {"value": "conversion_kpis"}},
                {"name": "status", "value": {"value": "rendered"}},
            ],
        },
        {
            "milestone_id": "conversion_render_completed",
            "attributes": [
                {"name": "surface", "value": {"value": "conversion_trend"}},
                {"name": "status", "value": {"value": "rendered"}},
            ],
        },
    )
    for sequence, payload in enumerate(milestones, start=2):
        await record_observation(
            database,
            document=document,
            export_sequence=sequence,
            observation_type="product.milestone",
            target=None,
            payload=payload,
        )
    return document


async def observe_browser(database: Database, session_id: UUID, *, ready: bool) -> None:
    """The browser SDK leaving the old page as the tab reloads, or reporting from the new one."""
    await record_observation_facts(
        database,
        interview_session_id=session_id,
        preview_allocation_ready=True,
        extension_readiness=ExtensionReadiness.READY,
        browser_sdk_ready=ready,
        server_sdk_ready=True,
        trace_correlation_ready=True,
    )


async def ready_candidate(database: Database, call: LiveCall, *, attempt_id: UUID) -> UUID:
    """Commit what a finished attempt leaves: its candidate, a ready Preview Version.

    The session's preview now serves the candidate, and the case awaits validation.
    Returns the candidate's allocation.
    """
    case = call.case
    version_id, allocation_id = uuid7(), uuid7()
    seed = "sha256:seed-realtime"
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.implementation_attempts set state = %s where id = %s",
            (ImplementationAttemptState.CANDIDATE_READY.value, attempt_id),
        )
        await cur.execute(
            """
            insert into public.preview_versions (
                id, client_id, product_signal_id, implementation_attempt_id, state,
                source_commit, source_digest, build_digest, base_revision,
                seed_manifest_digest, ready_at
            )
            values (%s, %s, %s, %s, 'ready', 'candidate', 'sha256:source',
                    'sha256:build', 'base', %s, now())
            """,
            (version_id, case.client_id, case.product_signal_id, attempt_id, seed),
        )
        await cur.execute(
            """
            insert into public.preview_runtime_allocations (
                id, client_id, product_signal_id, allocation_kind, pool_role, state,
                interview_session_id, preview_version_id, seed_manifest_digest,
                authenticated_identity, origin_host, fencing_token,
                pinned_configuration_release_id
            )
            values (%s, %s, %s, 'candidate', 'candidate', 'bound', %s, %s, %s,
                    'maya@dudulemon.test', %s, utrace.next_fencing_token(), %s)
            """,
            (
                allocation_id,
                case.client_id,
                case.product_signal_id,
                call.session_id,
                version_id,
                seed,
                f"{allocation_id.hex[:20]}.preview.utrace.dev",
                case.configuration_release_id,
            ),
        )
        await cur.execute(
            "update public.interview_sessions set preview_runtime_allocation_id = %s where id = %s",
            (allocation_id, call.session_id),
        )
        await cur.execute(
            """
            update public.product_signals
            set state = %s, acceptance_criteria_revision = 1, revision = revision + 1
            where id = %s
            """,
            (ProductSignalState.AWAITING_VALIDATION.value, case.product_signal_id),
        )
    return allocation_id
