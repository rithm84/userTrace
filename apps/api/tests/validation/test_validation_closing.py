"""The agent closes the conversation itself once validation is recorded, then the session ends."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Final

from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    EvidenceClass,
    ProductSignalState,
)

from app.db.pool import Database
from app.interview.voice.channel import Floor
from app.interview.voice.cues import announce_validation_outcome
from app.interview.voice.session_changes import SessionChanges
from app.validation.completion import CriterionConfirmation, complete_validation
from app.validation.controller import begin_validation, open_validation_attempt
from app.validation.evidence import DurableEvidenceQuery, EvidenceStance
from tests.interview.recording_agent import RecordingAgent
from tests.supabase_auth import SignedInUser
from tests.validation.scenario import add_criterion_observation, binding_of, build_scenario

_CUE_DEADLINE_SECONDS: Final[float] = 15.0
_CLOSING: Final[str] = "Validation is complete"


async def test_a_validated_case_has_the_agent_close_the_conversation(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    scenario = await build_scenario(
        database,
        name="closing-cue",
        contact_budget=2,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=auth_users["participant"].auth_user_id,
        criteria=(("the saved view reopens with every filter", CriterionObservability.OBSERVED),),
        signal_state=ProductSignalState.AWAITING_VALIDATION,
        candidate_ready=True,
    )
    (criterion,) = scenario.criteria
    await add_criterion_observation(
        database,
        scenario,
        criterion=criterion,
        stance=EvidenceStance.SUPPORTS,
        evidence_class=EvidenceClass.VALIDATION_OBSERVATION,
        summary="the saved view reopened with the agreed filters",
        supersedes_id=None,
    )

    attempt_id = await open_validation_attempt(database, binding=binding_of(scenario))
    await begin_validation(
        database, attempt_id=attempt_id, originating_user_id=scenario.client_user_id
    )
    window = await DurableEvidenceQuery(database).freeze_window(
        client_id=scenario.client_id,
        product_signal_id=scenario.product_signal_id,
        interview_session_id=scenario.interview_session_id,
        purpose="validation",
        starts_at=datetime.now(tz=UTC) - timedelta(hours=2),
        ends_at=datetime.now(tz=UTC) + timedelta(minutes=5),
    )
    await complete_validation(
        database,
        DurableEvidenceQuery(database),
        attempt_id=attempt_id,
        evidence_window_id=window.evidence_window_id,
        confirmations=[
            CriterionConfirmation(
                criterion_id=criterion.id,
                user_confirmed=True,
                user_rejected=False,
                confirmation_evidence_record_id=None,
                workflow_observed=True,
            )
        ],
        assembly_deadline_seconds=7 * 24 * 60 * 60,
    )

    # The cue reads the case as it starts, so a connection made after validation was
    # recorded (or a reconnect) still closes the conversation.
    changes = SessionChanges(database, interview_session_id=scenario.interview_session_id)
    listener = asyncio.create_task(changes.run())
    await changes.listening()
    agent = RecordingAgent()
    floor = Floor(
        agent_quiet=asyncio.Event(),
        no_tool_running=asyncio.Event(),
        participant_quiet=asyncio.Event(),
    )
    for event in (floor.agent_quiet, floor.no_tool_running, floor.participant_quiet):
        event.set()
    cue = asyncio.create_task(
        announce_validation_outcome(
            database,
            changes,
            client_id=scenario.client_id,
            product_signal_id=scenario.product_signal_id,
            agent=agent,
            floor=floor,
        )
    )

    async def session_closed() -> str:
        while True:
            async with database.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "select state::text as state, closure_reason from public.interview_sessions"
                    " where id = %s",
                    (scenario.interview_session_id,),
                )
                row = await cur.fetchone()
            if row is not None and row["state"] == "closed":
                return str(row["closure_reason"])
            await asyncio.sleep(0.1)

    try:
        async with asyncio.timeout(_CUE_DEADLINE_SECONDS):
            await agent.spoke.wait()
            # The agent says its goodbye; the session closes once it has played.
            floor.agent_quiet.clear()
            await asyncio.sleep(1.0)
            floor.agent_quiet.set()
            closure_reason = await session_closed()
    finally:
        for task in (cue, listener):
            task.cancel()
        await asyncio.gather(cue, listener, return_exceptions=True)
    (said,) = agent.said
    assert _CLOSING in said

    # The session ends once the goodbye has played, before the Case File exists.
    assert closure_reason == "completed"
    assert "will close now" in said
    assert "Ask nothing more" in said
