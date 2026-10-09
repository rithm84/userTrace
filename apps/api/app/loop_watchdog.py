"""Reporting where the API is stuck when its event loop stops answering.

Supabase Compute suspends an idle service and resumes it on the next request. Twice
the API stopped answering after an idle stretch while its instance stayed up, and
nothing it logged showed why. A thread outside the event loop now watches a
heartbeat the loop beats every second: when the loop misses it, the thread logs
how long the loop has been stalled and the stack of every thread, once per stall,
and when the wall clock jumps far past the thread's own sleep it logs that the
process was resumed.
"""

import asyncio
import sys
import threading
import time
import traceback
from typing import Final

from app.logging import get_logger

__all__ = ["LoopWatchdog"]

_log = get_logger(__name__)

_BEAT_SECONDS: Final[float] = 1.0
_CHECK_SECONDS: Final[float] = 5.0
_STALL_SECONDS: Final[float] = 15.0
_RESUME_SECONDS: Final[float] = 30.0


class LoopWatchdog:
    """Watches one event loop from its own daemon thread."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._last_beat = time.monotonic()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="loop-watchdog", daemon=True)
        self._beat: asyncio.TimerHandle | None = None

    def start(self) -> None:
        self._schedule_beat()
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._beat is not None:
            self._beat.cancel()
        self._thread.join(timeout=_CHECK_SECONDS * 2)

    def _schedule_beat(self) -> None:
        self._last_beat = time.monotonic()
        self._beat = self._loop.call_later(_BEAT_SECONDS, self._schedule_beat)

    def _watch(self) -> None:
        reported = False
        wall, mono = time.time(), time.monotonic()
        while not self._stopped.wait(_CHECK_SECONDS):
            now_wall, now_mono = time.time(), time.monotonic()
            if now_wall - wall > _CHECK_SECONDS + _RESUME_SECONDS:
                _log.warning(
                    "process_resumed",
                    wall_seconds=round(now_wall - wall, 1),
                    monotonic_seconds=round(now_mono - mono, 1),
                )
            wall, mono = now_wall, now_mono
            stalled = now_mono - self._last_beat
            if stalled < _STALL_SECONDS:
                if reported:
                    _log.warning("event_loop_recovered", stalled_seconds=round(stalled, 1))
                reported = False
                continue
            if not reported:
                _log.error(
                    "event_loop_stalled",
                    stalled_seconds=round(stalled, 1),
                    threads={
                        str(thread_id): "".join(traceback.format_stack(frame))[-4000:]
                        for thread_id, frame in sys._current_frames().items()
                    },
                )
                reported = True
