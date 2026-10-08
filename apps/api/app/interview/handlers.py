"""The durable Jobs the interview runtime owns.

The worker registers these alongside the control plane's own handlers. Each one
builds its provider clients from configuration when it runs, so a worker that
never claims a voice Job never needs voice or TypeSafe credentials, and a missing
variable fails that Job by name instead of the whole process.

Three Jobs:

- `interview.voice_agent` joins one connection's call as the voice agent and holds
  the conversation until the participant leaves.
- `interview.close_provider_session` deletes a call no participant connection may
  use, which is how a superseded or late-arriving call is cleaned up rather than
  left to expire.
- `interview.expire_reconnect_window` closes a session whose lost call was not
  replaced in time.
"""

from collections.abc import Awaitable, Callable, Mapping

from app.connectors.typesafe_jev import JevClient
from app.db.pool import Database
from app.execution.jobs import ClaimedJob
from app.ingestion.target_registry import IngestionTargetRegistry
from app.interview.reconnect import EXPIRE_RECONNECT_WINDOW, expire_reconnect_window
from app.interview.settings import get_interview_settings, get_jev_settings, get_voice_settings
from app.interview.voice.adapter import build_voice_adapter
from app.interview.voice.agent import run_voice_agent, subject_from_job
from app.interview.voice.connections import CLOSE_PROVIDER_SESSION, VOICE_AGENT
from app.interview.voice.judge import VoiceJudge
from app.logging import get_logger

__all__ = ["close_provider_session", "interview_handlers", "run_voice_agent_job"]

_log = get_logger(__name__)

type JobHandler = Callable[[Database, ClaimedJob], Awaitable[None]]


def interview_handlers() -> Mapping[str, JobHandler]:
    """Return this workstream's Job handlers for the worker's registry."""
    return {
        VOICE_AGENT: run_voice_agent_job,
        CLOSE_PROVIDER_SESSION: close_provider_session,
        EXPIRE_RECONNECT_WINDOW: expire_reconnect_window,
    }


async def run_voice_agent_job(database: Database, claimed: ClaimedJob) -> None:
    """Join one connection's call as the voice agent with configured providers."""
    voice = get_voice_settings()
    jev_settings = get_jev_settings()
    adapter = build_voice_adapter(settings=voice)
    jev = JevClient(
        api_key=jev_settings.api_key,
        model_id=jev_settings.model_id,
        timeout_seconds=jev_settings.timeout_seconds,
    )
    subject = await subject_from_job(database, claimed)
    async with adapter, jev:
        await run_voice_agent(
            database,
            rooms=adapter.rooms,
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


async def close_provider_session(database: Database, claimed: ClaimedJob) -> None:
    """Delete one call that no participant connection may use."""
    provider_session_ref = str(claimed.input["provider_session_ref"])
    adapter = build_voice_adapter(settings=get_voice_settings())
    async with adapter:
        await adapter.rooms.delete_room(room_name=provider_session_ref)
    _log.info(
        "voice_provider_session_cleanup_completed",
        job_id=str(claimed.job_id),
        provider_session_ref=provider_session_ref,
        reason=str(claimed.input["reason"]),
    )
