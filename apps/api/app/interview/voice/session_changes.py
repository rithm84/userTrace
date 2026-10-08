"""Waking the voice agent when its Interview Session's committed state changes.

The voice agent acts on state other processes commit: a queued mute, the preview
route and readiness, implementation progress, and the participant's preview
activity. Database triggers notify the `voice_sideband` channel (named for the
earlier voice design, kept to avoid a schema change) with the session
each change belongs to, and a controller whose committed fact no trigger covers
notifies it in the same transaction (`wake_voice_agents`). This listener wakes
every watcher of that session, so each re-reads its state at once instead of
polling for it.

A watcher starts woken, so its first read happens immediately, and is woken again
whenever the listener (re)connects, so a change committed while nobody listened
is still read. A notification carries no state; the watcher always re-reads.
"""

import asyncio
from typing import Final
from uuid import UUID

from psycopg import AsyncConnection, OperationalError
from psycopg.rows import DictRow

from app.db.pool import Database
from app.logging import get_logger

__all__ = ["VOICE_SIDEBAND_CHANNEL", "SessionChanges", "wake_voice_agents"]

_log = get_logger(__name__)

VOICE_SIDEBAND_CHANNEL: Final[str] = "voice_sideband"
#: Waits before each reconnect of a lost listener; after the last, it fails.
_RECONNECT_DELAYS_SECONDS: Final[tuple[float, ...]] = (0.5, 1.0, 2.0, 4.0, 8.0)


class SessionChanges:
    """Fans committed changes to one Interview Session out to its watchers."""

    def __init__(self, database: Database, *, interview_session_id: UUID) -> None:
        self._database = database
        self._session = str(interview_session_id)
        self._watchers: set[asyncio.Event] = set()
        self._listening = asyncio.Event()

    def watch(self) -> asyncio.Event:
        """A new watcher: set when the session may have changed, cleared by its owner.

        The owner clears it before each read, so a change committed during the read
        sets it again rather than being lost.
        """
        watcher = asyncio.Event()
        watcher.set()
        self._watchers.add(watcher)
        return watcher

    def committed(self) -> None:
        """Wake every watcher for a change this process committed that no trigger reports.

        Triggers report what any process commits to the tables they watch; a change
        committed here outside them, such as a Validation Attempt beginning, would
        otherwise wait for the next unrelated notification.
        """
        self._wake_all()

    async def listening(self) -> None:
        """Return once the first listener is established."""
        await self._listening.wait()

    async def run(self) -> None:
        """Listen until cancelled, reopening a lost listener a bounded number of times."""
        delays = iter(_RECONNECT_DELAYS_SECONDS)
        while True:
            try:
                async with self._database.listener(VOICE_SIDEBAND_CHANNEL) as listener:
                    delays = iter(_RECONNECT_DELAYS_SECONDS)
                    self._wake_all()
                    self._listening.set()
                    async for notification in listener.notifies():
                        if notification.payload == self._session:
                            self._wake_all()
            except OperationalError as error:
                delay = next(delays, None)
                if delay is None:
                    raise
                _log.warning(
                    "voice_sideband_listener_lost",
                    interview_session_id=self._session,
                    detail=str(error),
                    reconnect_in_seconds=delay,
                )
                await asyncio.sleep(delay)

    def _wake_all(self) -> None:
        for watcher in self._watchers:
            watcher.set()


async def wake_voice_agents(conn: AsyncConnection[DictRow], *, product_signal_id: UUID) -> None:
    """Wake the voice agent of every open Interview Session of a case when the caller commits.

    Postgres delivers a notification only once its transaction commits, so a
    watcher never re-reads before the fact it is woken for exists.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select pg_notify(%s, sessions.id::text)
            from public.interview_sessions as sessions
            where sessions.product_signal_id = %s and sessions.state <> 'closed'
            """,
            (VOICE_SIDEBAND_CHANNEL, product_signal_id),
        )
