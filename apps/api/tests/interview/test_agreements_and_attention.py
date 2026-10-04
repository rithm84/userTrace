"""Flow classification, acceptance-criteria agreements, and live Jev attention.

The classification and agreement records are checked against the real schema.
The attention tests call the real Jev model through Vercel AI Gateway with the
pinned model id and check the recorded judgement, because a mocked classifier
would only prove the mock matched the guess. They skip with the missing variable
named.
"""

from uuid import UUID

import pytest
from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    FlowType,
    InterviewPhase,
    ProductSignalState,
)

from app.connectors.typesafe_jev import JevClient
from app.context.bundles import AssembledBundle, assemble_bundle
from app.db.pool import Database
from app.errors import GuardRejectedError, StaleContextError
from app.interview.agreements import (
    CriterionDraft,
    record_acceptance_criteria_agreement,
    record_flow_classification,
)
from app.interview.attention import (
    assess_evidence_coverage,
    propose_flow_classification,
    relate_claim,
    route_attention,
)
from app.interview.readiness import ExtensionReadiness, record_observation_facts
from tests.connectors.conftest import optional_env, require_env
from tests.factories import CaseFixture, create_case, create_gate_decision
from tests.interview.fixtures import create_live_session
from tests.supabase_auth import SignedInUser

_CANONICAL_REQUEST = (
    "I keep rebuilding the same conversion filters every Monday. Can Shopalytics let me "
    "save a view for TikTok traffic from women 25-34 on products with negative sizing "
    "reviews, then reopen it later?"
)


async def _case(
    database: Database, auth_users: dict[str, SignedInUser], name: str
) -> CaseFixture:
    """One routed Product Signal in its interviewing state."""
    return await create_case(
        database,
        name=name,
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )


async def _bundle(
    database: Database, *, case: CaseFixture, session_id: UUID, purpose: str
) -> AssembledBundle:
    """Assemble one current bundle for the case."""
    return await assemble_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        purpose=purpose,
        budget_items=32,
        observations=(),
        extra_items=(),
        recent_fragment_limit=10,
        prior_command_limit=5,
    )


async def test_the_interview_records_the_classification_it_established(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "classification-retained")
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )
    bundle = await _bundle(
        database, case=case, session_id=session_id, purpose="flow_classification"
    )
    record = await record_flow_classification(
        database,
        bundle=bundle,
        flow_type=FlowType.FEATURE_REQUEST_FLOW,
        rationale="the participant asks to save and reopen a filter stack",
        supporting_evidence_ids=(),
        semantic_judgement_id=None,
    )

    assert record.classification_revision == 1
    assert record.retained is True
    assert record.gate_hold_id is None

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select established_flow_type, classification_revision
            from public.product_signals where id = %s
            """,
            (case.product_signal_id,),
        )
        signal = await cur.fetchone()
        await cur.execute(
            """
            select event_type, payload from public.events
            where stream_id = %s and event_type = 'interview_flow_classification_recorded'
            """,
            (case.product_signal_id,),
        )
        event = await cur.fetchone()
    assert signal is not None
    assert signal["established_flow_type"] == FlowType.FEATURE_REQUEST_FLOW.value
    assert signal["classification_revision"] == 1
    assert event is not None
    assert event["payload"]["retained"] is True


async def test_correcting_a_used_classification_holds_new_implementation_steps(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "classification-corrected")
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )
    await create_gate_decision(
        database,
        case=case,
        decision="approved_live",
        classification_revision=0,
        blocker_reason=None,
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.evidence_records (
                id, client_id, product_signal_id, interview_session_id,
                evidence_class, claim_state, source_ref, context_revision, body
            )
            values (gen_random_uuid(), %s, %s, %s, 'transcript_fragment', 'confirmed',
                    'transcript:test:0-100', 0, '{"kind": "interview_claim"}'::jsonb)
            """,
            (case.client_id, case.product_signal_id, session_id),
        )

    bundle = await _bundle(
        database, case=case, session_id=session_id, purpose="flow_classification"
    )
    record = await record_flow_classification(
        database,
        bundle=bundle,
        flow_type=FlowType.BUGFIX_FLOW,
        rationale="the observed behaviour is a failure, not a missing capability",
        supporting_evidence_ids=(),
        semantic_judgement_id=None,
    )
    assert record.retained is False
    assert record.gate_hold_id is not None

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select hold_kind, reason, blocks_new_implementation_steps, released_at
            from public.dependency_holds where id = %s
            """,
            (record.gate_hold_id,),
        )
        hold = await cur.fetchone()
        await cur.execute(
            "select classification_revision, decision from public.gate_decisions "
            "where product_signal_id = %s",
            (case.product_signal_id,),
        )
        gate = await cur.fetchone()
    assert hold is not None
    assert hold["blocks_new_implementation_steps"] is True
    assert hold["released_at"] is None
    assert str(hold["reason"]).startswith("classification_revision_superseded")
    assert gate is not None
    assert gate["classification_revision"] == 0


async def test_a_stale_bundle_cannot_record_an_agreement(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "agreement-stale")
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )
    stale = await _bundle(
        database, case=case, session_id=session_id, purpose="voice_delegation"
    )
    await record_flow_classification(
        database,
        bundle=await _bundle(
            database, case=case, session_id=session_id, purpose="flow_classification"
        ),
        flow_type=FlowType.FEATURE_REQUEST_FLOW,
        rationale="established from the participant's own account",
        supporting_evidence_ids=(),
        semantic_judgement_id=None,
    )

    with pytest.raises(GuardRejectedError) as refusal:
        await record_acceptance_criteria_agreement(
            database,
            bundle=stale,
            agreed_scope={"intended_outcome": "save a view"},
            criteria=[
                CriterionDraft(
                    ordinal=0,
                    statement="the saved view reopens",
                    observability=CriterionObservability.OBSERVED,
                )
            ],
            supporting_evidence_ids=(),
        )
    assert "classification_revision" in str(refusal.value)


async def test_only_what_a_classification_rests_on_can_make_it_stale(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Telemetry readiness moving refuses no classification; a scope change still does."""
    case = await _case(database, auth_users, "classification-dependencies")
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )
    before_readiness = await _bundle(
        database, case=case, session_id=session_id, purpose="flow_classification"
    )
    # The browser stream reconnecting, as it did in the live session that failed.
    for browser_sdk_ready in (True, False, True):
        await record_observation_facts(
            database,
            interview_session_id=session_id,
            preview_allocation_ready=True,
            extension_readiness=ExtensionReadiness.READY,
            browser_sdk_ready=browser_sdk_ready,
            server_sdk_ready=True,
            trace_correlation_ready=True,
        )
    assert (
        await _bundle(database, case=case, session_id=session_id, purpose="flow_classification")
    ).dependencies.readiness_revision > before_readiness.dependencies.readiness_revision

    record = await record_flow_classification(
        database,
        bundle=before_readiness,
        flow_type=FlowType.FEATURE_REQUEST_FLOW,
        rationale="the participant asks to save and reopen a filter stack",
        supporting_evidence_ids=(),
        semantic_judgement_id=None,
    )
    assert record.classification_revision == 1

    before_scope = await _bundle(
        database, case=case, session_id=session_id, purpose="flow_classification"
    )
    await record_acceptance_criteria_agreement(
        database,
        bundle=await _bundle(
            database, case=case, session_id=session_id, purpose="voice_delegation"
        ),
        agreed_scope={"intended_outcome": "save and reopen a conversion view"},
        criteria=[
            CriterionDraft(
                ordinal=0,
                statement="the saved view reopens with every filter restored",
                observability=CriterionObservability.OBSERVED,
            )
        ],
        supporting_evidence_ids=(),
    )
    with pytest.raises(StaleContextError) as refusal:
        await record_flow_classification(
            database,
            bundle=before_scope,
            flow_type=FlowType.FEATURE_REQUEST_FLOW,
            rationale="judged before the participant agreed the scope",
            supporting_evidence_ids=(),
            semantic_judgement_id=None,
        )
    assert "acceptance_criteria_revision" in refusal.value.reason
    assert "readiness_revision" not in refusal.value.reason


async def test_jev_assesses_criterion_coverage_over_a_real_bundle(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    values = require_env("VERCEL_AI_GATEWAY_API_KEY")
    model_id = optional_env("UTRACE_JEV_MODEL_ID") or "typesafe-ai/jev"
    case = await _case(database, auth_users, "attention-coverage")
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.evidence_records (
                id, client_id, product_signal_id, interview_session_id,
                evidence_class, claim_state, source_ref, context_revision, body
            )
            values (gen_random_uuid(), %s, %s, %s, 'transcript_fragment', 'proposed',
                    'transcript:test:0-400', 0, %s::jsonb)
            """,
            (
                case.client_id,
                case.product_signal_id,
                session_id,
                '{"kind": "open_question", "question": "'
                + _CANONICAL_REQUEST.replace('"', "'")
                + '", "criterion_id": null}',
            ),
        )
    bundle = await _bundle(
        database, case=case, session_id=session_id, purpose="jev_attention"
    )

    async with JevClient(
        api_key=values["VERCEL_AI_GATEWAY_API_KEY"], model_id=model_id, timeout_seconds=30.0
    ) as jev:
        coverage = await assess_evidence_coverage(
            database,
            jev,
            bundle=bundle,
            criteria={
                "saved-view": "the complete filter stack can be saved under a name",
                "restores": "reopening the view restores every saved filter",
            },
        )
        relation = await relate_claim(
            database,
            jev,
            bundle=bundle,
            statement="I rebuild the same filters every Monday.",
            claims={"weekly": "The participant rebuilds the filter stack weekly."},
        )
        routing = await route_attention(
            database,
            jev,
            bundle=bundle,
            tasks={
                "look_up_saved_views": "Search the product docs for saved views.",
                "ask_about_frequency": "Ask how often the workflow repeats.",
            },
        )

    assert set(coverage.selections) == {"coverage::saved-view", "coverage::restores"}
    assert all(
        choice in {"addressed", "unanswered", "contradicted", "unclear"}
        for choice in coverage.selections.values()
    )
    assert relation.selections["relation::weekly"] in {
        "supports",
        "contradicts",
        "unrelated",
        "unclear",
    }
    assert routing.selections["attention::next_task"] in {
        "look_up_saved_views",
        "ask_about_frequency",
        "no_task",
    }

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select purpose, model_id, accepted, rejections, answers, context_bundle_id
            from public.semantic_judgements
            where product_signal_id = %s
            order by created_at
            """,
            (case.product_signal_id,),
        )
        judgements = [dict(row) for row in await cur.fetchall()]
    assert [row["purpose"] for row in judgements] == [
        "evidence_coverage",
        "claim_relationship",
        "attention_routing",
    ]
    assert {row["model_id"] for row in judgements} == {model_id}
    assert all(row["context_bundle_id"] == bundle.bundle_id for row in judgements)
    assert all(row["answers"] for row in judgements)


async def test_jev_proposes_the_interview_established_classification(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    values = require_env("VERCEL_AI_GATEWAY_API_KEY")
    model_id = optional_env("UTRACE_JEV_MODEL_ID") or "typesafe-ai/jev"
    case = await _case(database, auth_users, "attention-classification")
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.evidence_records (
                id, client_id, product_signal_id, interview_session_id,
                evidence_class, claim_state, source_ref, context_revision, body
            )
            values (gen_random_uuid(), %s, %s, %s, 'transcript_fragment', 'confirmed',
                    'transcript:test:0-400', 0, %s::jsonb)
            """,
            (
                case.client_id,
                case.product_signal_id,
                session_id,
                '{"kind": "interview_claim", "statement": "'
                + _CANONICAL_REQUEST.replace('"', "'")
                + '"}',
            ),
        )
    bundle = await _bundle(
        database, case=case, session_id=session_id, purpose="flow_classification"
    )
    async with JevClient(
        api_key=values["VERCEL_AI_GATEWAY_API_KEY"], model_id=model_id, timeout_seconds=30.0
    ) as jev:
        proposal = await propose_flow_classification(database, jev, bundle=bundle)

    choice = proposal.selections["classification::established_flow"]
    assert choice in {"bugfix_flow", "feature_request_flow", "unclear"}

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select purpose, accepted, rejections, answers
            from public.semantic_judgements where id = %s
            """,
            (proposal.semantic_judgement_id,),
        )
        judgement = await cur.fetchone()
    assert judgement is not None
    assert judgement["purpose"] == "flow_classification"
    assert judgement["accepted"] is proposal.accepted
    answer = judgement["answers"]["classification::established_flow"]
    assert 0.0 <= answer["confidence"] <= 1.0
    if not proposal.accepted:
        # Unresolved is a legitimate outcome, not a weak yes: the controller
        # keeps the proposal as a record and does not act on it.
        assert set(judgement["rejections"]) <= {
            "unresolved_distribution",
            "no_match_selected",
            "below_threshold",
        }
        return

    record = await record_flow_classification(
        database,
        bundle=bundle,
        flow_type=FlowType(choice),
        rationale="Jev proposed this flow from the participant's own account",
        supporting_evidence_ids=(),
        semantic_judgement_id=proposal.semantic_judgement_id,
    )
    assert record.classification_revision == 1
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select semantic_judgement_id from public.flow_classification_records "
            "where product_signal_id = %s",
            (case.product_signal_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["semantic_judgement_id"] == proposal.semantic_judgement_id
