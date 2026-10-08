"""Context bundles, their budgets, their provenance, and their staleness rules.

Every assertion runs against the real schema: the bundle header, its ordered
items, the omitted-source records the budget produced, and the invalidation a
correction writes.
"""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    EvidenceClaimState,
    EvidenceClass,
    InterviewPhase,
    ProductSignalState,
)

from app.context.bundles import assemble_bundle, assemble_conversation_bundle, load_bundle
from app.context.freshness import check_bundle_freshness
from app.context.purposes import BundlePurpose
from app.context.sources import (
    SECTION_ACCEPTANCE_CRITERIA,
    SECTION_AGREED_SCOPE,
    SECTION_LIFECYCLE_AND_POLICY,
    SECTION_RECENT_CONVERSATION,
    SECTION_SELECTED_OBSERVATIONS,
)
from app.db.pool import Database
from app.ids import uuid7
from app.interview.agreements import (
    CriterionDraft,
    record_acceptance_criteria_agreement,
)
from app.interview.evidence import (
    TranscriptFragment,
    accept_correction,
    implementation_hold_active,
    record_claim,
    record_transcript_fragment,
)
from app.interview.observations import ObservedActivity
from tests.factories import CaseFixture, create_case
from tests.interview.fixtures import create_live_session
from tests.supabase_auth import SignedInUser


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


async def _connection(database: Database, *, case: CaseFixture, session_id: UUID) -> UUID:
    """One closed voice connection so fragments have a connection to cite."""
    connection_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.voice_connections (
                id, client_id, interview_session_id, state, voice_run_id,
                participant_connection_id, provider_session_ref
            )
            values (%s, %s, %s, 'closed', %s, %s, 'sess_test')
            """,
            (connection_id, case.client_id, session_id, uuid7(), connection_id),
        )
    return connection_id


def _observation(index: int) -> ObservedActivity:
    """One customer-safe activity summary, as the query surface projects it."""
    return ObservedActivity(
        observation_id=f"obs-{index}",
        observation_type="interaction.activation",
        occurred_at=datetime.now(tz=UTC).isoformat(),
        target_id=None,
        control=None,
        milestone=None,
        milestone_attributes={},
    )


async def test_a_bundle_is_source_linked_budgeted_and_stamped(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "context-budget")
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )
    connection_id = await _connection(database, case=case, session_id=session_id)
    async with database.transaction() as conn:
        for index in range(6):
            await record_transcript_fragment(
                conn,
                client_id=case.client_id,
                fragment=TranscriptFragment(
                    interview_session_id=session_id,
                    voice_connection_id=connection_id,
                    voice_run_id=uuid7(),
                    provider_session_ref="sess_test",
                    speaker="participant" if index % 2 else "agent",
                    provider_event_type="session.input_transcript.delta",
                    start_ms=index * 100,
                    end_ms=index * 100 + 90,
                    text=f"fragment {index}",
                ),
                provenance={"provider": "utrace_voice_agent"},
            )

    bundle = await assemble_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        purpose="voice_delegation",
        budget_items=5,
        observations=[_observation(index) for index in range(3)],
        extra_items=(),
        recent_fragment_limit=10,
        prior_command_limit=5,
    )

    assert bundle.context_revision == 1
    assert bundle.digest.startswith("sha256:")
    assert len(bundle.included) == 5
    assert len(bundle.omitted) > 0
    assert {dropped.reason for dropped in bundle.omitted} == {"context_budget_exceeded"}
    # Critical state survives the budget; conversation and observations are cut.
    assert bundle.section(SECTION_LIFECYCLE_AND_POLICY)
    assert bundle.section(SECTION_SELECTED_OBSERVATIONS) == ()
    omitted_sections = {dropped.section for dropped in bundle.omitted}
    assert SECTION_RECENT_CONVERSATION in omitted_sections
    assert SECTION_SELECTED_OBSERVATIONS in omitted_sections

    reloaded = await load_bundle(database, bundle_id=bundle.bundle_id)
    assert reloaded.digest == bundle.digest
    assert [item.source_id for item in reloaded.included] == [
        item.source_id for item in bundle.included
    ]
    assert len(reloaded.omitted) == len(bundle.omitted)

    freshness = await check_bundle_freshness(database, bundle_id=bundle.bundle_id)
    assert freshness.fresh is True


async def test_concurrent_bundles_for_one_purpose_take_successive_revisions(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    # The voice agent's concurrent tool calls each assemble their own bundle; none
    # may lose a race for the same revision.
    case = await _case(database, auth_users, "context-concurrent")
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )

    bundles = await asyncio.gather(
        *(
            assemble_conversation_bundle(
                database,
                client_id=case.client_id,
                product_signal_id=case.product_signal_id,
                interview_session_id=session_id,
                purpose=BundlePurpose.VOICE_TOOLS,
                recent_fragment_limit=10,
            )
            for _ in range(12)
        )
    )

    assert sorted(bundle.context_revision for bundle in bundles) == list(range(1, 13))


async def test_a_changed_criteria_revision_makes_a_bundle_stale(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "context-stale")
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )
    bundle = await assemble_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        purpose="voice_delegation",
        budget_items=32,
        observations=(),
        extra_items=(),
        recent_fragment_limit=10,
        prior_command_limit=5,
    )
    await record_acceptance_criteria_agreement(
        database,
        bundle=bundle,
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

    freshness = await check_bundle_freshness(database, bundle_id=bundle.bundle_id)
    assert freshness.fresh is False
    assert "acceptance_criteria_revision" in freshness.stale_dependencies
    assert "product_signal_revision" in freshness.stale_dependencies

    later = await assemble_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        purpose="voice_delegation",
        budget_items=32,
        observations=(),
        extra_items=(),
        recent_fragment_limit=10,
        prior_command_limit=5,
    )
    assert later.context_revision == 2
    assert later.section(SECTION_AGREED_SCOPE)
    assert len(later.section(SECTION_ACCEPTANCE_CRITERIA)) == 1
    assert (await check_bundle_freshness(database, bundle_id=later.bundle_id)).fresh


async def test_a_correction_invalidates_the_bundles_that_cited_the_claim(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "context-correction")
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )
    async with database.transaction() as conn:
        claim_id = await record_claim(
            conn,
            client_id=case.client_id,
            product_signal_id=case.product_signal_id,
            interview_session_id=session_id,
            evidence_class=EvidenceClass.TRANSCRIPT_FRAGMENT,
            claim_state=EvidenceClaimState.CONTRADICTED,
            source_ref="transcript:test:0-100",
            context_revision=0,
            body={"kind": "contradiction_flag", "reason": "unclear", "statement": "weekly"},
        supersedes_id=None,
        )
    bundle = await assemble_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        purpose="jev_attention",
        budget_items=32,
        observations=(),
        extra_items=(),
        recent_fragment_limit=10,
        prior_command_limit=5,
    )
    assert claim_id in bundle.cited_evidence_ids()

    outcome = await accept_correction(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        supersedes_id=claim_id,
        corrected_statement="I actually rebuild them every Monday, not every day",
        source_ref="transcript:test:200-400",
        context_revision=1,
    )
    assert bundle.bundle_id in outcome.invalidated_bundle_ids

    freshness = await check_bundle_freshness(database, bundle_id=bundle.bundle_id)
    assert freshness.fresh is False
    assert freshness.invalidation_reason == "superseded_by_accepted_correction"

    async with database.connection() as conn:
        assert await implementation_hold_active(
            conn, product_signal_id=case.product_signal_id
        )
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select supersedes_id, claim_state from public.evidence_records
                where product_signal_id = %s and supersedes_id is not null
                """,
                (case.product_signal_id,),
            )
            superseding = [dict(row) for row in await cur.fetchall()]
    assert len(superseding) == 1
    assert superseding[0]["supersedes_id"] == claim_id
    assert superseding[0]["claim_state"] == EvidenceClaimState.CONFIRMED.value


async def test_an_unready_observation_path_contributes_no_observations(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "context-observation-gate")
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )
    bundle = await assemble_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        purpose="voice_delegation",
        budget_items=32,
        observations=(),
        extra_items=(),
        recent_fragment_limit=10,
        prior_command_limit=5,
    )
    assert bundle.section(SECTION_SELECTED_OBSERVATIONS) == ()
