"""Durable reads the validation controller works from.

Every fact a Validation Attempt turns on is a committed row: the exact candidate,
its dataset, the pinned criteria revision, the originating user, and the case's
remaining budgets. Nothing here accepts a model claim or a caller-supplied
duplicate of a stored value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    ValidationAttemptState,
    ValidationOutcome,
)

from app.db.pool import Database
from app.db.rows import (
    as_bool,
    as_datetime,
    as_int,
    as_optional_str,
    as_optional_uuid,
    as_str,
    as_uuid,
)
from app.errors import AggregateNotFoundError

__all__ = [
    "CaseBudgets",
    "CriterionRow",
    "ValidationAttemptRow",
    "load_attempt",
    "load_budgets",
    "load_criteria",
    "load_criteria_on_connection",
    "session_is_bound",
]


@dataclass(frozen=True, slots=True)
class ValidationAttemptRow:
    """One Validation Attempt joined to the candidate it is bound to."""

    id: UUID
    client_id: UUID
    product_signal_id: UUID
    implementation_attempt_id: UUID
    preview_version_id: UUID
    preview_runtime_allocation_id: UUID
    interview_session_id: UUID
    client_user_id: UUID
    state: ValidationAttemptState
    revision: int
    dataset_manifest_digest: str
    acceptance_criteria_revision: int
    authenticated_identity_ref: str
    workflow_ref: str
    evidence_window_id: UUID | None
    outcome: ValidationOutcome | None
    interrupted_at: datetime | None
    opened_at: datetime
    source_commit: str
    source_digest: str
    build_digest: str
    candidate_base_revision: str
    repository_ref: str


@dataclass(frozen=True, slots=True)
class CaseBudgets:
    """The case facts validation, Case File assembly, and closure read."""

    product_signal_id: UUID
    client_id: UUID
    state: str
    revision: int
    return_deadline_at: datetime | None
    pinned_configuration_release_id: UUID
    cancellation_scope_id: UUID
    originating_client_user_id: UUID
    originating_customer_account_id: UUID
    acceptance_criteria_revision: int


@dataclass(frozen=True, slots=True)
class CriterionRow:
    """One frozen acceptance criterion at the pinned criteria revision."""

    id: UUID
    ordinal: int
    statement: str
    observability: CriterionObservability


_ATTEMPT_QUERY = """
    select attempts.id, attempts.client_id, attempts.product_signal_id,
           attempts.implementation_attempt_id, attempts.preview_version_id,
           attempts.preview_runtime_allocation_id, attempts.interview_session_id,
           attempts.client_user_id, attempts.state, attempts.revision,
           attempts.dataset_manifest_digest, attempts.acceptance_criteria_revision,
           attempts.authenticated_identity_ref, attempts.workflow_ref,
           attempts.evidence_window_id, attempts.outcome, attempts.interrupted_at,
           attempts.created_at as opened_at, versions.source_commit, versions.source_digest, versions.build_digest,
           versions.base_revision as candidate_base_revision,
           implementations.repository_ref
    from public.validation_attempts as attempts
    join public.preview_versions as versions
      on versions.id = attempts.preview_version_id
    join public.implementation_attempts as implementations
      on implementations.id = attempts.implementation_attempt_id
    where attempts.id = %s
"""


def _to_attempt(row: Mapping[str, object]) -> ValidationAttemptRow:
    outcome = as_optional_str(row, "outcome")
    return ValidationAttemptRow(
        id=as_uuid(row, "id"),
        client_id=as_uuid(row, "client_id"),
        product_signal_id=as_uuid(row, "product_signal_id"),
        implementation_attempt_id=as_uuid(row, "implementation_attempt_id"),
        preview_version_id=as_uuid(row, "preview_version_id"),
        preview_runtime_allocation_id=as_uuid(row, "preview_runtime_allocation_id"),
        interview_session_id=as_uuid(row, "interview_session_id"),
        client_user_id=as_uuid(row, "client_user_id"),
        state=ValidationAttemptState(as_str(row, "state")),
        revision=as_int(row, "revision"),
        dataset_manifest_digest=as_str(row, "dataset_manifest_digest"),
        acceptance_criteria_revision=as_int(row, "acceptance_criteria_revision"),
        authenticated_identity_ref=as_str(row, "authenticated_identity_ref"),
        workflow_ref=as_str(row, "workflow_ref"),
        evidence_window_id=as_optional_uuid(row, "evidence_window_id"),
        outcome=None if outcome is None else ValidationOutcome(outcome),
        interrupted_at=(
            None if row["interrupted_at"] is None else as_datetime(row, "interrupted_at")
        ),
        opened_at=as_datetime(row, "opened_at"),
        source_commit=as_str(row, "source_commit"),
        source_digest=as_str(row, "source_digest"),
        build_digest=as_str(row, "build_digest"),
        candidate_base_revision=as_str(row, "candidate_base_revision"),
        repository_ref=as_str(row, "repository_ref"),
    )


async def load_attempt(
    conn: AsyncConnection[DictRow], *, attempt_id: UUID
) -> ValidationAttemptRow:
    """Return one Validation Attempt with its candidate identities."""
    async with conn.cursor() as cur:
        await cur.execute(_ATTEMPT_QUERY, (attempt_id,))
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="validation_attempts", row_id=attempt_id)
    return _to_attempt(row)


async def load_budgets(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID
) -> CaseBudgets:
    """Return the case's pinned facts and its current lifecycle revision."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select signals.id, signals.client_id, signals.state, signals.revision,
                   signals.return_deadline_at,
                   signals.pinned_configuration_release_id, signals.cancellation_scope_id,
                   signals.originating_client_user_id,
                   signals.originating_customer_account_id,
                   signals.acceptance_criteria_revision
            from public.product_signals as signals
            where signals.id = %s
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="product_signals", row_id=product_signal_id)
    return CaseBudgets(
        product_signal_id=as_uuid(row, "id"),
        client_id=as_uuid(row, "client_id"),
        state=as_str(row, "state"),
        revision=as_int(row, "revision"),
        return_deadline_at=(
            None
            if row["return_deadline_at"] is None
            else as_datetime(row, "return_deadline_at")
        ),
        pinned_configuration_release_id=as_uuid(row, "pinned_configuration_release_id"),
        cancellation_scope_id=as_uuid(row, "cancellation_scope_id"),
        originating_client_user_id=as_uuid(row, "originating_client_user_id"),
        originating_customer_account_id=as_uuid(row, "originating_customer_account_id"),
        acceptance_criteria_revision=as_int(row, "acceptance_criteria_revision"),
    )


async def load_criteria_on_connection(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID, criteria_revision: int
) -> tuple[CriterionRow, ...]:
    """Return the frozen acceptance criteria for one criteria revision."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, ordinal, statement, observability
            from public.validation_criteria
            where product_signal_id = %s and criteria_revision = %s
            order by ordinal
            """,
            (product_signal_id, criteria_revision),
        )
        rows = list(await cur.fetchall())
    return tuple(
        CriterionRow(
            id=as_uuid(row, "id"),
            ordinal=as_int(row, "ordinal"),
            statement=as_str(row, "statement"),
            observability=CriterionObservability(as_str(row, "observability")),
        )
        for row in rows
    )


async def load_criteria(
    database: Database, *, product_signal_id: UUID, criteria_revision: int
) -> tuple[CriterionRow, ...]:
    """Return the frozen acceptance criteria outside a caller-owned transaction."""
    async with database.connection() as conn:
        return await load_criteria_on_connection(
            conn, product_signal_id=product_signal_id, criteria_revision=criteria_revision
        )


async def session_is_bound(
    conn: AsyncConnection[DictRow],
    *,
    interview_session_id: UUID,
    product_signal_id: UUID,
    client_user_id: UUID,
) -> bool:
    """Whether one Interview Session belongs to this case and this participant."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select exists (
                select 1 from public.interview_sessions
                where id = %s and product_signal_id = %s and client_user_id = %s
            ) as bound
            """,
            (interview_session_id, product_signal_id, client_user_id),
        )
        row = await cur.fetchone()
    return as_bool(row, "bound") if row is not None else False
