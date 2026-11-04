"""A prepared draft, end to end, against real Vercel Sandbox, Codex, and Supabase.

The case's workspace is prepared as it is while an interview runs, and the code
investigation's findings are on record. A proposal is drafted in it by the real
coding agent, whose task carries those findings and the files they cite; the
draft stores its artifacts before any attempt exists. An agreement records that
draft, and the attempt the gate would authorize adopts it: the attempt authors
nothing itself, publishes from the draft's record before it has the workspace
open, and then runs the full checks on the workspace's own files. The preview side
is recorded rather than deployed; the live preview test proves the spare runtime.

The test prints the stage timings it measured, for the build-speed benchmarks.

`UTRACE_LIVE_WORKSPACE_BASE_REVISION` overrides the base revision the workspace
clones, for a template whose fixture commit is not yet on the repository's remote;
the workspace needs only the product source, which that commit's parent carries.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    InterviewSessionState,
    ProductSignalState,
)

from app.clients.shopalytics_release import (
    SHOPALYTICS_BUILD_RECIPE,
    SHOPALYTICS_REQUIRED_CHECKS,
    shopalytics_implementation_policy,
)
from app.connectors.openai_roles import OpenAIRole, openai_configuration_for
from app.connectors.vercel_sandboxes import VercelSandboxes
from app.db.pool import Database
from app.gate.policy import load_implementation_policy
from app.ids import uuid7
from app.implementation.agent_author import AgentAuthor
from app.implementation.artifact_store import ArtifactStore
from app.implementation.attempt import AttemptRunner, WorkspaceDependencies
from app.implementation.budgets import BudgetLedger, ConsumedBudgets
from app.implementation.draft_records import ProposedChange, ProposedCriterion
from app.implementation.drafts import draft_change
from app.implementation.ports import CandidateHandoff
from app.implementation.prepared import prepare_case_workspace
from tests.factories import CaseFixture, create_interview_session
from tests.gate.factories import SHOPALYTICS_BASE_REVISION, create_gated_case
from tests.implementation.driver import RecordingPreviewRuntime, Secret
from tests.implementation.factories import authorize_attempt, saved_views_scope
from tests.supabase_auth import SignedInUser

PROPOSAL = ProposedChange(
    intended_outcome=(
        "Save the current conversion filter stack under a name you choose and reopen it later"
    ),
    workflow_ref="saved_conversion_views",
    criteria=(
        ProposedCriterion(
            statement="Once it's ready, you'll save your filters under a name you choose",
            observability=CriterionObservability.OBSERVED,
        ),
        ProposedCriterion(
            statement="After a refresh, reopening the saved view brings back every filter",
            observability=CriterionObservability.OBSERVED,
        ),
    ),
)


FINDINGS = (
    ("src/components/dashboard/ConversionDashboard.tsx", 85, 150, "Holds the filter state and runs the dashboard's queries from it."),
    ("src/components/dashboard/FilterBar.tsx", 1, 80, "Renders the filter controls a saved view would capture."),
    ("db/schema.sql", 1, 40, "The dataset's tables, where a saved view would persist."),
)


async def _record_findings(database: Database, *, case: CaseFixture) -> None:
    """Record findings as the code investigation does while the interview runs."""
    investigation_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        for path, first_line, last_line, note in FINDINGS:
            await cur.execute(
                """
                insert into public.code_investigation_findings (
                    id, client_id, product_signal_id, investigation_id, repository_ref,
                    base_revision, path, first_line, last_line, excerpt_digest, note
                )
                values (%s, %s, %s, %s, 'utrace-dev/shopalytics', %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid7(),
                    case.client_id,
                    case.product_signal_id,
                    investigation_id,
                    SHOPALYTICS_BASE_REVISION,
                    path,
                    first_line,
                    last_line,
                    "sha256:" + "0" * 64,
                    note,
                ),
            )


async def _propose(database: Database, *, case: CaseFixture, session_id: UUID) -> UUID:
    """Record the proposal as `propose_change` does, without a context bundle."""
    draft_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.prepared_drafts (
                id, client_id, product_signal_id, interview_session_id, state,
                proposed_scope, criteria, proposal_digest
            )
            values (%s, %s, %s, %s, 'proposed', %s, %s, %s)
            """,
            (
                draft_id,
                case.client_id,
                case.product_signal_id,
                session_id,
                Jsonb(PROPOSAL.scope_column()),
                Jsonb([criterion.model_dump(mode="json") for criterion in PROPOSAL.criteria]),
                PROPOSAL.digest(),
            ),
        )
    return draft_id


async def _agree(database: Database, *, case: CaseFixture, session_id: UUID, draft_id: UUID) -> None:
    """Record the agreement as proposed, which names the draft, at the scope's revision."""
    bundle_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.context_bundles (
                id, client_id, product_signal_id, purpose, context_revision, cutoff_at,
                budget_items, included_items, omitted_items, dependencies, digest
            )
            values (%s, %s, %s, 'voice_delegation', 0, now(), 1, 0, 0, '{}', 'sha256:test')
            """,
            (bundle_id, case.client_id, case.product_signal_id),
        )
        await cur.execute(
            """
            insert into public.acceptance_criteria_agreements (
                id, client_id, product_signal_id, interview_session_id,
                acceptance_criteria_revision, agreed_scope, criterion_ids,
                supporting_evidence_ids, context_bundle_id, prepared_draft_id
            )
            values (%s, %s, %s, %s, 0, %s, '{}', '{}', %s, %s)
            """,
            (
                uuid7(),
                case.client_id,
                case.product_signal_id,
                session_id,
                Jsonb(PROPOSAL.scope_column()),
                bundle_id,
                draft_id,
            ),
        )


async def test_a_draft_written_during_the_proposal_is_what_the_attempt_publishes(
    database: Database,
    sandboxes: VercelSandboxes,
    artifact_store: ArtifactStore,
    auth_users: dict[str, SignedInUser],
    workspace_snapshot_id: str,
    github_token: Secret,
    openai_key: Secret,
) -> None:
    policy = shopalytics_implementation_policy(
        base_revision=os.environ.get("UTRACE_LIVE_WORKSPACE_BASE_REVISION", SHOPALYTICS_BASE_REVISION),
        workspace_snapshot_id=workspace_snapshot_id,
        callback_hosts=("callbacks.utrace.dev",),
    )
    gated = await create_gated_case(
        database,
        name="draft-live",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        policy=policy.model_dump(mode="json"),
        state=ProductSignalState.INTERVIEWING,
    )
    case = cast(CaseFixture, gated)
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    prepared_started = time.monotonic()
    await prepare_case_workspace(
        database,
        sandboxes,
        client_id=gated.client_id,
        product_signal_id=gated.product_signal_id,
        policy=await load_implementation_policy(
            database, configuration_release_id=gated.configuration_release_id
        ),
        repository_token=github_token.value,
    )
    preparation_seconds = time.monotonic() - prepared_started
    await _record_findings(database, case=case)

    # The voice agent proposes the change; the draft is written while it speaks.
    draft_id = await _propose(database, case=case, session_id=session_id)
    draft_runtime = RecordingPreviewRuntime(database)
    draft_started = time.monotonic()
    await draft_change(
        database,
        sandboxes,
        prepared_draft_id=draft_id,
        preview_runtime=draft_runtime,
        artifacts=artifact_store,
        change_author=AgentAuthor,
        openai_key=openai_key.value,
        prepare_gate=False,
    )
    draft_seconds = time.monotonic() - draft_started
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select state, failure_detail, fast_checks, files, preview_version_id,
                   agent_cost_cents, artifacts
            from public.prepared_drafts where id = %s
            """,
            (draft_id,),
        )
        draft = await cur.fetchone()
    assert draft is not None
    assert draft["state"] == "drafted", draft["failure_detail"]
    assert draft["files"], "the draft changed no file"
    # The type check runs in the workspace; the production build runs in the spare.
    assert [run["name"] for run in draft["fast_checks"]["runs"]] == ["typecheck", "build"]
    assert all(run["exit_code"] == 0 for run in draft["fast_checks"]["runs"])
    # Its version identifier is minted and its large artifacts are stored already.
    assert draft["preview_version_id"] is not None
    assert len(draft["artifacts"]) == 3
    assert draft["agent_cost_cents"] > 0

    # The participant says yes, and the gate approves exactly that change.
    agreed_at = time.monotonic()
    await _agree(database, case=case, session_id=session_id, draft_id=draft_id)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.product_signals set state = 'implementing' where id = %s",
            (gated.product_signal_id,),
        )
    scope = saved_views_scope(checks=SHOPALYTICS_REQUIRED_CHECKS).model_copy(
        update={
            "permitted_paths": policy.permitted_paths,
            "blocked_paths": policy.blocked_paths,
            "base_revision": policy.base_revision,
        }
    )
    attempt = await authorize_attempt(
        database, case=gated, scope=scope, workspace_snapshot_id=workspace_snapshot_id
    )
    # The workspace was prepared at the policy's base, which the attempt checks out too.
    specification = attempt.specification.model_copy(
        update={
            "checkout": attempt.specification.checkout.model_copy(
                update={"base_revision": policy.base_revision}
            )
        }
    )
    runtime = RecordingPreviewRuntime(database)
    published_at: list[float] = []

    async def candidate_ready(handoff: CandidateHandoff) -> None:
        published_at.append(time.monotonic())
        await runtime.candidate_ready(handoff)

    ledger = BudgetLedger(
        budgets=scope.budgets,
        already_consumed=ConsumedBudgets.empty(),
        started_at=datetime.now(tz=UTC),
    )
    attempt_started = time.monotonic()
    result = await AttemptRunner(
        database=database,
        dependencies=WorkspaceDependencies(
            sandboxes=sandboxes,
            artifacts=artifact_store,
            preview_runtime=runtime,
            change_author=AgentAuthor(
                configuration=openai_configuration_for(OpenAIRole.IMPLEMENTATION), ledger=ledger
            ),
            candidate_ready=candidate_ready,
        ),
        specification=specification,
        build_recipe=SHOPALYTICS_BUILD_RECIPE,
        repository_access_token=github_token.value,
        openai_api_key=openai_key.value,
        ledger=ledger,
    ).run()
    finished_at = time.monotonic()

    assert published_at, result.reason
    # The attempt published the draft's own files, staged nowhere new, under the
    # identifier the draft minted.
    assert runtime.staged == []
    assert [handoff.preview_version_id for handoff in runtime.ready] == [
        handoff.preview_version_id for handoff in runtime.handoffs
    ]
    assert runtime.handoffs[0].preview_version_id == draft["preview_version_id"]
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state, implementation_attempt_id from public.prepared_drafts where id = %s",
            (draft_id,),
        )
        adopted = await cur.fetchone()
        await cur.execute(
            "select full_checks_state, full_checks, full_checks_source_digest, source_digest "
            "from public.preview_versions "
            "where implementation_attempt_id = %s order by created_at",
            (attempt.implementation_attempt_id,),
        )
        versions = await cur.fetchall()
    assert adopted is not None and adopted["state"] == "adopted"
    assert adopted["implementation_attempt_id"] == attempt.implementation_attempt_id
    # The full checks ran on the workspace holding exactly the published tree.
    assert versions[0]["full_checks_source_digest"] == versions[0]["source_digest"]
    print(
        "BUILD_SPEED_TIMINGS "
        + json.dumps(
            {
                "workspace_preparation_seconds": round(preparation_seconds, 1),
                "draft_seconds": round(draft_seconds, 1),
                "agreement_to_published_seconds": round(published_at[0] - agreed_at, 2),
                "attempt_to_published_seconds": round(published_at[0] - attempt_started, 2),
                "published_to_full_checks_seconds": round(finished_at - published_at[0], 1),
                "versions": [
                    {
                        "full_checks_state": row["full_checks_state"],
                        "full_checks": [
                            [run["name"], run["exit_code"], round(run["duration_seconds"], 1)]
                            for run in row["full_checks"] or []
                        ],
                    }
                    for row in versions
                ],
                "outcome": result.outcome.value,
                "full_checks_passed": result.full_checks_passed,
                "reason": result.reason,
            }
        )
    )
