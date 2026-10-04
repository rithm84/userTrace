"""One case built all the way to a ready candidate, in the real database.

``tests.factories.create_case`` pins a configuration release whose policy carries
only the implementation modes and the revision budget, and that row is immutable.
Validation and Case File assembly additionally read the pinned contact allowance
and the authenticated dashboard origin, so this module builds its own case with
the full policy rather than mutating a frozen release.

Every insert goes through the same connector the control plane uses, so the
scenario exercises the production schema, its constraints, and its policies.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    AgentCharacterState,
    CriterionObservability,
    EvidenceClaimState,
    EvidenceClass,
    FlowType,
    GateDecisionKind,
    ImplementationAttemptState,
    ImplementationProgress,
    IntakeItemState,
    InterviewPhase,
    InterviewSessionState,
    PreviewAllocationKind,
    PreviewRuntimeAllocationState,
    PreviewVersionState,
    ProductSignalState,
)

from app.db.pool import Database
from app.ids import uuid7
from app.preview.artifacts import patch_digest_for
from app.validation.controller import ValidationBinding
from app.validation.evidence import EvidenceStance, evidence_body
from tests.helpers import policy_digest, record_session_readiness

__all__ = [
    "CRITERIA_REVISION",
    "DASHBOARD_BASE_URL",
    "ScenarioCriterion",
    "ValidationScenario",
    "add_check_result",
    "add_criterion_observation",
    "add_implementation_artifact",
    "add_intake_source",
    "add_transcript_fragment",
    "add_workflow_observation",
    "binding_of",
    "build_scenario",
]

CRITERIA_REVISION = 1
DASHBOARD_BASE_URL = "https://app.utrace.dev"
WORKFLOW_REF = "saved-conversion-views"


@dataclass(frozen=True, slots=True)
class ScenarioCriterion:
    """One frozen acceptance criterion inserted for the scenario."""

    id: UUID
    ordinal: int
    statement: str
    observability: CriterionObservability


@dataclass(frozen=True, slots=True)
class ValidationScenario:
    """A case with a ready candidate, its dataset, and its frozen criteria."""

    client_id: UUID
    configuration_release_id: UUID
    utrace_user_id: UUID
    client_user_id: UUID
    customer_account_id: UUID
    intake_item_id: UUID
    product_signal_id: UUID
    cancellation_scope_id: UUID
    interview_session_id: UUID
    gate_decision_id: UUID
    implementation_attempt_id: UUID
    preview_version_id: UUID
    preview_runtime_allocation_id: UUID
    dataset_manifest_digest: str
    repository_ref: str
    base_revision: str
    source_commit: str
    criteria: tuple[ScenarioCriterion, ...]


def binding_of(scenario: ValidationScenario) -> ValidationBinding:
    """Return the exact binding tuple for this scenario's candidate."""
    return ValidationBinding(
        client_id=scenario.client_id,
        product_signal_id=scenario.product_signal_id,
        client_user_id=scenario.client_user_id,
        implementation_attempt_id=scenario.implementation_attempt_id,
        preview_version_id=scenario.preview_version_id,
        preview_runtime_allocation_id=scenario.preview_runtime_allocation_id,
        interview_session_id=scenario.interview_session_id,
        dataset_manifest_digest=scenario.dataset_manifest_digest,
        authenticated_identity_ref="dudulemon-growth-lead",
        acceptance_criteria_revision=CRITERIA_REVISION,
        workflow_ref=WORKFLOW_REF,
    )


async def build_scenario(
    database: Database,
    *,
    name: str,
    contact_budget: int,
    authorizing_auth_user_id: UUID,
    participant_auth_user_id: UUID | None,
    criteria: Sequence[tuple[str, CriterionObservability]],
    signal_state: ProductSignalState,
    candidate_ready: bool,
) -> ValidationScenario:
    """Insert one client, participant, case, candidate, and frozen criteria."""
    client_id = uuid7()
    release_id = uuid7()
    utrace_user_id = uuid7()
    client_user_id = uuid7()
    account_id = uuid7()
    membership_id = uuid7()
    intake_id = uuid7()
    signal_id = uuid7()
    scope_id = uuid7()
    session_id = uuid7()
    window_id = uuid7()
    gate_id = uuid7()
    attempt_id = uuid7()
    version_id = uuid7()
    allocation_id = uuid7()
    dataset_digest = f"sha256:dataset-{name}"
    repository_ref = "ShopalyticsStore/shopalytics"
    base_revision = "a" * 40
    source_commit = "b" * 40
    now = datetime.now(tz=UTC)
    policy = {
        "live_implementation_allowed": True,
        "contact_budget": contact_budget,
        "dashboard_base_url": DASHBOARD_BASE_URL,
    }

    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "insert into public.clients (id, name, product_name) values (%s, %s, %s)",
            (client_id, name, f"{name} product"),
        )
        await cur.execute(
            """
            insert into public.configuration_releases (
                id, client_id, release_number, policy, digest,
                authorized_by_auth_user_id
            )
            values (%s, %s, 1, %s, %s, %s)
            """,
            (
                release_id,
                client_id,
                Jsonb(policy),
                policy_digest(policy),
                authorizing_auth_user_id,
            ),
        )
        await cur.execute(
            "insert into public.users (id, auth_user_id, display_name) "
            "values (%s, %s, %s)",
            (utrace_user_id, participant_auth_user_id, "Maya Okonkwo"),
        )
        await cur.execute(
            "insert into public.client_users (id, client_id, utrace_user_id) "
            "values (%s, %s, %s)",
            (client_user_id, client_id, utrace_user_id),
        )
        await cur.execute(
            """
            insert into public.customer_accounts (
                id, client_id, external_account_key, name
            )
            values (%s, %s, %s, %s)
            """,
            (account_id, client_id, f"{name}-dudulemon", "Dudulemon"),
        )
        await cur.execute(
            """
            insert into public.customer_memberships (
                id, client_id, customer_account_id, client_user_id
            )
            values (%s, %s, %s, %s)
            """,
            (membership_id, client_id, account_id, client_user_id),
        )
        await cur.execute(
            """
            insert into public.intake_items (
                id, client_id, state, source_provider, source_channel_ref,
                source_batch_digest, author_provider_identity, client_user_id,
                customer_account_id, payload
            )
            values (%s, %s, %s, 'discord', 'channel', %s, 'discord-user', %s, %s, %s)
            """,
            (
                intake_id,
                client_id,
                IntakeItemState.ROUTED.value,
                f"digest-{intake_id}",
                client_user_id,
                account_id,
                Jsonb({"messages": ["I keep rebuilding the same conversion filters"]}),
            ),
        )
        await cur.execute(
            """
            insert into public.product_signals (
                id, client_id, state, intake_item_id,
                pinned_configuration_release_id, originating_client_user_id,
                originating_customer_account_id, initial_flow_type,
                established_flow_type, acceptance_criteria_revision,
                cancellation_scope_id
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                signal_id,
                client_id,
                signal_state.value,
                intake_id,
                release_id,
                client_user_id,
                account_id,
                FlowType.FEATURE_REQUEST_FLOW.value,
                FlowType.FEATURE_REQUEST_FLOW.value,
                CRITERIA_REVISION,
                scope_id,
            ),
        )
        await cur.execute(
            "update public.intake_items set product_signal_id = %s where id = %s",
            (signal_id, intake_id),
        )
        await cur.execute(
            """
            insert into public.interview_sessions (
                id, client_id, product_signal_id, client_user_id, state, phase,
                character_state, implementation_progress
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session_id,
                client_id,
                signal_id,
                client_user_id,
                InterviewSessionState.LIVE.value,
                InterviewPhase.VALIDATION.value,
                AgentCharacterState.LISTENING.value,
                ImplementationProgress.READY_FOR_VALIDATION.value,
            ),
        )
        await cur.execute(
            """
            insert into public.evidence_windows (
                id, client_id, product_signal_id, window_kind, starts_at, ends_at
            )
            values (%s, %s, %s, 'gate', %s, %s)
            """,
            (window_id, client_id, signal_id, now - timedelta(hours=1), now),
        )
        await cur.execute(
            """
            insert into public.gate_decisions (
                id, client_id, product_signal_id, decision, blocker_reason,
                classification_revision, acceptance_criteria_revision,
                pinned_configuration_release_id, evidence_window_id, approved_scope,
                required_checks, budgets, cited_evidence
            )
            values (%s, %s, %s, %s, null, 1, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                gate_id,
                client_id,
                signal_id,
                GateDecisionKind.APPROVED_LIVE.value,
                CRITERIA_REVISION,
                release_id,
                window_id,
                Jsonb(
                    {
                        "paths": ["apps/dashboard/saved-views"],
                        "excluded": ["sharing", "notifications"],
                    }
                ),
                Jsonb({"commands": ["pnpm test", "pnpm lint"]}),
                Jsonb({"elapsed_seconds": 3600}),
                Jsonb({"evidence_ids": []}),
            ),
        )
        await cur.execute(
            """
            insert into public.implementation_attempts (
                id, client_id, product_signal_id, gate_decision_id, state,
                repository_ref, base_revision, consumed_budgets
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                attempt_id,
                client_id,
                signal_id,
                gate_id,
                (
                    ImplementationAttemptState.CANDIDATE_READY.value
                    if candidate_ready
                    else ImplementationAttemptState.EXECUTING.value
                ),
                repository_ref,
                base_revision,
                Jsonb({"elapsed_seconds": 1200}),
            ),
        )
        await cur.execute(
            """
            insert into public.preview_versions (
                id, client_id, product_signal_id, implementation_attempt_id, state,
                source_commit, source_digest, build_digest, base_revision,
                seed_manifest_digest, ready_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                version_id,
                client_id,
                signal_id,
                attempt_id,
                (
                    PreviewVersionState.READY.value
                    if candidate_ready
                    else PreviewVersionState.BUILDING.value
                ),
                source_commit,
                f"sha256:source-{name}",
                f"sha256:build-{name}",
                base_revision,
                dataset_digest,
                now if candidate_ready else None,
            ),
        )
        await cur.execute(
            """
            insert into public.preview_runtime_allocations (
                id, client_id, product_signal_id, allocation_kind, pool_role, state,
                interview_session_id, preview_version_id, seed_manifest_digest,
                runtime_ref, origin_host, fencing_token
            )
            values (%s, %s, %s, %s, 'candidate', %s, %s, %s, %s, %s, %s, utrace.next_fencing_token())
            """,
            (
                allocation_id,
                client_id,
                signal_id,
                PreviewAllocationKind.CANDIDATE.value,
                PreviewRuntimeAllocationState.BOUND.value,
                session_id,
                version_id,
                dataset_digest,
                f"daytona-{allocation_id.hex[:8]}",
                f"{allocation_id.hex[:12]}.preview.utrace.dev",
            ),
        )
        await cur.execute(
            "update public.interview_sessions set preview_runtime_allocation_id = %s "
            "where id = %s",
            (allocation_id, session_id),
        )
        inserted: list[ScenarioCriterion] = []
        for ordinal, (statement, observability) in enumerate(criteria, start=1):
            criterion_id = uuid7()
            await cur.execute(
                """
                insert into public.validation_criteria (
                    id, client_id, product_signal_id, criteria_revision, ordinal,
                    statement, observability
                )
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    criterion_id,
                    client_id,
                    signal_id,
                    CRITERIA_REVISION,
                    ordinal,
                    statement,
                    observability.value,
                ),
            )
            inserted.append(
                ScenarioCriterion(
                    id=criterion_id,
                    ordinal=ordinal,
                    statement=statement,
                    observability=observability,
                )
            )

    await record_session_readiness(
        database,
        interview_session_id=session_id,
        conversational=True,
        observation=True,
    )
    return ValidationScenario(
        client_id=client_id,
        configuration_release_id=release_id,
        utrace_user_id=utrace_user_id,
        client_user_id=client_user_id,
        customer_account_id=account_id,
        intake_item_id=intake_id,
        product_signal_id=signal_id,
        cancellation_scope_id=scope_id,
        interview_session_id=session_id,
        gate_decision_id=gate_id,
        implementation_attempt_id=attempt_id,
        preview_version_id=version_id,
        preview_runtime_allocation_id=allocation_id,
        dataset_manifest_digest=dataset_digest,
        repository_ref=repository_ref,
        base_revision=base_revision,
        source_commit=source_commit,
        criteria=tuple(inserted),
    )


async def _insert_evidence(
    database: Database,
    scenario: ValidationScenario,
    *,
    evidence_class: EvidenceClass,
    source_ref: str,
    body: dict[str, object],
    supersedes_id: UUID | None,
) -> UUID:
    record_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.evidence_records (
                id, client_id, product_signal_id, interview_session_id,
                evidence_class, claim_state, source_ref, context_revision, body,
                supersedes_id
            )
            values (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
            """,
            (
                record_id,
                scenario.client_id,
                scenario.product_signal_id,
                scenario.interview_session_id,
                evidence_class.value,
                EvidenceClaimState.CONFIRMED.value,
                source_ref,
                Jsonb(body),
                supersedes_id,
            ),
        )
    return record_id


async def add_intake_source(
    database: Database, scenario: ValidationScenario, *, summary: str
) -> UUID:
    """Record the originating Discord request as intake source evidence."""
    return await _insert_evidence(
        database,
        scenario,
        evidence_class=EvidenceClass.INTAKE_SOURCE,
        source_ref=f"discord:message:{uuid7().hex[:16]}",
        body={"content": summary},
        supersedes_id=None,
    )


async def add_transcript_fragment(
    database: Database, scenario: ValidationScenario, *, field: str, summary: str
) -> UUID:
    """Record one contextualized interview fragment for a Case File field."""
    return await _insert_evidence(
        database,
        scenario,
        evidence_class=EvidenceClass.TRANSCRIPT_FRAGMENT,
        source_ref=f"transcript:{uuid7().hex[:16]}",
        body={"kind": "interview_claim", "statement": summary, "field": field},
        supersedes_id=None,
    )


async def add_criterion_observation(
    database: Database,
    scenario: ValidationScenario,
    *,
    criterion: ScenarioCriterion,
    stance: EvidenceStance,
    evidence_class: EvidenceClass,
    summary: str,
    supersedes_id: UUID | None,
) -> UUID:
    """Record one promoted observation for or against one acceptance criterion."""
    return await _insert_evidence(
        database,
        scenario,
        evidence_class=evidence_class,
        source_ref=f"observation:{uuid7().hex[:16]}",
        body=dict(
            evidence_body(
                criterion_id=criterion.id,
                stance=stance,
                summary=summary,
                workflow_ref=WORKFLOW_REF,
                milestone=f"criterion-{criterion.ordinal}",
                preview_version_id=scenario.preview_version_id,
                coverage_limit=None,
                observed_at=None,
            )
        ),
        supersedes_id=supersedes_id,
    )


async def add_workflow_observation(
    database: Database,
    scenario: ValidationScenario,
    *,
    milestone: str,
    summary: str,
    preview_version_id: UUID | None,
    coverage_limit: str | None,
) -> UUID:
    """Record one observed milestone of the agreed workflow."""
    return await _insert_evidence(
        database,
        scenario,
        evidence_class=EvidenceClass.BROWSER_OBSERVATION,
        source_ref=f"observation:{uuid7().hex[:16]}",
        body=dict(
            evidence_body(
                criterion_id=None,
                stance=None,
                summary=summary,
                workflow_ref=WORKFLOW_REF,
                milestone=milestone,
                preview_version_id=preview_version_id,
                coverage_limit=coverage_limit,
                observed_at=None,
            )
        ),
        supersedes_id=None,
    )


async def add_check_result(
    database: Database, scenario: ValidationScenario, *, name: str, outcome: str
) -> UUID:
    """Record one pinned check and how it ended."""
    return await _insert_evidence(
        database,
        scenario,
        evidence_class=EvidenceClass.CHECK_RESULT,
        source_ref=f"check:{name}",
        body={"summary": f"{name} {outcome}", "name": name, "outcome": outcome},
        supersedes_id=None,
    )


async def add_implementation_artifact(
    database: Database,
    scenario: ValidationScenario,
    *,
    files: Sequence[tuple[str, bytes]],
    cleanup_state: str,
) -> UUID:
    """Freeze the validated files on the Preview Version and record the artifact evidence.

    The files land where production freezes them, on the Preview Version, which is
    what delivery commits.
    """
    frozen = [
        {
            "path": path,
            "content": content.decode("utf-8"),
            "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
        }
        for path, content in files
    ]
    manifest = {item["path"]: item["digest"] for item in frozen}
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_versions
            set patch_text = %s, source_manifest = %s, patch_digest = %s,
                build_recipe = %s, migrations = %s, artifact_recorded_at = now()
            where id = %s
            """,
            (
                Jsonb(frozen),
                Jsonb(manifest),
                patch_digest_for(manifest),
                Jsonb([]),
                Jsonb([]),
                scenario.preview_version_id,
            ),
        )
    return await _insert_evidence(
        database,
        scenario,
        evidence_class=EvidenceClass.IMPLEMENTATION_ARTIFACT,
        source_ref=f"artifact:{scenario.source_commit}",
        body={
            "summary": f"{len(files)} file(s) changed in the isolated workspace",
            "files_touched": [path for path, _ in files],
            "cleanup_state": cleanup_state,
        },
        supersedes_id=None,
    )
