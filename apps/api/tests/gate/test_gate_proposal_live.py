"""The typed cited gate proposal, against the real OpenAI API.

The proposal is advisory. Whatever the model recommends, the deterministic
controller validates every citation and screens the proposal before anything is
committed.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.connectors.openai_roles import OpenAIRole, openai_gateway_for
from app.db.pool import Database
from app.gate.bundle import assemble_gate_bundle
from app.gate.controller import decide_gate
from app.gate.proposal import request_gate_proposal
from app.gate.screen import validate_citations
from tests.conftest import require_env
from tests.gate.factories import SHOPALYTICS_BASE_REVISION, gate_policy, prepare_gated_case
from tests.supabase_auth import SignedInUser

MAX_OUTPUT_TOKENS = 16_000


async def test_the_model_proposes_a_cited_decision_the_controller_then_owns(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The live path: the model proposes, the deterministic controller decides."""
    require_env("OPENAI_API_KEY")
    case, _, _ = await prepare_gated_case(
        database, auth_users["owner_a"].auth_user_id, "opus-gate", policy=gate_policy()
    )
    bundle = await assemble_gate_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        now=datetime.now(tz=UTC),
    )
    async with openai_gateway_for(OpenAIRole.GATE_PROPOSAL) as gateway:
        proposal = await request_gate_proposal(
            gateway, bundle=bundle, max_output_tokens=MAX_OUTPUT_TOKENS
        )
    findings = validate_citations(bundle, proposal)
    outcome = await decide_gate(
        database,
        bundle=bundle,
        proposal=proposal,
        base_revision=SHOPALYTICS_BASE_REVISION,
        now=datetime.now(tz=UTC),
    )
    if findings:
        assert outcome.decision.value == "no_implementation"
    assert outcome.gate_decision_id is not None
