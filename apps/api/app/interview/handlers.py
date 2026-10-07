"""The durable Jobs the interview runtime owns.

The worker registers these alongside the control plane's own handlers. Each one
builds its provider clients from configuration when it runs, so a worker that
never claims a voice Job never needs voice, OpenAI, or TypeSafe credentials, and a
missing variable fails that Job by name instead of the whole process.

Two Jobs:

- `interview.voice_sideband` attaches the backend sideband to one provider
  session and records the conversation until the provider closes it.
- `interview.close_provider_session` closes a provider session no participant
  connection may use, which is how a superseded or late-arriving session is
  cleaned up rather than left to expire.
"""

from collections.abc import Awaitable, Callable, Mapping

from app.connectors.openai_roles import OpenAIRole, openai_gateway_for
from app.connectors.typesafe_jev import JevClient
from app.db.pool import Database
from app.execution.jobs import ClaimedJob
from app.ingestion.target_registry import IngestionTargetRegistry
from app.interview.reconnect import EXPIRE_RECONNECT_WINDOW, expire_reconnect_window
from app.interview.settings import (
    get_interview_settings,
    get_jev_settings,
    get_openai_voice_settings,
)
from app.interview.voice.adapter import GptLiveVoiceAdapter, build_voice_adapter
from app.interview.voice.connections import CLOSE_PROVIDER_SESSION, VOICE_SIDEBAND
from app.interview.voice.delegation import DelegationRunner
from app.interview.voice.sideband import drain_sideband, subject_from_job
from app.logging import get_logger

__all__ = [
    "close_provider_session",
    "interview_handlers",
    "run_voice_sideband",
    "run_voice_sideband_with",
]

_log = get_logger(__name__)

type JobHandler = Callable[[Database, ClaimedJob], Awaitable[None]]


def interview_handlers() -> Mapping[str, JobHandler]:
    """Return this workstream's Job handlers for the worker's registry."""
    return {
        VOICE_SIDEBAND: run_voice_sideband,
        CLOSE_PROVIDER_SESSION: close_provider_session,
        EXPIRE_RECONNECT_WINDOW: expire_reconnect_window,
    }


async def run_voice_sideband(database: Database, claimed: ClaimedJob) -> None:
    """Attach and drain one connection's sideband with configured providers."""
    interview_settings = get_interview_settings()
    jev_settings = get_jev_settings()

    adapter = build_voice_adapter(settings=get_openai_voice_settings())
    reasoning = openai_gateway_for(OpenAIRole.VOICE_DELEGATION)
    jev = JevClient(
        api_key=jev_settings.api_key,
        model_id=jev_settings.model_id,
        timeout_seconds=jev_settings.timeout_seconds,
    )
    async with adapter, reasoning, jev:
        await run_voice_sideband_with(
            database,
            claimed,
            adapter=adapter,
            delegations=DelegationRunner(
                database=database,
                reasoning=reasoning,
                jev=jev,
                targets=IngestionTargetRegistry(database),
                settings=interview_settings,
            ),
        )


async def run_voice_sideband_with(
    database: Database,
    claimed: ClaimedJob,
    *,
    adapter: GptLiveVoiceAdapter,
    delegations: DelegationRunner,
) -> None:
    """Run one sideband Job with already-constructed providers.

    Tests and a process that already owns its provider clients call this
    directly; `run_voice_sideband` is the registered handler that builds them.
    """
    subject = await subject_from_job(database, claimed)
    summary = await drain_sideband(database, adapter, delegations, subject=subject)
    _log.info(
        "voice_sideband_job_finished",
        job_id=str(claimed.job_id),
        interview_session_id=str(subject.interview_session_id),
        fragments=summary.fragments,
        delegations=summary.delegations,
        close_reason=summary.close_reason,
    )


async def close_provider_session(database: Database, claimed: ClaimedJob) -> None:
    """Close one provider session that no participant connection may use."""
    provider_session_ref = str(claimed.input["provider_session_ref"])
    adapter = build_voice_adapter(settings=get_openai_voice_settings())
    async with adapter:
        await adapter.sessions.close_session(provider_session_id=provider_session_ref)
    _log.info(
        "voice_provider_session_cleanup_completed",
        job_id=str(claimed.job_id),
        provider_session_ref=provider_session_ref,
        reason=str(claimed.input["reason"]),
    )
