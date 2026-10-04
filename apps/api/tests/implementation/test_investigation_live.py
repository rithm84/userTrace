"""Read-only pre-gate code investigation against the real repository and model.

A real Vercel Sandbox workspace checks the authorized repository out at the pinned
base revision with the GitHub credential added at egress, then the investigation
model runs in Codex with only a placeholder API key inside the sandbox: the firewall
adds the real one. The run must leave the checkout untouched. Every reported finding is re-read by
code and recorded with the digest of its exact lines, and the sandbox is destroyed.
"""

from __future__ import annotations

from utrace_contracts.domain.lifecycle import ProductSignalState

from app.connectors.openai_roles import OpenAIRole, openai_configuration_for
from app.connectors.vercel_sandboxes import VercelSandboxes
from app.db.pool import Database
from app.db.rows import as_int, as_str
from app.implementation.investigation import InvestigationRequest, investigate_repository
from tests.gate.factories import (
    SHOPALYTICS_BASE_REVISION,
    SHOPALYTICS_CLONE_URL,
    SHOPALYTICS_REPOSITORY,
    create_gated_case,
    implementation_policy,
    shopalytics_checks,
)
from tests.implementation.driver import Secret
from tests.supabase_auth import SignedInUser


async def test_the_investigation_records_exact_revision_findings(
    database: Database,
    sandboxes: VercelSandboxes,
    auth_users: dict[str, SignedInUser],
    workspace_snapshot_id: str,
    github_token: Secret,
    openai_key: Secret,
) -> None:
    """Every finding names repository, revision, path, and the exact lines."""
    case = await create_gated_case(
        database,
        name="investigation",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        policy=implementation_policy(
            live_implementation_allowed=True,
            permitted_paths=["src"],
            blocked_paths=["db"],
            required_checks=shopalytics_checks(),
            dependency_policy="none",
            live_window_seconds=1800,
            correction_cycles=2,
            changed_files=10,
            workspace_snapshot_id=workspace_snapshot_id,
            elapsed_seconds=3600,
            process_executions=100,
        ),
        state=ProductSignalState.GATE_PENDING,
    )

    result = await investigate_repository(
        database,
        sandboxes,
        request=InvestigationRequest(
            client_id=case.client_id,
            product_signal_id=case.product_signal_id,
            repository_ref=SHOPALYTICS_REPOSITORY,
            clone_url=SHOPALYTICS_CLONE_URL,
            base_revision=SHOPALYTICS_BASE_REVISION,
            workspace_snapshot_id=workspace_snapshot_id,
            callback_hosts=(),
            concern=(
                "Users rebuild the same conversion dashboard filter stack every week and "
                "want to save it as a named view and reopen it later. Where is the filter "
                "state defined, held, and applied?"
            ),
        ),
        configuration=openai_configuration_for(OpenAIRole.CODE_INVESTIGATION),
        repository_token=github_token.value,
        openai_api_key=openai_key.value,
    )

    assert result.findings, "the investigation recorded no findings"
    paths = {finding.path for finding in result.findings}
    assert any(path.startswith("src/") for path in paths), paths
    for finding in result.findings:
        assert finding.repository_ref == SHOPALYTICS_REPOSITORY
        assert finding.base_revision == SHOPALYTICS_BASE_REVISION
        assert finding.first_line >= 1
        assert finding.last_line >= finding.first_line
        assert finding.excerpt_digest.startswith("sha256:")
        assert not finding.path.startswith("/")

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select count(*) as total from public.code_investigation_findings
            where product_signal_id = %s and investigation_id = %s
            """,
            (case.product_signal_id, result.investigation_id),
        )
        stored = await cur.fetchone()
        await cur.execute(
            """
            select source_ref, evidence_class from public.evidence_records
            where product_signal_id = %s
            """,
            (case.product_signal_id,),
        )
        evidence = await cur.fetchone()
    assert stored is not None
    assert as_int(stored, "total") == len(result.findings)
    assert evidence is not None
    assert as_str(evidence, "evidence_class") == "implementation_artifact"
    assert as_str(evidence, "source_ref") == (
        f"{SHOPALYTICS_REPOSITORY}@{SHOPALYTICS_BASE_REVISION}"
    )
