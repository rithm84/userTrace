"""The durable Jobs the interview runtime owns.

The worker registers these alongside the control plane's own handlers. Each one
builds its provider clients from configuration when it runs, so a missing variable
fails that Job by name instead of the whole process. The voice agent's own Job,
`interview.voice_agent`, runs in its session on Pipecat Cloud
(`app.interview.voice.cloud_session`), not here.

Two Jobs:

- `interview.close_provider_session` deletes a call no participant connection may
  use, which is how a superseded or late-arriving call is cleaned up rather than
  left to expire.
- `interview.expire_reconnect_window` closes a session whose lost call was not
  replaced in time.
"""

from collections.abc import Awaitable, Callable, Mapping

from app.db.pool import Database
from app.execution.jobs import ClaimedJob
from app.interview.reconnect import EXPIRE_RECONNECT_WINDOW, expire_reconnect_window
from app.interview.settings import get_voice_settings
from app.interview.voice.adapter import build_daily_rooms
from app.interview.voice.connections import CLOSE_PROVIDER_SESSION
from app.logging import get_logger

__all__ = ["close_provider_session", "interview_handlers"]

_log = get_logger(__name__)

type JobHandler = Callable[[Database, ClaimedJob], Awaitable[None]]


def interview_handlers() -> Mapping[str, JobHandler]:
    """Return this workstream's Job handlers for the worker's registry."""
    return {
        CLOSE_PROVIDER_SESSION: close_provider_session,
        EXPIRE_RECONNECT_WINDOW: expire_reconnect_window,
    }


async def close_provider_session(database: Database, claimed: ClaimedJob) -> None:
    """Delete one call that no participant connection may use."""
    provider_session_ref = str(claimed.input["provider_session_ref"])
    async with build_daily_rooms(settings=get_voice_settings()) as rooms:
        await rooms.delete_room(room_name=provider_session_ref)
    _log.info(
        "voice_provider_session_cleanup_completed",
        job_id=str(claimed.job_id),
        provider_session_ref=provider_session_ref,
        reason=str(claimed.input["reason"]),
    )
