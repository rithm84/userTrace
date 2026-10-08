"""The tools the application executes for the voice agent's model."""

import json
from uuid import UUID

from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    InterviewPhase,
    ProductSignalState,
)

from app.context.bundles import AssembledBundle, assemble_bundle
from app.context.purposes import BundlePurpose
from app.db.pool import Database
from app.interview.voice.tools import ToolContext, execute_tool
from tests.factories import CaseFixture, create_case
from tests.interview.fixtures import (
    create_live_session,
)
from tests.supabase_auth import SignedInUser

_DEVICE_KEY = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEtoolstestkeyfortheinstallationrecord"
_OBSERVATION_WINDOW_SECONDS = 300


async def _case(database: Database, auth_users: dict[str, SignedInUser], name: str) -> CaseFixture:
    """One routed Product Signal in its interviewing state."""
    return await create_case(
        database,
        name=name,
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )


async def _bundle(database: Database, *, case: CaseFixture, session_id: UUID) -> AssembledBundle:
    """Assemble one current tool-call bundle."""
    return await assemble_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        purpose=BundlePurpose.VOICE_TOOLS,
        budget_items=32,
        observations=(),
        extra_items=(),
        recent_fragment_limit=10,
        prior_command_limit=5,
    )


async def test_recording_criteria_freezes_them_and_advances_the_concern_revision(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "tools-criteria")
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    bundle = await _bundle(database, case=case, session_id=session_id)
    context = ToolContext(
        database=database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        bundle=bundle,
        observation_window_seconds=_OBSERVATION_WINDOW_SECONDS,
    )
    recorded = json.loads(
        await execute_tool(
            context,
            name="record_acceptance_criteria",
            arguments={
                "agreed_outcome": "save a conversion view and reopen it later",
                "workflow": "saved_conversion_views",
                "criteria": [
                    {
                        "statement": "the filter stack saves under a name I choose",
                        "observability": CriterionObservability.OBSERVED.value,
                    },
                    {
                        "statement": "reopening restores every saved filter",
                        "observability": CriterionObservability.OBSERVED.value,
                    },
                ],
            },
        )
    )
    assert recorded == {"acceptance_criteria_revision": 1, "criterion_count": 2}

    status = json.loads(await execute_tool(context, name="implementation_status", arguments={}))
    assert status["progress"] == "nothing is being changed yet"
    assert status["work_in_flight"] is False

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select concern_revision, acceptance_criteria_revision "
            "from public.product_signals where id = %s",
            (case.product_signal_id,),
        )
        signal = await cur.fetchone()
        await cur.execute(
            "select statement from public.validation_criteria "
            "where product_signal_id = %s order by ordinal",
            (case.product_signal_id,),
        )
        criteria = [dict(row) for row in await cur.fetchall()]
    assert signal is not None
    assert signal["concern_revision"] == 1
    assert signal["acceptance_criteria_revision"] == 1
    assert [row["statement"] for row in criteria] == [
        "the filter stack saves under a name I choose",
        "reopening restores every saved filter",
    ]

    # The bundle that produced the agreement is now stale, so a repeat is refused
    # rather than silently taking a second revision.
    repeat = json.loads(
        await execute_tool(
            context,
            name="record_acceptance_criteria",
            arguments={
                "agreed_outcome": "something else",
                "workflow": "saved_conversion_views",
                "criteria": [
                    {
                        "statement": "a different criterion",
                        "observability": CriterionObservability.SUBJECTIVE.value,
                    }
                ],
            },
        )
    )
    assert repeat["refused"] == "stale_context"
