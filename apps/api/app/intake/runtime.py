"""The watched-channel consumer runtime: one supervised task per client.

Where this runs
---------------
Inside the ``utrace-worker`` process, beside the Job loop, not in a service of
its own. Three facts decide it:

- One bot token may hold one useful gateway session. The worker compute is
  already pinned to a single instance, so hosting the consumer there makes
  "exactly one gateway session" a property of a compute that already has it. A
  second compute would need the same constraint declared again for no gain.
- The [deployment map](../../../../docs/ARCHITECTURE.md#active-provider-lookup)
  places the persistent API, Discord, and workers on two Supabase Compute
  computes. A separate always-on compute is cost with no architectural mandate.
- [Boot recovery](../../../../docs/arch-components/failure-recovery.md) requires
  a service to reconcile durable state before it becomes ready. The consumer
  holds no authority, so it inherits the worker's reconciliation and boot order
  instead of needing its own.

The cost is that the worker compute cannot scale past one instance while it hosts
the gateway. That is the pilot's shape today; a second gateway consumer would
duplicate every receipt.

What supervision means
----------------------
A consumer that loses its gateway session retries with a warning and bounded
jittered backoff, and raises the provider's typed failure after the last
attempt. The supervisor then stops every other consumer and returns that failure
to the worker, which exits non-zero so the platform restarts it. Nothing is
recovered silently: a message that arrived while the process was down is
re-ingested through the staff route in `app.api.intake`.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Sequence
from functools import lru_cache
from typing import Final
from uuid import UUID

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.connectors.failures import ConnectorFailure
from app.db.pool import Database
from app.db.rows import as_uuid
from app.intake.settings import IntakeOutreachSettings
from app.intake.worker import consume_watched_channels
from app.logging import get_logger
from app.settings import ConfigurationError

__all__ = [
    "IntakeConsumerSettings",
    "WatchedChannelConsumers",
    "get_consumer_settings",
    "intake_client_ids",
]

_log = get_logger(__name__)

_MAXIMUM_BACKOFF_SECONDS: Final[float] = 60.0
"""No backoff waits longer than this, however many attempts have failed."""


class IntakeConsumerSettings(BaseSettings):
    """How the supervisor reacts when a client's gateway session ends.

    Every value is required, so a deployment states its own tolerance for a
    flapping Customer Surface rather than inheriting one from this module. Field
    names populate it too, so a test states a policy outright instead of editing
    the process environment.
    """

    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, populate_by_name=True
    )

    maximum_attempts: int = Field(
        validation_alias="UTRACE_INTAKE_CONSUMER_MAX_ATTEMPTS", gt=0
    )
    backoff_seconds: float = Field(
        validation_alias="UTRACE_INTAKE_CONSUMER_BACKOFF_SECONDS", gt=0
    )
    healthy_session_seconds: float = Field(
        validation_alias="UTRACE_INTAKE_CONSUMER_HEALTHY_SECONDS", gt=0
    )
    shutdown_seconds: float = Field(
        validation_alias="UTRACE_INTAKE_CONSUMER_SHUTDOWN_SECONDS", gt=0
    )


@lru_cache(maxsize=1)
def get_consumer_settings() -> IntakeConsumerSettings:
    """Return the supervision settings, read once from the environment."""
    try:
        return IntakeConsumerSettings()  # type: ignore[call-arg]
    except ValidationError as error:
        names: list[str] = []
        for item in error.errors():
            location = item["loc"]
            if location:
                names.append(str(location[0]))
        raise ConfigurationError(tuple(dict.fromkeys(names))) from None


async def intake_client_ids(database: Database) -> tuple[UUID, ...]:
    """Return every client that has published a configuration release.

    A release always pins one Customer Surface with at least one watched
    channel, so a client with a release is a client with intake surfaces. Each
    consumer reads that client's own active release when it starts.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select distinct client_id from public.configuration_releases "
            "order by client_id"
        )
        rows = await cur.fetchall()
    return tuple(as_uuid(row, "client_id") for row in rows)


class WatchedChannelConsumers:
    """One supervised watched-channel consumer per configured client."""

    def __init__(
        self,
        database: Database,
        intake: IntakeOutreachSettings,
        supervision: IntakeConsumerSettings,
    ) -> None:
        self._database = database
        self._intake = intake
        self._supervision = supervision
        self._stopping = asyncio.Event()

    def request_stop(self) -> None:
        """Ask every consumer to close its gateway session and return."""
        self._stopping.set()

    async def run(self) -> None:
        """Consume every client's watched channels until stopped.

        Returns when a stop is requested. Raises the typed provider failure of
        the first consumer that exhausts its attempts, after stopping the rest.
        """
        client_ids = await intake_client_ids(self._database)
        _log.info(
            "intake_consumers_started",
            client_count=len(client_ids),
            client_ids=[str(client_id) for client_id in client_ids],
        )
        stop = asyncio.create_task(self._stopping.wait(), name="intake-consumers-stop")
        consumers = [
            asyncio.create_task(
                self._supervise(client_id), name=f"intake-consumer-{client_id}"
            )
            for client_id in client_ids
        ]
        try:
            await asyncio.wait({stop, *consumers}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop.cancel()
        failure = await self._stop(consumers)
        _log.info("intake_consumers_stopped", client_count=len(consumers))
        if failure is not None:
            raise failure

    async def _stop(self, consumers: Sequence[asyncio.Task[None]]) -> BaseException | None:
        """Cancel every consumer inside the shutdown window and report a failure."""
        for consumer in consumers:
            consumer.cancel()
        if not consumers:
            return None
        _, pending = await asyncio.wait(
            set(consumers), timeout=self._supervision.shutdown_seconds
        )
        if pending:
            _log.warning(
                "intake_consumers_shutdown_incomplete",
                pending=len(pending),
                shutdown_seconds=self._supervision.shutdown_seconds,
            )
        failure: BaseException | None = None
        for consumer in consumers:
            if consumer.cancelled() or not consumer.done():
                continue
            error = consumer.exception()
            if error is not None and failure is None:
                failure = error
        return failure

    async def _supervise(self, client_id: UUID) -> None:
        """Run one client's consumer, retrying a lost gateway a bounded number of times."""
        loop = asyncio.get_running_loop()
        attempt = 0
        while not self._stopping.is_set():
            started = loop.time()
            try:
                await consume_watched_channels(
                    self._database, self._intake, client_id=client_id
                )
                return
            except ConnectorFailure as failure:
                if loop.time() - started >= self._supervision.healthy_session_seconds:
                    attempt = 0
                attempt += 1
                if attempt >= self._supervision.maximum_attempts:
                    _log.error(
                        "intake_consumer_failed",
                        client_id=str(client_id),
                        attempt=attempt,
                        maximum_attempts=self._supervision.maximum_attempts,
                        category=failure.detail.category.value,
                        detail=str(failure),
                    )
                    raise
                delay = self._backoff(attempt)
                _log.warning(
                    "intake_consumer_retrying",
                    client_id=str(client_id),
                    attempt=attempt,
                    maximum_attempts=self._supervision.maximum_attempts,
                    delay_seconds=round(delay, 2),
                    category=failure.detail.category.value,
                    detail=str(failure),
                )
                await self._sleep_until_stop(delay)

    def _backoff(self, attempt: int) -> float:
        """Return the jittered delay before one retry."""
        base = self._supervision.backoff_seconds
        ceiling = min(base * 2 ** (attempt - 1), _MAXIMUM_BACKOFF_SECONDS)
        return random.uniform(min(base, ceiling), ceiling)

    async def _sleep_until_stop(self, seconds: float) -> None:
        """Wait out one backoff, returning early when a stop is requested."""
        stop = asyncio.create_task(self._stopping.wait())
        _, pending = await asyncio.wait({stop}, timeout=seconds)
        for task in pending:
            task.cancel()
