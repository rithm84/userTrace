"""GPT-6.1 Sol drafting a Case File's prose from a frozen bundle, against the OpenAI API.

The deterministic assembler, the citation check, sealing, and publication are
proved in ``test_case_file``; this proves that the model's drafted prose passes the
same code-owned citation and redaction checks.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from utrace_contracts.domain.lifecycle import (
    CaseFileVariant,
    CriterionObservability,
    ProductSignalState,
)

from app.connectors.openai_roles import OpenAIRole, openai_gateway_for
from app.db.pool import Database
from app.outputs.bundle import build_bundle
from app.outputs.core_builder import build_core, verify_core
from app.outputs.drafting import CaseFileDrafter, verify_narrative
from app.validation.evidence import DurableEvidenceQuery
from tests.conftest import require_env
from tests.supabase_auth import SignedInUser
from tests.validation.scenario import (
    add_intake_source,
    add_transcript_fragment,
    build_scenario,
)

ONE_CRITERION: tuple[tuple[str, CriterionObservability], ...] = (
    ("the saved view reopens with every filter", CriterionObservability.OBSERVED),
)


async def test_sol_drafts_a_case_file_whose_citations_code_can_check(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """GPT-6.1 Sol's prose is accepted only after code verifies every citation it names."""
    require_env("OPENAI_API_KEY")
    scenario = await build_scenario(
        database,
        name="opus-drafting",
        contact_budget=2,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=auth_users["participant"].auth_user_id,
        criteria=ONE_CRITERION,
        signal_state=ProductSignalState.AWAITING_VALIDATION,
        candidate_ready=True,
    )
    await add_intake_source(
        database,
        scenario,
        summary="A growth lead asks to save a conversion filter stack and reopen it.",
    )
    await add_transcript_fragment(
        database,
        scenario,
        field="expected_behavior",
        summary="the filter stack should survive a refresh",
    )
    await add_transcript_fragment(
        database,
        scenario,
        field="actual_behavior",
        summary="leaving the dashboard clears the applied filters",
    )
    window = await DurableEvidenceQuery(database).freeze_window(
        client_id=scenario.client_id,
        product_signal_id=scenario.product_signal_id,
        interview_session_id=None,
        purpose="case_file",
        starts_at=datetime.now(tz=UTC) - timedelta(hours=2),
        ends_at=datetime.now(tz=UTC) + timedelta(minutes=5),
    )
    async with database.connection() as conn:
        bundle = await build_bundle(
            conn,
            client_id=scenario.client_id,
            product_signal_id=scenario.product_signal_id,
            variant=CaseFileVariant.BLOCKED,
            evidence_window_id=window.evidence_window_id,
            validation_attempt_id=None,
            blocker_reason="the originating user did not return before the deadline",
        )

    async with openai_gateway_for(OpenAIRole.CASE_FILE_SYNTHESIS) as gateway:
        narrative = await CaseFileDrafter(gateway).draft(bundle)

    verify_narrative(narrative, bundle)
    core = build_core(
        bundle=bundle,
        narrative=narrative,
        comparison=None,
        delivery=None,
        narrative_source="model_drafted",
    )
    verify_core(core, bundle=bundle, forbidden_identifiers=("Maya Okonkwo",))
    assert core.metadata.narrative_source == "model_drafted"
    assert core.summary.concern.citations
