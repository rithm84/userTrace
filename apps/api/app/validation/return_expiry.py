"""The terminal expiry of a case's return-and-closure deadline.

Expiry is terminal: it revokes outstanding links and pending authority, fences
leases, cancels queued product-work descendants, reconciles dispatched effects,
abandons the open Validation Attempt without recording a verdict, and obliges the
explanatory blocker Case File once participation has happened. A case that
expires before participation is canceled with no Case File, like any other
pre-participation stop. It provides no resume path and no substitute validator.

Contract: docs/arch-components/failure-recovery.md#bounded-return-and-terminal-expiry
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    CaseFileVariant,
    CommandState,
    EnvironmentLeaseState,
    JobState,
    OutreachAttemptState,
)

from app.db.kernel import append_fact, apply_transition_on_connection
from app.db.pool import Database
from app.envelope import Actor, AuditIntent, EventAppend, JsonValue, StreamRef
from app.execution.jobs import ClaimedJob
from app.ids import uuid7
from app.lifecycle.case_stop import participation_occurred
from app.lifecycle.machine import plan_transition
from app.lifecycle.product_signal import (
    NONTERMINAL_STATES,
    PRODUCT_SIGNAL_MACHINE,
    ProductSignalFacts,
)
from app.logging import get_logger
from app.outputs.closure_jobs import (
    CASE_FILE_DEADLINE_SECONDS,
    case_file_assembly_job,
    entitlement_job,
)
from app.validation.controller import abandon_validation_attempt, open_attempt_id
from app.validation.records import load_budgets

__all__ = [
    "WithdrawnAuthority",
    "expire_validation_return",
    "withdraw_case_authority",
]

_log = get_logger(__name__)

_SYSTEM_ACTOR = Actor(kind=ActorKind.SYSTEM, ref="validation.return_expiry")

_OPEN_OUTREACH: tuple[str, ...] = (
    OutreachAttemptState.SCHEDULED.value,
    OutreachAttemptState.DISPATCHING.value,
    OutreachAttemptState.AWAITING_RESPONSE.value,
)


@dataclass(frozen=True, slots=True)
class WithdrawnAuthority:
    """What a terminal transaction revoked, fenced, cancelled, or reconciled."""

    revoked_outreach_attempts: int
    fenced_leases: int
    canceled_jobs: int
    commands_in_reconciliation: int


async def withdraw_case_authority(
    conn: AsyncConnection[DictRow],
    *,
    product_signal_id: UUID,
    cancellation_scope_id: UUID,
    reason: str,
) -> WithdrawnAuthority:
    """Revoke links and pending authority, fence leases, and reconcile effects."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.outreach_attempts
            set state = %s, reason = %s, revision = revision + 1, updated_at = now()
            where product_signal_id = %s and state = any(%s)
            """,
            (OutreachAttemptState.REVOKED.value, reason, product_signal_id, list(_OPEN_OUTREACH)),
        )
        revoked = cur.rowcount
        await cur.execute(
            """
            update public.invitations
            set revoked_at = now(), updated_at = now()
            where product_signal_id = %s and revoked_at is null and claimed_at is null
            """,
            (product_signal_id,),
        )
        await cur.execute(
            """
            update public.environment_leases
            set state = %s, revision = revision + 1, updated_at = now()
            where state = %s
              and implementation_attempt_id in (
                select id from public.implementation_attempts
                where product_signal_id = %s
              )
            """,
            (
                EnvironmentLeaseState.FENCED.value,
                EnvironmentLeaseState.ACTIVE.value,
                product_signal_id,
            ),
        )
        fenced = cur.rowcount
        await cur.execute(
            """
            update public.jobs
            set state = %s, revision = revision + 1, updated_at = now()
            where cancellation_scope_id = %s and state in (%s, %s)
            """,
            (
                JobState.CANCELED.value,
                cancellation_scope_id,
                JobState.PENDING.value,
                JobState.READY.value,
            ),
        )
        canceled = cur.rowcount
        await cur.execute(
            """
            update public.commands
            set state = %s, reconciling_at = now(), revision = revision + 1,
                updated_at = now()
            where cancellation_scope_id = %s and state in (%s, %s, %s)
            """,
            (
                CommandState.RECONCILING.value,
                cancellation_scope_id,
                CommandState.DISPATCHING.value,
                CommandState.DISPATCHED.value,
                CommandState.ACKNOWLEDGED.value,
            ),
        )
        reconciling = cur.rowcount
    return WithdrawnAuthority(
        revoked_outreach_attempts=revoked,
        fenced_leases=fenced,
        canceled_jobs=canceled,
        commands_in_reconciliation=reconciling,
    )


async def expire_validation_return(database: Database, claimed: ClaimedJob) -> None:
    """Close a case whose return-and-closure deadline expired."""
    product_signal_id = UUID(str(claimed.input["product_signal_id"]))
    reason = str(claimed.input["reason"])
    now = datetime.now(tz=UTC)
    async with database.transaction() as conn:
        budgets = await load_budgets(conn, product_signal_id=product_signal_id)
        if budgets.state not in NONTERMINAL_STATES:
            _log.info(
                "validation_expiry_skipped_terminal_case",
                product_signal_id=str(product_signal_id),
                state=budgets.state,
            )
            return
        if budgets.return_deadline_at is not None and budgets.return_deadline_at > now:
            _log.info(
                "validation_expiry_skipped_deadline_open",
                product_signal_id=str(product_signal_id),
                return_deadline_at=budgets.return_deadline_at.isoformat(),
            )
            return

        withdrawn = await withdraw_case_authority(
            conn,
            product_signal_id=product_signal_id,
            cancellation_scope_id=budgets.cancellation_scope_id,
            reason=reason,
        )
        attempt_id = await open_attempt_id(conn, product_signal_id=product_signal_id)
        if attempt_id is not None:
            await abandon_validation_attempt(conn, attempt_id=attempt_id, reason=reason)

        detail: Mapping[str, JsonValue] = {
            "reason": reason,
            "revoked_outreach_attempts": withdrawn.revoked_outreach_attempts,
            "fenced_leases": withdrawn.fenced_leases,
            "canceled_jobs": withdrawn.canceled_jobs,
            "commands_in_reconciliation": withdrawn.commands_in_reconciliation,
        }
        stream = StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=product_signal_id)
        event = EventAppend(
            message_id=uuid7(),
            stream=stream,
            event_type="validation_return_expired",
            payload=detail,
            actor=_SYSTEM_ACTOR,
            causation_id=claimed.job_id,
            correlation_id=product_signal_id,
            occurred_at=now,
        )
        audit = AuditIntent(
            audit_id=uuid7(),
            subject=stream,
            action="expire_validation_return",
            outcome="authority_withdrawn",
            reason_code=reason,
            actor=_SYSTEM_ACTOR,
            pinned_configuration_release_id=budgets.pinned_configuration_release_id,
            detail=detail,
        )
        entitlement = entitlement_job(
            product_signal_id=product_signal_id,
            stop_reason=reason,
            now=now,
            deadline_seconds=CASE_FILE_DEADLINE_SECONDS,
        )
        if not await participation_occurred(conn, product_signal_id):
            await apply_transition_on_connection(
                conn,
                plan_transition(
                    PRODUCT_SIGNAL_MACHINE,
                    ProductSignalFacts(
                        participation_occurred=False, case_file_published=False
                    ),
                    trigger="cancel",
                    current_state=budgets.state,
                    client_id=budgets.client_id,
                    aggregate_kind=AggregateKind.PRODUCT_SIGNAL,
                    aggregate_id=product_signal_id,
                    expected_revision=budgets.revision,
                    state_column="state",
                    mutation={"closure_reason": reason},
                    event=event,
                    jobs=(entitlement,),
                    commands=(),
                    audit=audit,
                    additional_locks=(),
                ),
            )
        else:
            await append_fact(
                conn,
                client_id=budgets.client_id,
                event=event,
                audit=audit,
                jobs=(
                    case_file_assembly_job(
                        product_signal_id=product_signal_id,
                        validation_attempt_id=None,
                        variant=CaseFileVariant.BLOCKED,
                        blocker_reason=reason,
                        closure_trigger="close_blocked",
                        closure_reason=reason,
                        now=now,
                        deadline_seconds=CASE_FILE_DEADLINE_SECONDS,
                    ),
                    entitlement,
                ),
            )
    _log.info(
        "validation_return_expired",
        product_signal_id=str(product_signal_id),
        reason=reason,
        revoked_outreach_attempts=withdrawn.revoked_outreach_attempts,
        fenced_leases=withdrawn.fenced_leases,
        canceled_jobs=withdrawn.canceled_jobs,
        commands_in_reconciliation=withdrawn.commands_in_reconciliation,
    )
