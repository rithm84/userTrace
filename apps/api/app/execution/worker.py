"""The durable worker loop and the ``utrace-worker`` entry point.

The worker reconciles durable state before it becomes ready, then repeats:
scan the outbox, consume pgmq wake-ups, claim the next legal Job under a
transactional lease, run its handler while heartbeating, and record the typed
outcome. Up to ``concurrency`` Jobs run at once. Queue delivery is at least
once, so every step is idempotent.

The same process hosts the watched-channel consumers beside the Job loop, so
one deployed control plane holds exactly one Discord gateway session. The
[consumer runtime](../intake/runtime.py) states why they live here. Beside them
runs the [liveness listener](./liveness.py) Supabase Compute serves on
``$PORT``. Any of the three ending stops the others: a signal stops them
cleanly, and a consumer that exhausts its attempts is recorded and exits the
process non-zero so the platform restarts it.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from logging import INFO

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import RetryAdvice, TechnicalCategory

from app.connectors.failures import ConnectorFailure
from app.db.pool import Database
from app.envelope import Failure, JsonValue
from app.errors import GuardRejectedError
from app.execution.handlers import build_exhausted_handlers, build_handlers
from app.execution.jobs import (
    ClaimedJob,
    ExhaustionHook,
    claim_job,
    complete_job_run,
    fail_job_run,
    heartbeat,
)
from app.execution.liveness import serve_liveness
from app.execution.outbox import acknowledge_wakeup, read_wakeups, scan_ready
from app.execution.reconcile import reconcile_startup
from app.intake.runtime import WatchedChannelConsumers, get_consumer_settings
from app.intake.settings import get_intake_settings
from app.logging import configure_logging, get_logger
from app.settings import get_settings

__all__ = ["ExhaustedHandler", "Worker", "WorkerConfig", "default_config", "main"]

_log = get_logger(__name__)

type JobHandler = Callable[[Database, ClaimedJob], Awaitable[None]]
type ExhaustedHandler = Callable[[AsyncConnection[DictRow], ClaimedJob, Failure], Awaitable[None]]
"""What a Job type's owning controller commits when a Job of that type has no legal run left."""


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    """Everything one worker process needs. Nothing is implicit."""

    worker_id: str
    lease_seconds: int
    heartbeat_seconds: int
    poll_interval_seconds: float
    wakeup_batch_size: int
    outbox_scan_limit: int
    stale_outbox_seconds: int
    stale_dispatch_seconds: int
    concurrency: int


class Worker:
    """One long-running worker process over the durable execution tables."""

    def __init__(
        self,
        database: Database,
        config: WorkerConfig,
        handlers: Mapping[str, JobHandler],
        exhausted_handlers: Mapping[str, ExhaustedHandler],
    ) -> None:
        self._database = database
        self._config = config
        self._handlers = handlers
        self._exhausted_handlers = exhausted_handlers
        self._stopping = asyncio.Event()

    def request_stop(self) -> None:
        """Ask the loop to let its running Jobs finish and exit."""
        self._stopping.set()

    async def run(self) -> None:
        """Reconcile, then process work until a stop is requested."""
        await reconcile_startup(
            self._database,
            stale_outbox_seconds=self._config.stale_outbox_seconds,
            stale_dispatch_seconds=self._config.stale_dispatch_seconds,
        )
        _log.info(
            "worker_ready",
            worker_id=self._config.worker_id,
            job_types=sorted(self._handlers),
            concurrency=self._config.concurrency,
        )
        # Jobs run side by side: an Implementation Attempt runs for minutes, and
        # the gate, preview, and ingestion work must keep moving meanwhile rather
        # than wait behind it.
        running: set[asyncio.Task[None]] = set()
        crashed: list[BaseException] = []

        def settle(task: asyncio.Task[None]) -> None:
            # `execute` records handler failures itself, so an exception here is
            # the worker's own infrastructure failing: stop, as the loop always has.
            running.discard(task)
            error = None if task.cancelled() else task.exception()
            if error is not None:
                crashed.append(error)
                self.request_stop()

        try:
            while not self._stopping.is_set():
                if len(running) >= self._config.concurrency:
                    await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                    continue
                claimed = await self._claim()
                if claimed is None:
                    await self._idle(running)
                    continue
                task = asyncio.create_task(self.execute(claimed))
                running.add(task)
                task.add_done_callback(settle)
        finally:
            if running:
                await asyncio.wait(running)
        if crashed:
            raise crashed[0]

    async def run_once(self) -> bool:
        """Do one unit of work. Returns whether anything was claimed."""
        claimed = await self._claim()
        if claimed is None:
            return False
        await self.execute(claimed)
        return True

    async def _claim(self) -> ClaimedJob | None:
        """Wake on queued signals and lease the next runnable Job, if any."""
        await scan_ready(self._database, limit=self._config.outbox_scan_limit)
        wakeups = await read_wakeups(
            self._database,
            batch_size=self._config.wakeup_batch_size,
            visibility_seconds=self._config.lease_seconds,
        )
        for wakeup in wakeups:
            await acknowledge_wakeup(self._database, message_id=wakeup.message_id)

        return await claim_job(
            self._database,
            job_types=sorted(self._handlers),
            worker_id=self._config.worker_id,
            lease_seconds=self._config.lease_seconds,
        )

    async def execute(self, claimed: ClaimedJob) -> None:
        handler = self._handlers.get(claimed.job_type)
        if handler is None:
            await self._fail(
                claimed,
                Failure(
                    category=TechnicalCategory.CONFIGURATION,
                    code="unhandled_job_type",
                    message=f"no handler is registered for job type {claimed.job_type}",
                    retry=RetryAdvice.AFTER_CONDITION_CHANGE,
                    retry_condition="job_handler_registered",
                    details={"job_type": claimed.job_type},
                ),
            )
            return

        beat = asyncio.create_task(self._heartbeat_until_done(claimed))
        try:
            await handler(self._database, claimed)
        except ConnectorFailure as error:
            # A provider call failed in a way its connector already classified, so
            # the Job's own retry categories can decide whether another run follows.
            beat.cancel()
            _log.warning(
                "job_provider_call_failed",
                job_id=str(claimed.job_id),
                job_run_id=str(claimed.job_run_id),
                job_type=claimed.job_type,
                provider=error.detail.provider,
                operation=error.detail.operation,
                category=error.detail.category.value,
                retry=error.detail.retry_advice.value,
            )
            await self._fail(claimed, _provider_failure(error))
            return
        except Exception as error:  # noqa: BLE001 - converted into a typed failure record
            # A handler failure is a durable outcome, not a worker crash: record the
            # typed failure so the Job's own retry class and run budget decide what
            # happens next, then keep serving other Jobs.
            beat.cancel()
            details = _handler_failure_details(error)
            _log.error(
                "job_handler_raised",
                job_id=str(claimed.job_id),
                job_run_id=str(claimed.job_run_id),
                job_type=claimed.job_type,
                failure_code="job_handler_raised",
                failure_class=type(error).__name__,
                failure_message=str(error),
                failure_details=details,
                exc_info=error,
            )
            await self._fail(
                claimed,
                Failure(
                    category=TechnicalCategory.INTERNAL,
                    code="job_handler_raised",
                    message=f"{type(error).__name__}: {error}",
                    retry=RetryAdvice.AFTER_DELAY,
                    # The Job's own backoff decides when; this failure imposes
                    # no minimum of its own.
                    retry_after_ms=0,
                    details=details,
                ),
            )
            return
        beat.cancel()
        await complete_job_run(
            self._database,
            job_id=claimed.job_id,
            job_run_id=claimed.job_run_id,
            fencing_token=claimed.fencing_token,
        )

    async def _fail(self, claimed: ClaimedJob, failure: Failure) -> None:
        """Record a failed run, with its owning controller's consequence if no run is left."""
        exhausted = self._exhausted_handlers.get(claimed.job_type)
        await fail_job_run(
            self._database,
            job_id=claimed.job_id,
            job_run_id=claimed.job_run_id,
            fencing_token=claimed.fencing_token,
            failure=failure,
            on_exhausted=None if exhausted is None else _bound(exhausted, claimed, failure),
        )

    async def _heartbeat_until_done(self, claimed: ClaimedJob) -> None:
        """Renew the lease while the handler runs."""
        while True:
            await asyncio.sleep(self._config.heartbeat_seconds)
            await heartbeat(
                self._database,
                job_run_id=claimed.job_run_id,
                fencing_token=claimed.fencing_token,
                lease_seconds=self._config.lease_seconds,
            )

    async def _idle(self, running: set[asyncio.Task[None]]) -> None:
        """Idle for one poll interval, waking early on a stop request or a finished Job.

        A finished Job has usually just committed the next step of its chain, so the
        loop claims that step at once instead of a poll interval later.
        """
        stop_wait = asyncio.create_task(self._stopping.wait())
        await asyncio.wait(
            {stop_wait, *running},
            timeout=self._config.poll_interval_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        stop_wait.cancel()


def _bound(handler: ExhaustedHandler, claimed: ClaimedJob, failure: Failure) -> ExhaustionHook:
    """The exhaustion consequence of one failed run, for the transaction recording it."""

    async def hook(conn: AsyncConnection[DictRow]) -> None:
        await handler(conn, claimed, failure)

    return hook


def _handler_failure_details(error: Exception) -> dict[str, JsonValue]:
    """The structured detail of a handler's error; a refused guard names what refused and why."""
    details: dict[str, JsonValue] = {"error_class": type(error).__name__}
    if isinstance(error, GuardRejectedError):
        details["guard_machine"] = error.machine
        details["guard_trigger"] = error.trigger
        details["guard_reason"] = error.reason
    return details


def _provider_failure(error: ConnectorFailure) -> Failure:
    """Record a classified provider failure under its own category and retry advice.

    The contract omits optional fields rather than nulling them, so each retry
    advice carries exactly the one field it requires.
    """
    detail = error.detail
    details: dict[str, JsonValue] = {
        "provider": detail.provider,
        "operation": detail.operation,
    }
    if detail.status_code is not None:
        details["status_code"] = detail.status_code
    if detail.provider_error_code is not None:
        details["provider_error_code"] = detail.provider_error_code
    common = {
        "category": detail.category,
        "code": "provider_call_failed",
        "message": str(error)[:2000],
        "retry": detail.retry_advice,
        "details": details,
    }
    if detail.retry_advice is RetryAdvice.AFTER_DELAY:
        # The Job's own backoff decides when; the provider imposes no minimum here.
        return Failure.model_validate({**common, "retry_after_ms": 0})
    if detail.retry_advice is RetryAdvice.AFTER_CONDITION_CHANGE:
        return Failure.model_validate(
            {**common, "retry_condition": "provider_condition_changed"}
        )
    return Failure.model_validate(common)


def default_config() -> WorkerConfig:
    """Read worker tuning from the environment with the process identity resolved."""
    return WorkerConfig(
        worker_id=os.environ.get(
            "UTRACE_WORKER_ID", f"{socket.gethostname()}:{os.getpid()}"
        ),
        lease_seconds=int(os.environ.get("UTRACE_WORKER_LEASE_SECONDS", "60")),
        heartbeat_seconds=int(os.environ.get("UTRACE_WORKER_HEARTBEAT_SECONDS", "20")),
        poll_interval_seconds=float(
            os.environ.get("UTRACE_WORKER_POLL_SECONDS", "2.0")
        ),
        wakeup_batch_size=int(os.environ.get("UTRACE_WORKER_WAKEUP_BATCH", "20")),
        outbox_scan_limit=int(os.environ.get("UTRACE_WORKER_OUTBOX_SCAN", "200")),
        stale_outbox_seconds=int(os.environ.get("UTRACE_WORKER_STALE_OUTBOX", "300")),
        stale_dispatch_seconds=int(os.environ.get("UTRACE_WORKER_STALE_DISPATCH", "900")),
        concurrency=int(os.environ.get("UTRACE_WORKER_CONCURRENCY", "6")),
    )


async def _run() -> int:
    configure_logging(INFO)
    liveness_port = _liveness_port()
    settings = get_settings()
    database = Database(
        dsn=settings.database_url,
        min_size=settings.database_pool_min_size,
        max_size=settings.database_pool_max_size,
    )
    await database.open()
    worker = Worker(database, default_config(), build_handlers(), build_exhausted_handlers())
    consumers = WatchedChannelConsumers(
        database, get_intake_settings(), get_consumer_settings()
    )

    def request_stop() -> None:
        """Stop the Job loop and the watched-channel consumers together."""
        _log.info("worker_stop_requested")
        worker.request_stop()
        consumers.request_stop()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, request_stop)
    try:
        return await _serve(worker, consumers, liveness_port=liveness_port)
    finally:
        await database.close()


def _liveness_port() -> int:
    """The port Supabase Compute serves, which the liveness listener answers on."""
    port = os.environ.get("PORT")
    if port is None:
        raise RuntimeError(
            "PORT is not set: Supabase Compute provides it, and a local run must set it "
            "for the worker's liveness listener"
        )
    return int(port)


async def _serve(
    worker: Worker, consumers: WatchedChannelConsumers, *, liveness_port: int
) -> int:
    """Run the Job loop, the consumers, and the liveness listener until one ends.

    Whichever ends first, the others are asked to stop and awaited, so a signal
    drains the current Job and closes the gateway sessions, and a failed
    component does not leave a half-running process behind.
    """
    job_loop = asyncio.create_task(worker.run(), name="job-loop")
    intake = asyncio.create_task(consumers.run(), name="intake-consumers")
    liveness = asyncio.create_task(serve_liveness(liveness_port), name="liveness")
    await asyncio.wait(
        {job_loop, intake, liveness}, return_when=asyncio.FIRST_COMPLETED
    )
    worker.request_stop()
    consumers.request_stop()
    liveness.cancel()
    results = await asyncio.gather(job_loop, intake, liveness, return_exceptions=True)
    failed = False
    for component, result in zip(
        ("job_loop", "intake_consumers", "liveness"), results, strict=True
    ):
        if isinstance(result, asyncio.CancelledError) and component == "liveness":
            continue
        if isinstance(result, BaseException):
            failed = True
            _log.error(
                "worker_component_failed",
                component=component,
                failure_class=type(result).__name__,
                exc_info=result,
            )
    _log.info("worker_exited", failed=failed)
    return 1 if failed else 0


def main() -> int:
    """Console entry point for ``utrace-worker``."""
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
