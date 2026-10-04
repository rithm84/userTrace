"""The gate confirms the interview-established flow before it screens, against real Jev."""

import asyncio
from collections.abc import Mapping
from typing import Final
from uuid import UUID

from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    InterviewPhase,
    ProductSignalState,
)

from app.connectors.typesafe_jev import JevClient
from app.context.bundles import assemble_bundle
from app.context.purposes import BundlePurpose
from app.db.pool import Database
from app.gate.classification import establish_flow_classification
from app.interview.agreements import CriterionDraft, record_acceptance_criteria_agreement
from app.interview.readiness import ExtensionReadiness, record_observation_facts
from app.interview.settings import get_jev_settings
from tests.factories import CaseFixture, create_case
from tests.helpers import count_rows
from tests.interview.fixtures import create_live_session
from tests.supabase_auth import SignedInUser

_STORM_INTERVAL_SECONDS: Final[float] = 0.05
_FIRST_BUNDLE_POLL_SECONDS: Final[float] = 0.01
_FIRST_BUNDLE_DEADLINE_SECONDS: Final[float] = 10.0

_REQUEST = (
    "I keep rebuilding the same conversion filters every Monday. Can Shopalytics let me "
    "save a view for TikTok traffic from women 25-34 on products with negative sizing "
    "reviews, then reopen it later?"
)


async def test_the_gate_confirms_the_flow_once_from_the_interview(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await create_case(
        database,
        name="gate-classification",
        state=ProductSignalState.GATE_PENDING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.evidence_records (
                id, client_id, product_signal_id, interview_session_id,
                evidence_class, claim_state, source_ref, context_revision, body
            )
            values (gen_random_uuid(), %s, %s, %s, 'transcript_fragment', 'confirmed',
                    'transcript:test:0-400', 0,
                    jsonb_build_object('kind', 'interview_claim', 'statement', %s::text))
            """,
            (case.client_id, case.product_signal_id, session_id, _REQUEST),
        )

    settings = get_jev_settings()
    async with JevClient(
        api_key=settings.api_key, model_id=settings.model_id, timeout_seconds=30.0
    ) as jev:
        flow = await establish_flow_classification(
            database, jev, client_id=case.client_id, product_signal_id=case.product_signal_id
        )
        judgements = await count_rows(
            database,
            table="semantic_judgements",
            where="product_signal_id = %s",
            params=(case.product_signal_id,),
        )
        again = await establish_flow_classification(
            database, jev, client_id=case.client_id, product_signal_id=case.product_signal_id
        )

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select established_flow_type::text as flow from public.product_signals where id = %s",
            (case.product_signal_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    if flow is None:
        # Unresolved stays unresolved: nothing is recorded for the gate to use.
        assert row["flow"] is None
        return
    assert row["flow"] == flow.value
    # Once established, the classification is reused rather than judged again.
    assert again is flow
    assert (
        await count_rows(
            database,
            table="semantic_judgements",
            where="product_signal_id = %s",
            params=(case.product_signal_id,),
        )
        == judgements
    )


async def _gate_pending_case(
    database: Database, auth_users: dict[str, SignedInUser], name: str
) -> tuple[CaseFixture, UUID]:
    """A case waiting at the gate, with its live session and the participant's request."""
    case = await create_case(
        database,
        name=name,
        state=ProductSignalState.GATE_PENDING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.evidence_records (
                id, client_id, product_signal_id, interview_session_id,
                evidence_class, claim_state, source_ref, context_revision, body
            )
            values (gen_random_uuid(), %s, %s, %s, 'transcript_fragment', 'confirmed',
                    'transcript:test:0-400', 0,
                    jsonb_build_object('kind', 'interview_claim', 'statement', %s::text))
            """,
            (case.client_id, case.product_signal_id, session_id, _REQUEST),
        )
    return case, session_id


async def _classification_judgements(
    database: Database, *, case: CaseFixture
) -> list[Mapping[str, object]]:
    """The flow judgements for a case, oldest first, with the bundle each judged."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select judgements.context_bundle_id, judgements.acceptance ->> 'detail' as detail
            from public.semantic_judgements as judgements
            where judgements.product_signal_id = %s and judgements.purpose = %s
            order by judgements.created_at
            """,
            (case.product_signal_id, BundlePurpose.FLOW_CLASSIFICATION.value),
        )
        return [dict(row) for row in await cur.fetchall()]


async def test_readiness_moving_throughout_does_not_stale_the_classification(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The browser stream reconnecting while Jev judges, as in the failed live session."""
    case, session_id = await _gate_pending_case(database, auth_users, "gate-readiness-storm")
    stop = asyncio.Event()

    async def reconnect_storm() -> None:
        browser_sdk_ready = False
        while not stop.is_set():
            browser_sdk_ready = not browser_sdk_ready
            await record_observation_facts(
                database,
                interview_session_id=session_id,
                preview_allocation_ready=True,
                extension_readiness=ExtensionReadiness.READY,
                browser_sdk_ready=browser_sdk_ready,
                server_sdk_ready=True,
                trace_correlation_ready=True,
            )
            await asyncio.sleep(_STORM_INTERVAL_SECONDS)

    settings = get_jev_settings()
    storm = asyncio.create_task(reconnect_storm())
    try:
        async with JevClient(
            api_key=settings.api_key, model_id=settings.model_id, timeout_seconds=30.0
        ) as jev:
            flow = await establish_flow_classification(
                database, jev, client_id=case.client_id, product_signal_id=case.product_signal_id
            )
    finally:
        stop.set()
        await storm

    judgements = await _classification_judgements(database, case=case)
    # Judged once: nothing it depends on moved, however often readiness did.
    assert len(judgements) == 1
    assert "context_bundle_stale" not in str(judgements[0]["detail"])
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select (bundles.dependencies ->> 'readiness_revision')::bigint as pinned,
                   readiness.readiness_revision as current,
                   signals.established_flow_type::text as flow
            from public.context_bundles as bundles
            join public.session_readiness as readiness
              on readiness.interview_session_id = bundles.interview_session_id
            join public.product_signals as signals on signals.id = bundles.product_signal_id
            where bundles.id = %s
            """,
            (judgements[0]["context_bundle_id"],),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["current"] > row["pinned"]
    assert row["flow"] == (None if flow is None else flow.value)


async def test_a_scope_change_during_the_judgement_has_the_flow_judged_again(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The participant corrects the agreed scope while Jev judges: that judgement is
    refused, and the classification is judged again on the corrected scope."""
    case, session_id = await _gate_pending_case(database, auth_users, "gate-scope-change")

    async def correct_scope_during_first_judgement() -> None:
        async with asyncio.timeout(_FIRST_BUNDLE_DEADLINE_SECONDS):
            while not await count_rows(
                database,
                table="context_bundles",
                where="product_signal_id = %s and purpose = %s",
                params=(case.product_signal_id, BundlePurpose.FLOW_CLASSIFICATION.value),
            ):
                await asyncio.sleep(_FIRST_BUNDLE_POLL_SECONDS)
        await record_acceptance_criteria_agreement(
            database,
            bundle=await assemble_bundle(
                database,
                client_id=case.client_id,
                product_signal_id=case.product_signal_id,
                interview_session_id=session_id,
                purpose=BundlePurpose.VOICE_DELEGATION,
                budget_items=32,
                observations=(),
                extra_items=(),
                recent_fragment_limit=10,
                prior_command_limit=5,
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

    settings = get_jev_settings()
    correction = asyncio.create_task(correct_scope_during_first_judgement())
    async with JevClient(
        api_key=settings.api_key, model_id=settings.model_id, timeout_seconds=30.0
    ) as jev:
        flow = await establish_flow_classification(
            database, jev, client_id=case.client_id, product_signal_id=case.product_signal_id
        )
    await correction

    judgements = await _classification_judgements(database, case=case)
    assert len(judgements) == 2
    assert "context_bundle_stale" in str(judgements[0]["detail"])
    assert "acceptance_criteria_revision" in str(judgements[0]["detail"])
    assert "context_bundle_stale" not in str(judgements[1]["detail"])
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select context_bundle_id from public.flow_classification_records "
            "where product_signal_id = %s",
            (case.product_signal_id,),
        )
        records = list(await cur.fetchall())
    if flow is None:
        assert records == []
        return
    # The classification the gate screens against cites the bundle judged afresh.
    assert [row["context_bundle_id"] for row in records] == [judgements[1]["context_bundle_id"]]
