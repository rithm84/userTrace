"""The voice agent's session: one `interview.voice_agent` Job, from its first run to its end.

The voice agent runs on Pipecat Cloud, whose machines carry the call's WebRTC media
to Daily; Supabase Compute's network does not. When the API opens a voice
connection it starts one session for the connection's Job
(`app.connectors.pipecat_cloud`). The session claims that Job by its id and runs
it through the worker's own run path (`Worker.execute`), so the Job's lease,
heartbeats, typed failures, and run budget apply as they do to every Job. A run
that fails returns the Job to the queue with its backoff; the session waits and
claims it again, until the Job completes, has no run left, or is leased by
another session started for it.

A Pipecat Cloud instance is kept warm and serves one session at a time, so it
opens its database pool once (`prepare_instance`, which the platform's readiness
check runs before any session) and every session reuses it. A local run of the
API starts sessions in its own process instead (`InProcessVoiceAgents`).
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from logging import INFO
from typing import Final
from uuid import UUID

from app.connectors.typesafe_jev import JevClient
from app.db.pool import Database
from app.execution.handlers import build_exhausted_handlers
from app.execution.jobs import ClaimedJob, claim_job_by_id, next_run_at
from app.execution.worker import Worker, WorkerConfig, default_config
from app.ids import uuid7
from app.ingestion.target_registry import IngestionTargetRegistry
from app.interview.settings import get_interview_settings, get_jev_settings, get_voice_settings
from app.interview.voice.adapter import build_daily_rooms
from app.interview.voice.agent import run_voice_agent
from app.interview.voice.call_record import subject_from_job
from app.interview.voice.connections import VOICE_AGENT
from app.interview.voice.judge import VoiceJudge
from app.logging import configure_logging, get_logger
from app.settings import get_settings

__all__ = ["InProcessVoiceAgents", "prepare_instance", "run_voice_session"]

_log = get_logger(__name__)

#: The shortest wait before trying a ready Job again, while another session's
#: agent still holds the same interview's concurrency key.
_MIN_RETRY_WAIT_SECONDS: Final[float] = 0.25


@dataclass(frozen=True, slots=True)
class _Instance:
    database: Database
    worker: Worker
    config: WorkerConfig


_instance: list[_Instance] = []
_instance_lock = asyncio.Lock()


async def prepare_instance() -> None:
    """Open this process's database pool and Job runner once, before its first session."""
    async with _instance_lock:
        if _instance:
            return
        configure_logging(INFO)
        settings = get_settings()
        database = Database(
            dsn=settings.database_url,
            min_size=settings.database_pool_min_size,
            max_size=settings.database_pool_max_size,
        )
        await database.open()
        config = default_config()
        _instance.append(
            _Instance(
                database=database,
                worker=Worker(
                    database, config, {VOICE_AGENT: run_voice_agent_job}, build_exhausted_handlers()
                ),
                config=config,
            )
        )
        _log.info("voice_agent_instance_ready")


async def run_voice_session(*, voice_agent_job_id: UUID, session_id: str) -> None:
    """Run the named Job, and each retry of it, until no run of it is left to claim."""
    await prepare_instance()
    instance = _instance[0]
    worker_id = f"voice-session:{session_id}"
    while True:
        claimed = await claim_job_by_id(
            instance.database,
            job_id=voice_agent_job_id,
            worker_id=worker_id,
            lease_seconds=instance.config.lease_seconds,
        )
        if claimed is not None:
            await instance.worker.execute(claimed)
            continue
        due = await next_run_at(instance.database, job_id=voice_agent_job_id)
        if due is None:
            _log.info(
                "voice_session_finished",
                session_id=session_id,
                voice_agent_job_id=str(voice_agent_job_id),
            )
            return
        wait = (due - datetime.now(tz=UTC)).total_seconds()
        await asyncio.sleep(max(wait, _MIN_RETRY_WAIT_SECONDS))


async def run_voice_agent_job(database: Database, claimed: ClaimedJob) -> None:
    """Join one connection's call as the voice agent with configured providers."""
    voice = get_voice_settings()
    jev_settings = get_jev_settings()
    rooms = build_daily_rooms(settings=voice)
    jev = JevClient(
        api_key=jev_settings.api_key,
        model_id=jev_settings.model_id,
        timeout_seconds=jev_settings.timeout_seconds,
    )
    subject = await subject_from_job(database, claimed)
    async with rooms, jev:
        await run_voice_agent(
            database,
            rooms=rooms,
            judge=VoiceJudge(database=database, jev=jev, targets=IngestionTargetRegistry(database)),
            voice=voice,
            interview=get_interview_settings(),
            subject=subject,
        )
    _log.info(
        "voice_agent_job_finished",
        job_id=str(claimed.job_id),
        interview_session_id=str(subject.interview_session_id),
    )


class InProcessVoiceAgents:
    """Runs each agent session as a task of this process, for a local run of the API."""

    def __init__(self) -> None:
        self._sessions: set[asyncio.Task[None]] = set()

    async def start(self, *, voice_agent_job_id: UUID) -> None:
        """Start the session in the background; its failures are its Job's to record."""
        session = asyncio.create_task(
            run_voice_session(voice_agent_job_id=voice_agent_job_id, session_id=str(uuid7()))
        )
        self._sessions.add(session)
        session.add_done_callback(self._sessions.discard)

    async def aclose(self) -> None:
        """Nothing to release: running sessions end with their Jobs."""
