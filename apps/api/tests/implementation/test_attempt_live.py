"""The Implementation Attempt, end to end, against real Vercel Sandbox and Supabase.

A deterministic driver applies a fixed Saved Conversion Views patch through the
same typed tool executor the runner uses, in place of the Codex author. That exercises the workspace, the pinned
checkout, the lease fencing, the budgets, the diff-versus-approved-scope check,
the required checks on the clean base and on the candidate, the artifact
extraction, and the Preview Version publication. The one thing it does not
exercise is the model's choice of what to write: the patch is fixed, so the run
is reproducible and the assertions are about the pipeline rather than about a
model's output.

The fast tests here swap the four Shopalytics check commands for one trivial
command, because the check set is pinned client policy and running `next build`
four times to prove a scope violation would only cost time.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from psycopg.errors import UniqueViolation
from utrace_contracts.domain.lifecycle import (
    ImplementationAttemptState,
    ImplementationProgress,
    ProductSignalState,
)

from app.connectors.vercel_sandboxes import VercelSandboxes
from app.db.pool import Database
from app.db.rows import as_int, as_str
from app.errors import LeaseLostError
from app.gate.policy import load_implementation_policy
from app.gate.scope import RequiredCheck
from app.ids import uuid7
from app.implementation.artifact_store import ArtifactStore
from app.implementation.attempt import AttemptRunner, WorkspaceDependencies
from app.implementation.budgets import BudgetLedger, ConsumedBudgets
from app.implementation.prepared import prepare_case_workspace
from app.implementation.scope_check import ViolationKind
from tests.gate.factories import (
    GatedCase,
    create_gated_case,
    implementation_policy,
    shopalytics_checks,
)
from tests.implementation.driver import (
    MODIFIED_PATHS,
    SAVED_VIEWS_PATCH,
    RecordingPreviewRuntime,
    ScopeChangeAuthor,
    ScriptedChangeAuthor,
    Secret,
    blocked_path_patch,
    load_patch,
)
from tests.implementation.factories import (
    SHOPALYTICS_BUILD_RECIPE,
    AuthorizedAttempt,
    authorize_attempt,
    saved_views_scope,
)
from tests.implementation.factories import shopalytics_checks as scope_checks
from tests.supabase_auth import SignedInUser

TRIVIAL_CHECK = (
    RequiredCheck(
        name="toolchain",
        command="node --version",
        working_directory=".",
        timeout_seconds=120,
        after=(),
    ),
)
"""One real command that proves the toolchain without paying for a full build."""


async def _case(
    database: Database, owner: SignedInUser, name: str, workspace_snapshot_id: str
) -> GatedCase:
    return await create_gated_case(
        database,
        name=name,
        authorizing_auth_user_id=owner.auth_user_id,
        policy=implementation_policy(
            live_implementation_allowed=True,
            permitted_paths=["src/lib/dashboard", "src/components/dashboard", "tests/unit"],
            blocked_paths=["src/lib/utrace", "src/app/api", "db", "scripts"],
            required_checks=shopalytics_checks(),
            dependency_policy="none",
            live_window_seconds=3600,
            correction_cycles=2,
            changed_files=12,
            workspace_snapshot_id=workspace_snapshot_id,
            elapsed_seconds=3600,
            process_executions=200,
        ),
        state=ProductSignalState.IMPLEMENTING,
    )


def _runner(
    *,
    database: Database,
    sandboxes: VercelSandboxes,
    artifact_store: ArtifactStore,
    runtime: RecordingPreviewRuntime,
    author: object,
    attempt: AuthorizedAttempt,
    github_token: Secret,
    openai_key: Secret,
) -> AttemptRunner:
    return AttemptRunner(
        database=database,
        dependencies=WorkspaceDependencies(
            sandboxes=sandboxes,
            artifacts=artifact_store,
            preview_runtime=runtime,
            change_author=author,  # type: ignore[arg-type]
        ),
        specification=attempt.specification,
        build_recipe=SHOPALYTICS_BUILD_RECIPE,
        repository_access_token=github_token.value,
        openai_api_key=openai_key.value,
        ledger=BudgetLedger(
            budgets=attempt.specification.approved_scope.budgets,
            already_consumed=ConsumedBudgets.empty(),
            started_at=datetime.now(tz=UTC),
        ),
    )


async def test_the_fixed_patch_reaches_a_candidate_ready_preview_version(
    database: Database,
    sandboxes: VercelSandboxes,
    artifact_store: ArtifactStore,
    auth_users: dict[str, SignedInUser],
    workspace_snapshot_id: str,
    github_token: Secret,
    openai_key: Secret,
) -> None:
    """The complete pipeline: checkout, baseline, patch, checks, artifacts, candidate."""
    case = await _case(database, auth_users["owner_a"], "attempt-candidate", workspace_snapshot_id)
    attempt = await authorize_attempt(
        database,
        case=case,
        scope=saved_views_scope(checks=scope_checks()),
        workspace_snapshot_id=workspace_snapshot_id,
    )
    runtime = RecordingPreviewRuntime(database)
    author = ScriptedChangeAuthor(
        files=load_patch(SAVED_VIEWS_PATCH), modified_paths=MODIFIED_PATHS
    )
    result = await _runner(
        database=database,
        sandboxes=sandboxes,
        artifact_store=artifact_store,
        runtime=runtime,
        author=author,
        attempt=attempt,
        github_token=github_token,
        openai_key=openai_key,
    ).run()

    assert result.outcome is ImplementationAttemptState.CANDIDATE_READY, result.reason
    assert result.workspace_destroyed is True
    assert author.cycles == 1
    # The preview built the staged files while the checks ran, and the deployment
    # adopted exactly that build.
    assert len(runtime.prebuilt) == 1
    assert runtime.adopted == runtime.prebuilt
    assert runtime.discarded == []
    assert set(runtime.prebuilt[0].file_digests) == set(SAVED_VIEWS_PATCH)

    # The baseline ran on the clean base and the candidate introduced no new failure.
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select phase, check_name, exit_code from public.implementation_check_runs
            where implementation_attempt_id = %s order by phase, check_name
            """,
            (attempt.implementation_attempt_id,),
        )
        runs = await cur.fetchall()
    by_phase = {
        (as_str(row, "phase"), as_str(row, "check_name")): as_int(row, "exit_code")
        for row in runs
    }
    for name in ("lint", "typecheck", "test", "build"):
        assert by_phase[("baseline", name)] == 0, f"the clean base failed {name}"
        assert by_phase[("candidate", name)] == 0, f"the candidate failed {name}"

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select check_summary from public.preview_versions where implementation_attempt_id = %s",
            (attempt.implementation_attempt_id,),
        )
        summary_row = await cur.fetchone()
    assert summary_row is not None
    summary = summary_row["check_summary"]
    assert isinstance(summary, dict)
    assert summary["new_failures"] == []
    assert summary["pre_existing_failures"] == []

    # The scope check passed against the exact frozen scope.
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select passed, scope_digest, changed_paths
            from public.implementation_scope_checks
            where implementation_attempt_id = %s
            """,
            (attempt.implementation_attempt_id,),
        )
        checks = await cur.fetchall()
    assert len(checks) == 1
    assert checks[0]["passed"] is True
    assert as_str(checks[0], "scope_digest") == attempt.specification.scope_digest()
    assert set(checks[0]["changed_paths"]) == set(SAVED_VIEWS_PATCH)

    # Every required artifact is durable and content-addressed before cleanup.
    manifest = result.manifest
    assert manifest is not None
    types = {reference.artifact_type for reference in manifest.references()}
    assert types >= {
        "implementation_final_diff",
        "implementation_revision_patch",
        "implementation_tool_journal",
        "implementation_check_results",
        "implementation_check_log",
        "implementation_inspection_evidence",
        "implementation_scope_checks",
        "implementation_dependency_manifest",
        "implementation_source_provenance",
        "implementation_build_provenance",
    }
    assert all(len(digest) == 64 for digest in manifest.digests())

    # The Preview Version binds this attempt and its exact source and build identity.
    handoff = runtime.only_handoff()
    assert handoff.implementation_attempt_id == attempt.implementation_attempt_id
    assert handoff.approved_scope_digest == attempt.specification.scope_digest()
    assert handoff.source.base_revision == attempt.specification.checkout.base_revision
    assert handoff.source.patch.artifact_type == "implementation_final_diff"
    assert set(handoff.source.changed_paths) == set(SAVED_VIEWS_PATCH)
    assert handoff.build.install_command == SHOPALYTICS_BUILD_RECIPE.install_command
    assert handoff.dependencies.lockfile_path == "package-lock.json"
    assert handoff.build_digest
    assert handoff.source.source_digest

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select state, source_digest, build_digest, artifact_manifest_digest,
                   implementation_attempt_id
            from public.preview_versions where id = %s
            """,
            (handoff.preview_version_id,),
        )
        version = await cur.fetchone()
        await cur.execute(
            "select progress from public.implementation_attempts where id = %s",
            (attempt.implementation_attempt_id,),
        )
        progress = await cur.fetchone()
    assert version is not None
    assert as_str(version, "source_digest") == handoff.source.source_digest
    assert as_str(version, "build_digest") == handoff.build_digest
    assert as_str(version, "artifact_manifest_digest")
    assert progress is not None
    assert as_str(progress, "progress") == ImplementationProgress.READY_FOR_VALIDATION.value


async def test_the_attempt_claims_the_workspace_prepared_during_the_interview(
    database: Database,
    sandboxes: VercelSandboxes,
    artifact_store: ArtifactStore,
    auth_users: dict[str, SignedInUser],
    workspace_snapshot_id: str,
    github_token: Secret,
    openai_key: Secret,
) -> None:
    """A workspace prepared from the case's release is claimed under the attempt's lease."""
    policy = implementation_policy(
        live_implementation_allowed=True,
        permitted_paths=["src/lib/dashboard", "src/components/dashboard", "tests/unit"],
        blocked_paths=["src/lib/utrace", "src/app/api", "db", "scripts"],
        required_checks=[check.model_dump(mode="json") for check in TRIVIAL_CHECK],
        dependency_policy="none",
        live_window_seconds=3600,
        correction_cycles=2,
        changed_files=12,
        workspace_snapshot_id=workspace_snapshot_id,
        elapsed_seconds=3600,
        process_executions=200,
    )
    policy["build_recipe"] = SHOPALYTICS_BUILD_RECIPE.model_dump(mode="json")
    case = await create_gated_case(
        database,
        name="attempt-prepared",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        policy=policy,
        state=ProductSignalState.IMPLEMENTING,
    )
    await prepare_case_workspace(
        database,
        sandboxes,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        policy=await load_implementation_policy(
            database, configuration_release_id=case.configuration_release_id
        ),
        repository_token=github_token.value,
    )
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state, baseline_checks from public.prepared_workspaces "
            "where product_signal_id = %s",
            (case.product_signal_id,),
        )
        prepared = await cur.fetchone()
    assert prepared is not None
    assert as_str(prepared, "state") == "ready"
    assert [(run["name"], run["exit_code"]) for run in prepared["baseline_checks"]] == [
        ("toolchain", 0)
    ]

    attempt = await authorize_attempt(
        database,
        case=case,
        scope=saved_views_scope(checks=TRIVIAL_CHECK),
        workspace_snapshot_id=workspace_snapshot_id,
    )
    runtime = RecordingPreviewRuntime(database)
    result = await _runner(
        database=database,
        sandboxes=sandboxes,
        artifact_store=artifact_store,
        runtime=runtime,
        author=ScriptedChangeAuthor(
            files=load_patch(SAVED_VIEWS_PATCH), modified_paths=MODIFIED_PATHS
        ),
        attempt=attempt,
        github_token=github_token,
        openai_key=openai_key,
    ).run()

    assert result.outcome is ImplementationAttemptState.CANDIDATE_READY, result.reason
    assert result.workspace_destroyed is True
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state, implementation_attempt_id, lease_fencing_token "
            "from public.prepared_workspaces where product_signal_id = %s",
            (case.product_signal_id,),
        )
        claimed = await cur.fetchone()
        await cur.execute(
            "select phase, check_name, exit_code from public.implementation_check_runs "
            "where implementation_attempt_id = %s order by phase",
            (attempt.implementation_attempt_id,),
        )
        runs = await cur.fetchall()
    assert claimed is not None
    assert as_str(claimed, "state") == "claimed"
    assert claimed["implementation_attempt_id"] == attempt.implementation_attempt_id
    assert claimed["lease_fencing_token"] == attempt.specification.lease.fencing_token
    # The baseline the preparation ran is the attempt's baseline.
    assert [(as_str(row, "phase"), as_str(row, "check_name"), row["exit_code"]) for row in runs] == [
        ("baseline", "toolchain", 0),
        ("candidate", "toolchain", 0),
    ]


async def test_a_blocked_path_violation_is_caught_after_the_edit_cycle(
    database: Database,
    sandboxes: VercelSandboxes,
    artifact_store: ArtifactStore,
    auth_users: dict[str, SignedInUser],
    workspace_snapshot_id: str,
    github_token: Secret,
    openai_key: Secret,
) -> None:
    """A blocked surface is writable in the workspace but never in the accepted diff."""
    case = await _case(database, auth_users["owner_a"], "attempt-blocked", workspace_snapshot_id)
    attempt = await authorize_attempt(
        database,
        case=case,
        scope=saved_views_scope(checks=TRIVIAL_CHECK, correction_cycles=1),
        workspace_snapshot_id=workspace_snapshot_id,
    )
    runtime = RecordingPreviewRuntime(database)
    result = await _runner(
        database=database,
        sandboxes=sandboxes,
        artifact_store=artifact_store,
        runtime=runtime,
        author=ScriptedChangeAuthor(files=blocked_path_patch(), modified_paths=()),
        attempt=attempt,
        github_token=github_token,
        openai_key=openai_key,
    ).run()

    assert result.outcome is ImplementationAttemptState.SCOPE_CHANGE_REQUIRED
    assert runtime.handoffs == []

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select passed, violations, changed_paths
            from public.implementation_scope_checks
            where implementation_attempt_id = %s
            """,
            (attempt.implementation_attempt_id,),
        )
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["passed"] is False
    violations = rows[0]["violations"]
    assert isinstance(violations, list)
    assert [item["kind"] for item in violations] == [ViolationKind.BLOCKED_CODE_SURFACE.value]
    assert [item["subject"] for item in violations] == ["src/lib/utrace/chart-state.ts"]

    # A refused candidate still leaves its evidence durable.
    assert result.manifest is not None
    assert result.workspace_destroyed is True


async def test_a_scope_change_discovery_stops_mutation_and_returns_to_the_gate(
    database: Database,
    sandboxes: VercelSandboxes,
    artifact_store: ArtifactStore,
    auth_users: dict[str, SignedInUser],
    workspace_snapshot_id: str,
    github_token: Secret,
    openai_key: Secret,
) -> None:
    """Scope never grows inside the workspace: the discovery goes back to the gate."""
    case = await _case(database, auth_users["owner_a"], "attempt-scope", workspace_snapshot_id)
    attempt = await authorize_attempt(
        database,
        case=case,
        scope=saved_views_scope(checks=TRIVIAL_CHECK, correction_cycles=1),
        workspace_snapshot_id=workspace_snapshot_id,
    )
    runtime = RecordingPreviewRuntime(database)
    result = await _runner(
        database=database,
        sandboxes=sandboxes,
        artifact_store=artifact_store,
        runtime=runtime,
        author=ScopeChangeAuthor(reason="the saved view needs a server-side table"),
        attempt=attempt,
        github_token=github_token,
        openai_key=openai_key,
    ).run()

    assert result.outcome is ImplementationAttemptState.SCOPE_CHANGE_REQUIRED
    assert result.reason == "the saved view needs a server-side table"
    assert runtime.handoffs == []


async def test_an_exhausted_process_budget_ends_the_attempt(
    database: Database,
    sandboxes: VercelSandboxes,
    artifact_store: ArtifactStore,
    auth_users: dict[str, SignedInUser],
    workspace_snapshot_id: str,
    github_token: Secret,
    openai_key: Secret,
) -> None:
    """A budget refusal is a typed outcome, never a silently shortened run."""
    case = await _case(database, auth_users["owner_a"], "attempt-budget", workspace_snapshot_id)
    attempt = await authorize_attempt(
        database,
        case=case,
        scope=saved_views_scope(checks=TRIVIAL_CHECK, process_executions=1),
        workspace_snapshot_id=workspace_snapshot_id,
    )
    runtime = RecordingPreviewRuntime(database)
    result = await _runner(
        database=database,
        sandboxes=sandboxes,
        artifact_store=artifact_store,
        runtime=runtime,
        author=ScriptedChangeAuthor(
            files=load_patch(SAVED_VIEWS_PATCH), modified_paths=MODIFIED_PATHS
        ),
        attempt=attempt,
        github_token=github_token,
        openai_key=openai_key,
    ).run()

    assert result.outcome is ImplementationAttemptState.BUDGET_EXHAUSTED
    assert result.reason is not None
    assert "process_executions" in result.reason
    assert runtime.handoffs == []
    assert result.consumed.process_executions >= 1


async def test_a_stale_fencing_token_refuses_every_mutation(
    database: Database,
    sandboxes: VercelSandboxes,
    artifact_store: ArtifactStore,
    auth_users: dict[str, SignedInUser],
    workspace_snapshot_id: str,
    github_token: Secret,
    openai_key: Secret,
) -> None:
    """A superseded run may still read, but it can write nothing and publish nothing."""
    case = await _case(database, auth_users["owner_a"], "attempt-stale", workspace_snapshot_id)
    attempt = await authorize_attempt(
        database,
        case=case,
        scope=saved_views_scope(checks=TRIVIAL_CHECK),
        workspace_snapshot_id=workspace_snapshot_id,
    )
    # A newer lease supersedes this run's token, exactly as a recovery would.
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.environment_leases set state = 'fenced', revision = revision + 1
            where id = %s
            """,
            (attempt.lease_id,),
        )
        await cur.execute(
            """
            insert into public.environment_leases (
                id, client_id, implementation_attempt_id, state, fencing_token,
                expires_at
            )
            values (%s, %s, %s, 'active', utrace.next_fencing_token(),
                    now() + interval '1 hour')
            """,
            (uuid7(), case.client_id, attempt.implementation_attempt_id),
        )

    runtime = RecordingPreviewRuntime(database)
    try:
        with pytest.raises(LeaseLostError) as refusal:
            await _runner(
                database=database,
                sandboxes=sandboxes,
                artifact_store=artifact_store,
                runtime=runtime,
                author=ScriptedChangeAuthor(files=blocked_path_patch(), modified_paths=()),
                attempt=attempt,
                github_token=github_token,
                openai_key=openai_key,
            ).run()
        assert refusal.value.fencing_token == attempt.fencing_token
        assert runtime.handoffs == []
    finally:
        # The stale run destroys nothing, because the current lease holder owns
        # the workspace. The orphan is reconciled by the name its lease derived.
        orphan = await sandboxes.find(
            name=f"utrace-attempt-{attempt.implementation_attempt_id.hex}-{attempt.fencing_token}"
        )
        assert orphan is not None
        await sandboxes.delete(orphan)

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as total from public.preview_versions where implementation_attempt_id = %s",
            (attempt.implementation_attempt_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert as_int(row, "total") == 0


async def test_one_attempt_exists_per_product_signal(
    database: Database, auth_users: dict[str, SignedInUser], workspace_snapshot_id: str
) -> None:
    """A second concurrent attempt is refused by the schema, not by a convention."""
    case = await _case(database, auth_users["owner_a"], "attempt-concurrency", workspace_snapshot_id)
    scope = saved_views_scope(checks=TRIVIAL_CHECK)
    await authorize_attempt(
        database, case=case, scope=scope, workspace_snapshot_id=workspace_snapshot_id
    )
    with pytest.raises(UniqueViolation):
        await authorize_attempt(
            database, case=case, scope=scope, workspace_snapshot_id=workspace_snapshot_id
        )

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as total from public.implementation_attempts where product_signal_id = %s",
            (case.product_signal_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert as_int(row, "total") == 1
