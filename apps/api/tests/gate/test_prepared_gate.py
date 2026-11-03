"""The gate's evaluation, prepared while the agent proposes, stands only for what was agreed.

The proposal model's answer depends on the bundle alone, so a proposal prepared
over the bundle an agreement would leave is reused only when the agreement records
that proposal's draft and nothing the decision depends on has changed since.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import InterviewSessionState

from app.db.pool import Database
from app.gate.bundle import GateBundle, assemble_gate_bundle
from app.gate.prepared import decision_inputs_digest, predicted_bundle, prepared_gate_proposal
from app.gate.proposal import GateProposal
from app.ids import uuid7
from tests.factories import CaseFixture, create_interview_session
from tests.gate.factories import GatedCase, prepare_gated_case
from tests.gate.test_controller import _proposal
from tests.supabase_auth import SignedInUser


def _criteria(bundle: GateBundle) -> tuple[tuple[str, str], ...]:
    return tuple(
        (criterion.statement, criterion.observability) for criterion in bundle.acceptance_criteria
    )


async def _bundle(database: Database, case: GatedCase) -> GateBundle:
    return await assemble_gate_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        now=datetime.now(tz=UTC),
    )


async def _prepared(
    database: Database, *, case: GatedCase, proposal: GateProposal, digest: str
) -> UUID:
    """A proposal's draft carrying the gate's prepared evaluation."""
    session_id = await create_interview_session(
        database,
        case=cast(CaseFixture, case),
        state=InterviewSessionState.LIVE,
        conversational_ready=True,
    )
    draft_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.prepared_drafts (
                id, client_id, product_signal_id, interview_session_id, state,
                proposed_scope, criteria, proposal_digest, gate_proposal, gate_inputs_digest
            )
            values (%s, %s, %s, %s, 'drafting', '{}', '[]', 'sha256:proposal', %s, %s)
            """,
            (
                draft_id,
                case.client_id,
                case.product_signal_id,
                session_id,
                Jsonb(proposal.model_dump(mode="json")),
                digest,
            ),
        )
    return draft_id


async def _agree(database: Database, *, case: GatedCase, revision: int, draft_id: UUID) -> None:
    """Record the agreement at the bundle's revision, naming the draft."""
    bundle_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "select interview_session_id from public.prepared_drafts where id = %s", (draft_id,)
        )
        row = await cur.fetchone()
        assert row is not None
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
            values (%s, %s, %s, %s, %s, '{}', '{}', '{}', %s, %s)
            """,
            (
                uuid7(),
                case.client_id,
                case.product_signal_id,
                row["interview_session_id"],
                revision,
                bundle_id,
                draft_id,
            ),
        )


async def test_the_decision_digest_follows_what_the_gate_decides_on_and_nothing_that_moves(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case, _, _ = await prepare_gated_case(
        database, auth_users["owner_a"].auth_user_id, "prepared-gate-digest"
    )
    bundle = await _bundle(database, case)

    # Predicting the same criteria moves revisions, state, and criterion ids only.
    predicted = predicted_bundle(bundle, criteria=_criteria(bundle))
    assert predicted.acceptance_criteria_revision == bundle.acceptance_criteria_revision + 1
    assert decision_inputs_digest(predicted) == decision_inputs_digest(bundle)

    # Different agreed wording, or another attempt on the case, is another decision.
    reworded = predicted_bundle(bundle, criteria=(("a broader dashboard redesign", "observed"),))
    assert decision_inputs_digest(reworded) != decision_inputs_digest(bundle)
    counted = bundle.model_copy(update={"implementation_count": bundle.implementation_count + 1})
    assert decision_inputs_digest(counted) != decision_inputs_digest(bundle)


async def test_the_gate_reuses_a_prepared_proposal_only_for_the_unchanged_agreement(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case, evidence_id, finding_id = await prepare_gated_case(
        database, auth_users["owner_a"].auth_user_id, "prepared-gate-reuse"
    )
    bundle = await _bundle(database, case)
    proposal = _proposal(decision="approved_live", evidence_id=evidence_id, finding_id=finding_id)
    draft_id = await _prepared(
        database, case=case, proposal=proposal, digest=decision_inputs_digest(bundle)
    )

    # No agreement records the draft yet: the gate asks the model.
    assert await prepared_gate_proposal(database, bundle=bundle) is None

    await _agree(
        database, case=case, revision=bundle.acceptance_criteria_revision, draft_id=draft_id
    )
    assert await prepared_gate_proposal(database, bundle=bundle) == proposal

    # Evidence that changed after the proposal was prepared makes it stale.
    changed = bundle.model_copy(update={"confirmed_evidence": ()})
    assert await prepared_gate_proposal(database, bundle=changed) is None
