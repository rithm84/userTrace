"""The worker's voice lane: the Job loop that runs voice agents, in its own process.

A voice agent streams a call's audio for as long as the call lasts. It runs apart
from the worker's other Jobs so neither waits on the other: a call's agent is
claimed within a poll of being queued however many gate, preview, and ingestion
Jobs are running, and their work never stalls the call's audio on a shared event
loop or interpreter lock. `utrace-worker` starts this process and stops with it
(`app.execution.worker`).
"""

import asyncio
import dataclasses
import signal
from logging import INFO
from typing import Final

from app.db.pool import Database
from app.execution.handlers import VOICE_JOB_TYPES, build_exhausted_handlers, build_handlers
from app.execution.worker import Worker, WorkerConfig, default_config
from app.logging import configure_logging
from app.settings import get_settings

__all__ = ["main", "voice_lane_config"]

#: How often an idle voice lane looks for a queued call: the participant is waiting.
_VOICE_POLL_SECONDS: Final[float] = 0.25
#: Calls one worker holds at once.
_VOICE_CONCURRENCY: Final[int] = 4


def voice_lane_config(base: WorkerConfig) -> WorkerConfig:
    """The voice lane's tuning: the worker's, polling fast and leaving wake-ups to it."""
    return dataclasses.replace(
        base,
        worker_id=f"{base.worker_id}:voice",
        poll_interval_seconds=_VOICE_POLL_SECONDS,
        concurrency=_VOICE_CONCURRENCY,
        wakeup_batch_size=0,
    )


async def _run() -> None:
    configure_logging(INFO)
    settings = get_settings()
    database = Database(
        dsn=settings.database_url,
        min_size=settings.database_pool_min_size,
        max_size=settings.database_pool_max_size,
    )
    await database.open()
    handlers = {
        job_type: handler
        for job_type, handler in build_handlers().items()
        if job_type in VOICE_JOB_TYPES
    }
    worker = Worker(
        database, voice_lane_config(default_config()), handlers, build_exhausted_handlers()
    )
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.request_stop)
    try:
        await worker.run()
    finally:
        await database.close()


def main() -> int:
    """Run the voice lane until it is asked to stop."""
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
