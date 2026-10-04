"""Bundle assembly and the durable Implementation Gate decision.

These run against the real database: the gate's guarantees are transactional and
its records are immutable, so only real rows prove them. Every decision path is
committed and read back.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from utrace_contracts.domain.lifecycle import GateDecisionKind, ProductSignalState

from app.db.pool import Database
from app.db.rows import as_int, as_optional_str, as_str
from app.gate.bundle import assemble_gate_bundle
from app.gate.controller import decide_gate
from app.gate.proposal import GateBlocker, GateProposal, ProposedChange
from app.gate.scope import ApprovedScope
from app.ids import uuid7
from tests.gate.factories import (
    SHOPALYTICS_BASE_REVISION,
    create_gated_case,
    gate_policy,
    prepare_gated_case,
)
from tests.supabase_auth import SignedInUser


def _proposal(
    *,
    decision: str,
    evidence_id: str,
    finding_id: str,
    path: str = "src/lib/dashboard/saved-views.ts",
    estimated_elapsed_seconds: int = 900,
) -> GateProposal:
    return GateProposal(
        recommended_decision=decision,  # type: ignore[arg-type]
        intended_outcome="save the current conversion filter stack under a name",
        in_scope_changes=["persist saved conversion views for the Dudulemon account"],
        excluded_changes=["sharing", "rename", "deletion", "a dashboard redesign"],
        proposed_changes=[
            ProposedChange(
                path=path,
                change_kind="create",
                rationale="the filter stack is not persisted anywhere today",
                cited_finding_ids=[finding_id],
            )
        ],
        dependency_additions=[],
        cited_evidence_ids=[evidence_id],
        cited_finding_ids=[finding_id],
        live_window_fits=True,
        estimated_elapsed_seconds=estimated_elapsed_seconds,
        blocker=None,
        blocker_detail=None,
    )


async def _decision_row(database: Database, decision_id: object) -> dict[str, object]:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select decision, blocker_reason, approved_scope, required_checks, budgets,
                   cited_evidence, classification_revision
            from public.gate_decisions where id = %s
            """,
            (decision_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    return dict(row)


async def test_the_bundle_reads_every_input_the_gate_contract_names(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Assembly is deterministic and comes only from durable rows."""
    case, evidence_id, finding_id = await prepare_gated_case(
        database, auth_users["owner_a"].auth_user_id, "gate-bundle"
    )
    bundle = await assemble_gate_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        now=datetime.now(tz=UTC),
    )
    assert bundle.product_signal_state is ProductSignalState.GATE_PENDING
    assert bundle.established_flow_type is not None
    assert len(bundle.acceptance_criteria) == 1
    assert {str(item.evidence_id) for item in bundle.confirmed_evidence} == {evidence_id}
    assert {str(item.finding_id) for item in bundle.code_findings} == {finding_id}
    assert bundle.code_findings[0].base_revision == SHOPALYTICS_BASE_REVISION
    assert bundle.readiness.prepared is True
    assert bundle.implementation_count == 0
    assert bundle.policy.repository_ref == "ShopalyticsStore/shopalytics"


async def test_approved_live_freezes_the_scope(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A live approval narrows the pinned policy into the frozen scope."""
    case, evidence_id, finding_id = await prepare_gated_case(
        database, auth_users["owner_a"].auth_user_id, "gate-live"
    )
    bundle = await assemble_gate_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        now=datetime.now(tz=UTC),
    )
    outcome = await decide_gate(
        database,
        bundle=bundle,
        proposal=_proposal(
            decision="approved_live", evidence_id=evidence_id, finding_id=finding_id
        ),
        base_revision=SHOPALYTICS_BASE_REVISION,
        now=datetime.now(tz=UTC),
    )
    assert outcome.decision is GateDecisionKind.APPROVED_LIVE
    assert outcome.findings == ()
    scope = outcome.approved_scope
    assert scope is not None
    assert scope.repository_ref == "ShopalyticsStore/shopalytics"
    assert scope.base_revision == SHOPALYTICS_BASE_REVISION
    assert scope.blocked_paths == ("src/lib/utrace", "src/app/api", "db")
    assert [check.name for check in scope.required_checks] == ["lint", "typecheck", "test"]
    # Live work is bounded by the live window, not by the overall elapsed budget.
    assert scope.budgets.elapsed_seconds == 1800

    row = await _decision_row(database, outcome.gate_decision_id)
    assert as_str(row, "decision") == GateDecisionKind.APPROVED_LIVE.value
    assert as_optional_str(row, "blocker_reason") is None
    rebuilt = ApprovedScope.from_columns(
        approved_scope=row["approved_scope"],  # type: ignore[arg-type]
        required_checks=row["required_checks"],  # type: ignore[arg-type]
        budgets=row["budgets"],  # type: ignore[arg-type]
    )
    assert rebuilt.digest() == scope.digest()


async def test_the_committed_decision_is_immutable(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A frozen decision cannot be rewritten by a later revision."""
    case, evidence_id, finding_id = await prepare_gated_case(
        database, auth_users["owner_a"].auth_user_id, "gate-immutable"
    )
    bundle = await assemble_gate_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        now=datetime.now(tz=UTC),
    )
    outcome = await decide_gate(
        database,
        bundle=bundle,
        proposal=_proposal(
            decision="approved_live", evidence_id=evidence_id, finding_id=finding_id
        ),
        base_revision=SHOPALYTICS_BASE_REVISION,
        now=datetime.now(tz=UTC),
    )
    with pytest.raises(Exception, match="immutable|not allowed|reject"):
        async with database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                "update public.gate_decisions set decision = 'no_implementation' where id = %s",
                (outcome.gate_decision_id,),
            )


async def test_an_uncited_reference_commits_no_implementation_with_a_typed_blocker(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A citation the bundle does not carry denies the whole proposal."""
    case, _, finding_id = await prepare_gated_case(
        database, auth_users["owner_a"].auth_user_id, "gate-uncited"
    )
    bundle = await assemble_gate_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        now=datetime.now(tz=UTC),
    )
    outcome = await decide_gate(
        database,
        bundle=bundle,
        proposal=_proposal(
            decision="approved_live", evidence_id=str(uuid7()), finding_id=finding_id
        ),
        base_revision=SHOPALYTICS_BASE_REVISION,
        now=datetime.now(tz=UTC),
    )
    assert outcome.decision is GateDecisionKind.NO_IMPLEMENTATION
    assert outcome.blocker is GateBlocker.UNVERIFIABLE_CITATION
    assert outcome.approved_scope is None
    row = await _decision_row(database, outcome.gate_decision_id)
    assert as_optional_str(row, "blocker_reason") is not None
    assert "unverifiable_citation" in as_str(row, "blocker_reason")


async def test_a_blocked_surface_proposal_denies_implementation(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A proposal that would change a blocked surface never becomes an approval."""
    case, evidence_id, finding_id = await prepare_gated_case(
        database, auth_users["owner_a"].auth_user_id, "gate-blocked"
    )
    bundle = await assemble_gate_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        now=datetime.now(tz=UTC),
    )
    outcome = await decide_gate(
        database,
        bundle=bundle,
        proposal=_proposal(
            decision="approved_live",
            evidence_id=evidence_id,
            finding_id=finding_id,
            path="src/lib/utrace/chart-state.ts",
        ),
        base_revision=SHOPALYTICS_BASE_REVISION,
        now=datetime.now(tz=UTC),
    )
    assert outcome.decision is GateDecisionKind.NO_IMPLEMENTATION
    assert outcome.blocker is GateBlocker.BLOCKED_CODE_SURFACE


async def test_an_unprepared_environment_denies_implementation(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The gate cannot approve work whose environment is not ready."""
    case, evidence_id, finding_id = await prepare_gated_case(
        database, auth_users["owner_a"].auth_user_id, "gate-unready", allocation_state="pending"
    )
    bundle = await assemble_gate_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        now=datetime.now(tz=UTC),
    )
    outcome = await decide_gate(
        database,
        bundle=bundle,
        proposal=_proposal(
            decision="approved_live", evidence_id=evidence_id, finding_id=finding_id
        ),
        base_revision=SHOPALYTICS_BASE_REVISION,
        now=datetime.now(tz=UTC),
    )
    assert outcome.decision is GateDecisionKind.NO_IMPLEMENTATION
    assert outcome.blocker is GateBlocker.ENVIRONMENT_NOT_READY


async def test_an_incomplete_pinned_policy_refuses_assembly(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A release without a usable implementation policy fails with the release named."""
    case = await create_gated_case(
        database,
        name="gate-policy",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        policy={"live_implementation_allowed": True},
        state=ProductSignalState.GATE_PENDING,
    )
    with pytest.raises(Exception, match="missing or invalid"):
        await assemble_gate_bundle(
            database,
            client_id=case.client_id,
            product_signal_id=case.product_signal_id,
            now=datetime.now(tz=UTC),
        )


async def test_every_decision_writes_its_evidence_window_and_audit_record(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A decision is reconstructable: its window, its citations, and its audit fact."""
    case, evidence_id, finding_id = await prepare_gated_case(
        database, auth_users["owner_a"].auth_user_id, "gate-audit"
    )
    bundle = await assemble_gate_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        now=datetime.now(tz=UTC),
    )
    outcome = await decide_gate(
        database,
        bundle=bundle,
        proposal=_proposal(
            decision="approved_live", evidence_id=evidence_id, finding_id=finding_id
        ),
        base_revision=SHOPALYTICS_BASE_REVISION,
        now=datetime.now(tz=UTC),
    )
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as total from public.evidence_windows where id = %s",
            (outcome.evidence_window_id,),
        )
        windows = await cur.fetchone()
        await cur.execute(
            """
            select outcome from public.audit_records
            where subject_id = %s and action = 'implementation_gate_decision'
            """,
            (case.product_signal_id,),
        )
        audit = await cur.fetchone()
        await cur.execute(
            """
            select count(*) as total from public.events
            where stream_id = %s and event_type = 'implementation_gate_decided'
            """,
            (case.product_signal_id,),
        )
        events = await cur.fetchone()
    assert windows is not None and as_int(windows, "total") == 1
    assert audit is not None and as_str(audit, "outcome") == GateDecisionKind.APPROVED_LIVE.value
    assert events is not None and as_int(events, "total") == 1
    row = await _decision_row(database, outcome.gate_decision_id)
    cited = row["cited_evidence"]
    assert isinstance(cited, dict)
    assert cited["evidence_ids"] == [evidence_id]
    assert cited["code_finding_ids"] == [finding_id]


async def test_a_policy_denying_live_implementation_denies_implementation(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Deny wins: no live implementation path means no implementation, whatever is proposed."""
    case, evidence_id, finding_id = await prepare_gated_case(
        database,
        auth_users["owner_a"].auth_user_id,
        "gate-nomode",
        policy=gate_policy(live_implementation_allowed=False),
    )
    bundle = await assemble_gate_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        now=datetime.now(tz=UTC),
    )
    assert bundle.policy.live_implementation_allowed is False
    outcome = await decide_gate(
        database,
        bundle=bundle,
        proposal=_proposal(
            decision="approved_live", evidence_id=evidence_id, finding_id=finding_id
        ),
        base_revision=SHOPALYTICS_BASE_REVISION,
        now=datetime.now(tz=UTC),
    )
    assert outcome.decision is GateDecisionKind.NO_IMPLEMENTATION
    assert outcome.blocker is GateBlocker.POLICY_DENIED
