"""The open-preview cue: the agent hears of an unopened preview at once and asks outright
only after a quiet stretch.

It runs the production cue against a recording agent and a floor the test holds, over
the test database, so what the agent is told and when is exactly what is checked.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from utrace_contracts.domain.lifecycle import InterviewPhase, ProductSignalState

from app.db.pool import Database
from app.ids import uuid7
from app.interview.readiness import ExtensionReadiness, record_observation_facts
from app.interview.voice import cues
from app.interview.voice.channel import Floor
from app.interview.voice.session_changes import SessionChanges
from tests.factories import CaseFixture, create_case
from tests.interview.fixtures import create_live_session, create_preview_allocation
from tests.interview.recording_agent import RecordingAgent
from tests.supabase_auth import SignedInUser

_QUIET_SECONDS = 0.5
_DEADLINE_SECONDS = 10.0


async def _route_ready_preview(
    database: Database, *, case: CaseFixture, session_id: UUID, allocation_id: UUID
) -> None:
    """Commit the session's preview route, ready for the participant to open."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.preview_routes (
                id, client_id, product_signal_id, interview_session_id,
                preview_runtime_allocation_id, public_preview_host, route_revision, state,
                readiness_state, upstream_port_url, application_port,
                route_context_signing_secret, expires_at
            )
            values (%s, %s, %s, %s, %s, %s, 1, 'active', 'ready', %s, 3300, %s, %s)
            """,
            (
                uuid7(),
                case.client_id,
                case.product_signal_id,
                session_id,
                allocation_id,
                f"{session_id.hex[:12]}.preview.utrace.test",
                "https://runtime.example.test",
                f"secret-{session_id.hex}",
                datetime.now(tz=UTC) + timedelta(hours=1),
            ),
        )


async def test_an_unopened_preview_is_noted_and_asked_for_only_after_a_quiet_stretch(
    database: Database, auth_users: dict[str, SignedInUser], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saying "let me open it" holds the ask back; only nobody speaking for a while brings it.

    In a live interview the agent asked the participant to click Open preview three
    seconds after they said they were opening it, because a timer fired whatever
    had just been said.
    """
    monkeypatch.setattr(cues, "_OPEN_PREVIEW_QUIET_SECONDS", _QUIET_SECONDS)
    case = await create_case(
        database,
        name="open-preview-cue",
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    allocation_id = await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="bound"
    )
    await _route_ready_preview(
        database, case=case, session_id=session_id, allocation_id=allocation_id
    )

    changes = SessionChanges(database, interview_session_id=session_id)
    listener = asyncio.create_task(changes.run())
    await changes.listening()
    agent = RecordingAgent()
    floor = Floor(agent_quiet=asyncio.Event(), participant_quiet=asyncio.Event())
    floor.agent_quiet.set()
    # The participant is talking: "Let me open the preview real quick."
    cue = asyncio.create_task(
        cues.prompt_open_preview(
            database, changes, interview_session_id=session_id, agent=agent, floor=floor
        )
    )
    try:
        async with asyncio.timeout(_DEADLINE_SECONDS):
            await agent.told.wait()
        (note,) = agent.noted
        assert "have not opened it" in note

        # Holding the floor past the quiet stretch brings no ask.
        await asyncio.sleep(_QUIET_SECONDS * 3)
        assert agent.said == []

        # Nobody speaks for the quiet stretch: the agent asks, once.
        floor.participant_quiet.set()
        async with asyncio.timeout(_DEADLINE_SECONDS):
            await agent.spoke.wait()
        await asyncio.sleep(_QUIET_SECONDS * 3)
        (ask,) = agent.said
        assert "Open preview" in ask

        # The preview opens: the agent is told, and the cue is done.
        await record_observation_facts(
            database,
            interview_session_id=session_id,
            preview_allocation_ready=True,
            extension_readiness=ExtensionReadiness.READY,
            browser_sdk_ready=True,
            server_sdk_ready=True,
            trace_correlation_ready=True,
        )
        async with asyncio.timeout(_DEADLINE_SECONDS):
            await cue
    finally:
        for task in (cue, listener):
            task.cancel()
        await asyncio.gather(cue, listener, return_exceptions=True)
    assert "opened the preview now" in agent.noted[-1]
