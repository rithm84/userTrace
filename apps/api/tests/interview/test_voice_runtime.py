"""The voice connection lifecycle against real Daily rooms and the real database.

Opening a connection creates a private Daily room and the participant's token for it,
binds the room to the connection's voice run, and queues the voice agent's Job; a
takeover supersedes the previous connection and queues its room's deletion, and
that call's end leaves the successor's readiness alone. Each test
deletes the rooms it created. What the agent does in a call is covered by the opt-in
live tests (`realtime_harness`).
"""

from collections.abc import Mapping
from uuid import UUID

import pytest
from utrace_contracts.domain.lifecycle import (
    InterviewSessionState,
    ProductSignalState,
    VoiceConnectionState,
)

from app.auth.principals import ParticipantPrincipal
from app.db.pool import Database
from app.errors import GuardRejectedError
from app.execution.jobs import claim_job
from app.ids import uuid7
from app.interview.handlers import close_provider_session
from app.interview.readiness import load_readiness
from app.interview.reconnect import EXPIRE_RECONNECT_WINDOW
from app.interview.settings import get_voice_settings
from app.interview.voice.adapter import DailyVoiceAdapter, build_voice_adapter
from app.interview.voice.agent import record_call_ended, subject_from_job
from app.interview.voice.connections import (
    CLOSE_PROVIDER_SESSION,
    VOICE_AGENT,
    open_voice_connection,
)
from tests.factories import CaseFixture, create_case, create_interview_session
from tests.supabase_auth import SignedInUser


def participant_of(case: CaseFixture) -> ParticipantPrincipal:
    """The originating user's resolved participation identity."""
    return ParticipantPrincipal(
        auth_user_id=uuid7(),
        utrace_user_id=case.utrace_user_id,
        client_user_id=case.client_user_id,
        client_id=case.client_id,
    )


async def routed_case(
    database: Database, auth_users: dict[str, SignedInUser], name: str
) -> CaseFixture:
    """One routed Product Signal with its originating participant."""
    return await create_case(
        database,
        name=name,
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )


async def _connections(database: Database, session_id: UUID) -> list[Mapping[str, object]]:
    """Every voice connection for one session, oldest first."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, state, voice_run_id, provider_session_ref, superseded_by_id
            from public.voice_connections
            where interview_session_id = %s
            order by created_at
            """,
            (session_id,),
        )
        return [dict(row) for row in await cur.fetchall()]


async def _jobs(database: Database, job_type: str) -> list[Mapping[str, object]]:
    """Every Job of one type, oldest first."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select id, job_type, state, input from public.jobs where job_type = %s "
            "order by created_at",
            (job_type,),
        )
        return [dict(row) for row in await cur.fetchall()]


async def _preparing_session(database: Database, case: CaseFixture) -> UUID:
    return await create_interview_session(
        database, case=case, state=InterviewSessionState.PREPARING, conversational_ready=False
    )


def _adapter() -> DailyVoiceAdapter:
    return build_voice_adapter(settings=get_voice_settings())


async def test_opening_a_connection_binds_a_private_room_to_the_voice_run(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await routed_case(database, auth_users, "voice-setup")
    session_id = await _preparing_session(database, case)
    async with _adapter() as adapter:
        opened = await open_voice_connection(
            database, adapter, participant=participant_of(case), interview_session_id=session_id
        )
        await adapter.rooms.delete_room(room_name=opened.provider_session_ref)

    assert opened.room_url.endswith(f"/{opened.provider_session_ref}")
    assert opened.participant_token

    rows = await _connections(database, session_id)
    assert len(rows) == 1
    assert rows[0]["state"] == VoiceConnectionState.ACTIVE.value
    assert rows[0]["voice_run_id"] == opened.voice_run_id
    assert rows[0]["provider_session_ref"] == opened.provider_session_ref

    agent_jobs = await _jobs(database, VOICE_AGENT)
    assert len(agent_jobs) == 1
    job_input = agent_jobs[0]["input"]
    assert isinstance(job_input, dict)
    assert job_input["voice_run_id"] == str(opened.voice_run_id)
    assert job_input["room_url"] == opened.room_url
    # The participant's token admits the browser only; the agent mints its own.
    assert opened.participant_token not in str(job_input)

    readiness = await load_readiness(database, interview_session_id=session_id)
    assert readiness.components["voice_transport_ready"] is True
    assert readiness.components["sideband_attached"] is False
    assert readiness.conversational is False


async def test_a_takeover_supersedes_the_previous_connection_and_deletes_its_room(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await routed_case(database, auth_users, "voice-takeover")
    session_id = await _preparing_session(database, case)
    async with _adapter() as adapter:
        first = await open_voice_connection(
            database, adapter, participant=participant_of(case), interview_session_id=session_id
        )
        second = await open_voice_connection(
            database, adapter, participant=participant_of(case), interview_session_id=session_id
        )
        await adapter.rooms.delete_room(room_name=second.provider_session_ref)

    rows = await _connections(database, session_id)
    assert [row["state"] for row in rows] == [
        VoiceConnectionState.CLOSED.value,
        VoiceConnectionState.ACTIVE.value,
    ]
    assert rows[0]["superseded_by_id"] == second.connection_id
    assert first.voice_run_id != second.voice_run_id

    cleanup = await _jobs(database, CLOSE_PROVIDER_SESSION)
    assert len(cleanup) == 1
    cleanup_input = cleanup[0]["input"]
    assert isinstance(cleanup_input, dict)
    assert cleanup_input["provider_session_ref"] == first.provider_session_ref
    assert cleanup_input["reason"] == "superseded_by_authenticated_takeover"

    claimed = await claim_job(
        database, job_types=[CLOSE_PROVIDER_SESSION], worker_id="voice-cleanup", lease_seconds=60
    )
    assert claimed is not None
    await close_provider_session(database, claimed)

    # Deleting its room ends the superseded call after its successor started; that
    # end leaves the successor's readiness alone and opens no reconnect window.
    first_agent = await claim_job(
        database, job_types=[VOICE_AGENT], worker_id="voice-agent", lease_seconds=60
    )
    assert first_agent is not None
    first_subject = await subject_from_job(database, first_agent)
    assert first_subject.voice_connection_id == first.connection_id
    await record_call_ended(database, subject=first_subject, reason="participant_left")
    readiness = await load_readiness(database, interview_session_id=session_id)
    assert readiness.components["voice_transport_ready"] is True
    assert await _jobs(database, EXPIRE_RECONNECT_WINDOW) == []


async def test_a_voice_connection_needs_a_preparing_or_live_session(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await routed_case(database, auth_users, "voice-state-guard")
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.CLAIMED, conversational_ready=False
    )
    async with _adapter() as adapter:
        with pytest.raises(GuardRejectedError) as refusal:
            await open_voice_connection(
                database,
                adapter,
                participant=participant_of(case),
                interview_session_id=session_id,
            )
    assert "preparing or live" in str(refusal.value)
    assert await _connections(database, session_id) == []
