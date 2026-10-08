"""Job handlers the control plane owns.

Other workstreams register their own job types, and, where a Job left with no
legal run has a product consequence, the handler that commits it. The control
plane owns the maintenance work that only it can authorize: here, committing the
terminal expiry of an Outreach Attempt whose pinned response deadline has passed.
"""

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import ActorKind, AggregateKind

from app.db.kernel import apply_transition
from app.db.pool import Database
from app.db.rows import as_int, as_str, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, Failure, StreamRef
from app.errors import AggregateNotFoundError
from app.execution.jobs import ClaimedJob
from app.gate.job_types import DECIDE_GATE
from app.gate.jobs import run_early_investigation, run_gate, settle_failed_gate
from app.ids import uuid7
from app.implementation.handlers import (
    RUN_IMPLEMENTATION_ATTEMPT,
    run_implementation_attempt,
)
from app.implementation.job_types import (
    INVESTIGATE_CODE,
    PREPARE_WORKSPACE,
    RELEASE_PREPARED_WORKSPACE,
)
from app.implementation.prepared import prepare_workspace, release_prepared_workspace
from app.ingestion.jobs import build_ingestion_handlers
from app.intake.worker import intake_handlers
from app.interview.handlers import interview_handlers
from app.interview.voice.connections import VOICE_AGENT
from app.lifecycle.case_stop import CLOSE_CASE_AFTER_STOP
from app.lifecycle.machine import plan_transition
from app.lifecycle.outreach import OUTREACH_ATTEMPT_MACHINE, OutreachAttemptFacts
from app.logging import get_logger
from app.offers.handlers import offer_handlers
from app.outputs.handlers import assemble_case_file, close_case_after_stop
from app.outputs.job_types import ASSEMBLE_CASE_FILE
from app.preview.jobs import build_preview_handlers
from app.validation.controller import EXPIRE_VALIDATION_RETURN
from app.validation.return_expiry import expire_validation_return

__all__ = [
    "EXPIRE_OUTREACH_ATTEMPT",
    "VOICE_JOB_TYPES",
    "build_exhausted_handlers",
    "build_handlers",
    "expire_outreach_attempt",
]

_log = get_logger(__name__)

EXPIRE_OUTREACH_ATTEMPT = "control_plane.expire_outreach_attempt"

_SYSTEM_ACTOR = Actor(kind=ActorKind.SYSTEM, ref="control_plane.scheduler")


async def expire_outreach_attempt(database: Database, claimed: ClaimedJob) -> None:
    """Commit the terminal expiry of one Outreach Attempt past its deadline."""
    attempt_id = UUID(str(claimed.input["outreach_attempt_id"]))
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select state, revision, expires_at, product_signal_id
            from public.outreach_attempts where id = %s
            """,
            (attempt_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="outreach_attempts", row_id=attempt_id)

    now = datetime.now(tz=UTC)
    request = plan_transition(
        OUTREACH_ATTEMPT_MACHINE,
        OutreachAttemptFacts(
            bound_identity_matches=False, expired=True, contact_budget_remaining=0
        ),
        trigger="expire",
        current_state=as_str(row, "state"),
        client_id=claimed.client_id,
        aggregate_kind=AggregateKind.OUTREACH_ATTEMPT,
        aggregate_id=attempt_id,
        expected_revision=as_int(row, "revision"),
        state_column="state",
        mutation={"reason": "response_deadline_expired"},
        event=EventAppend(
            message_id=uuid7(),
            stream=StreamRef(kind=AggregateKind.OUTREACH_ATTEMPT, id=attempt_id),
            event_type="outreach_attempt_expired",
            payload={"expires_at": row["expires_at"].isoformat()},
            actor=_SYSTEM_ACTOR,
            causation_id=claimed.job_id,
            correlation_id=as_uuid(row, "product_signal_id"),
            occurred_at=now,
        ),
        jobs=(),
        commands=(),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=StreamRef(kind=AggregateKind.OUTREACH_ATTEMPT, id=attempt_id),
            action="expire_outreach_attempt",
            outcome="expired",
            reason_code="response_deadline_expired",
            actor=_SYSTEM_ACTOR,
            pinned_configuration_release_id=None,
            detail={"job_id": str(claimed.job_id)},
        ),
        additional_locks=(),
    )
    outcome = await apply_transition(database, request)
    _log.info(
        "outreach_attempt_expired",
        client_id=str(claimed.client_id),
        outreach_attempt_id=str(attempt_id),
        revision=outcome.revision,
    )


#: The Job types the worker's voice lane runs in its own process (`app.execution.voice_lane`).
VOICE_JOB_TYPES: frozenset[str] = frozenset({VOICE_AGENT})


def build_handlers() -> Mapping[str, Callable[[Database, ClaimedJob], Awaitable[None]]]:
    """Return every Job handler the worker runs.

    A handler that needs a provider opens its connector for the Job Run from the
    process environment, so a missing credential fails that Job by name rather
    than the whole worker.
    """
    return {
        EXPIRE_OUTREACH_ATTEMPT: expire_outreach_attempt,
        EXPIRE_VALIDATION_RETURN: expire_validation_return,
        ASSEMBLE_CASE_FILE: assemble_case_file,
        CLOSE_CASE_AFTER_STOP: close_case_after_stop,
        DECIDE_GATE: run_gate,
        INVESTIGATE_CODE: run_early_investigation,
        PREPARE_WORKSPACE: prepare_workspace,
        RELEASE_PREPARED_WORKSPACE: release_prepared_workspace,
        RUN_IMPLEMENTATION_ATTEMPT: run_implementation_attempt,
        **offer_handlers(),
        **intake_handlers(),
        **build_ingestion_handlers(),
        **build_preview_handlers(),
        **interview_handlers(),
    }


def build_exhausted_handlers() -> Mapping[
    str, Callable[[AsyncConnection[DictRow], ClaimedJob, Failure], Awaitable[None]]
]:
    """Return what each Job type's owning controller commits when a Job has no legal run left.

    The worker runs it in the transaction that records the terminal failure.
    """
    return {DECIDE_GATE: settle_failed_gate}
