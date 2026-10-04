"""Durable fixtures for the implementation-controller tests.

These build on the gate fixtures: a gated case, its immutable gate decision with
a real frozen approved scope, the authorized attempt under real revision
accounting, and the attempt's fenced Environment Lease. Nothing here bypasses a
constraint the production path enforces.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import ImplementationAttemptState

from app.clients.shopalytics_release import (
    SHOPALYTICS_BUILD_RECIPE,
    SHOPALYTICS_REQUIRED_CHECKS,
)
from app.db.pool import Database
from app.db.rows import as_datetime, as_int
from app.gate.scope import (
    AcceptanceCriterion,
    ApprovedScope,
    DependencyPolicy,
    RequiredCheck,
    ResourceBudgets,
)
from app.ids import uuid7
from app.implementation.environment import WORKSPACE_ROOT
from app.implementation.specification import (
    AttemptSpecification,
    LeaseBinding,
    RepositoryCheckout,
)
from tests.gate.factories import (
    SHOPALYTICS_BASE_REVISION,
    SHOPALYTICS_CLONE_URL,
    SHOPALYTICS_REPOSITORY,
    GatedCase,
)

__all__ = [
    "SHOPALYTICS_BUILD_RECIPE",
    "AuthorizedAttempt",
    "authorize_attempt",
    "saved_views_scope",
    "shopalytics_checks",
]

@dataclass(frozen=True, slots=True)
class AuthorizedAttempt:
    """One authorized attempt with its frozen scope and its active lease."""

    gate_decision_id: UUID
    implementation_attempt_id: UUID
    lease_id: UUID
    fencing_token: int
    specification: AttemptSpecification


def shopalytics_checks() -> tuple[RequiredCheck, ...]:
    """The Shopalytics required checks, in the order the workspace runs them."""
    return SHOPALYTICS_REQUIRED_CHECKS


def saved_views_scope(
    *,
    checks: Sequence[RequiredCheck],
    correction_cycles: int = 2,
    elapsed_seconds: int = 3600,
    process_executions: int = 200,
    artifact_bytes: int = 20_000_000,
    changed_files: int = 12,
) -> ApprovedScope:
    """The frozen scope for the demo's Saved Conversion Views work."""
    return ApprovedScope(
        intended_outcome=(
            "save the current conversion filter stack under a name and reopen it later"
        ),
        acceptance_criteria=(
            AcceptanceCriterion(
                criterion_id=str(uuid7()),
                ordinal=0,
                statement="the complete filter stack can be saved under a user-supplied name",
                observability="observed",
            ),
            AcceptanceCriterion(
                criterion_id=str(uuid7()),
                ordinal=1,
                statement="reopening the view restores every saved filter",
                observability="observed",
            ),
        ),
        acceptance_criteria_revision=0,
        classification_revision=0,
        evidence_revision=0,
        repository_ref=SHOPALYTICS_REPOSITORY,
        base_revision=SHOPALYTICS_BASE_REVISION,
        in_scope_changes=(
            "persist saved conversion views for the account",
            "add a control that saves the current conversion-view state with a name",
            "add a selector for reopening a saved view",
        ),
        excluded_changes=("sharing", "rename", "deletion", "a dashboard redesign"),
        permitted_paths=("src/lib/dashboard", "src/components/dashboard", "tests/unit"),
        blocked_paths=("src/lib/utrace", "src/app/api", "db", "scripts"),
        required_checks=tuple(checks),
        dependency_policy=DependencyPolicy.NONE,
        permitted_dependencies=(),
        dependency_manifest_paths=("package.json", "package-lock.json"),
        budgets=ResourceBudgets(
            elapsed_seconds=elapsed_seconds,
            agent_turns=400,
            agent_cost_cents=5_000,
            process_executions=process_executions,
            artifact_bytes=artifact_bytes,
            correction_cycles=correction_cycles,
            changed_files=changed_files,
        ),
    )


async def authorize_attempt(
    database: Database,
    *,
    case: GatedCase,
    scope: ApprovedScope,
    workspace_snapshot_id: str,
) -> AuthorizedAttempt:
    """Insert the gate decision, the attempt, and its active fenced lease."""
    window_id = uuid7()
    decision_id = uuid7()
    attempt_id = uuid7()
    lease_id = uuid7()
    columns = scope.to_columns()

    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.evidence_windows (
                id, client_id, product_signal_id, window_kind, starts_at, ends_at
            )
            values (%s, %s, %s, 'implementation_gate', now() - interval '1 hour', now())
            """,
            (window_id, case.client_id, case.product_signal_id),
        )
        await cur.execute(
            """
            insert into public.gate_decisions (
                id, client_id, product_signal_id, decision, blocker_reason,
                classification_revision, acceptance_criteria_revision,
                pinned_configuration_release_id, evidence_window_id, approved_scope,
                required_checks, budgets, cited_evidence
            )
            values (%s, %s, %s, %s, null, 0, 0, %s, %s, %s, %s, %s, %s)
            """,
            (
                decision_id,
                case.client_id,
                case.product_signal_id,
                "approved_live",
                case.configuration_release_id,
                window_id,
                Jsonb(dict(columns.approved_scope)),
                Jsonb(dict(columns.required_checks)),
                Jsonb(dict(columns.budgets)),
                Jsonb({"evidence_ids": [], "scope_digest": scope.digest()}),
            ),
        )
        await cur.execute(
            """
            insert into public.implementation_attempts (
                id, client_id, product_signal_id, gate_decision_id, state,
                repository_ref, base_revision, consumed_budgets, workspace_snapshot_id
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                attempt_id,
                case.client_id,
                case.product_signal_id,
                decision_id,
                ImplementationAttemptState.EXECUTING.value,
                SHOPALYTICS_REPOSITORY,
                SHOPALYTICS_BASE_REVISION,
                Jsonb({}),
                workspace_snapshot_id,
            ),
        )
        await cur.execute(
            """
            insert into public.environment_leases (
                id, client_id, implementation_attempt_id, state, fencing_token,
                expires_at
            )
            values (%s, %s, %s, 'active', utrace.next_fencing_token(),
                    now() + interval '2 hours')
            returning fencing_token, expires_at
            """,
            (lease_id, case.client_id, attempt_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise AssertionError("the Environment Lease insert returned no fencing token")
    fencing_token = as_int(row, "fencing_token")
    assert as_datetime(row, "expires_at") is not None

    specification = AttemptSpecification(
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        implementation_attempt_id=attempt_id,
        gate_decision_id=decision_id,
        interview_session_id=None,
        pinned_configuration_release_id=case.configuration_release_id,
        checkout=RepositoryCheckout(
            repository_ref=SHOPALYTICS_REPOSITORY,
            clone_url=SHOPALYTICS_CLONE_URL,
            base_revision=SHOPALYTICS_BASE_REVISION,
            workspace_root=WORKSPACE_ROOT,
            application_port=SHOPALYTICS_BUILD_RECIPE.application_port,
        ),
        approved_scope=scope,
        lease=LeaseBinding(lease_id=lease_id, fencing_token=fencing_token),
        workspace_snapshot_id=workspace_snapshot_id,
        context_bundle_refs=(f"gate_decision:{decision_id}",),
        code_findings=(),
        callback_hosts=("callbacks.utrace.dev",),
    )
    return AuthorizedAttempt(
        gate_decision_id=decision_id,
        implementation_attempt_id=attempt_id,
        lease_id=lease_id,
        fencing_token=fencing_token,
        specification=specification,
    )
