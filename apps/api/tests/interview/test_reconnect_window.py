"""The bounded reconnect window after the participant's call is lost.

A real connection is opened (a Daily room), and the call ends as the voice agent ends
it when the participant leaves (`record_call_ended`). The window's Job is then made
due and run as the worker would.
"""

from collections.abc import Mapping
from uuid import UUID

from utrace_contracts.domain.lifecycle import InterviewSessionState

from app.db.pool import Database
from app.execution.jobs import claim_job
from app.interview.reconnect import EXPIRE_RECONNECT_WINDOW, expire_reconnect_window
from app.interview.settings import get_voice_settings
from app.interview.voice.adapter import build_voice_adapter
from app.interview.voice.agent import record_call_ended, subject_from_job
from app.interview.voice.connections import VOICE_AGENT, open_voice_connection
from tests.factories import CaseFixture, create_interview_session
from tests.helpers import record_session_readiness
from tests.interview.fixtures import create_preview_allocation
from tests.interview.test_voice_runtime import participant_of, routed_case
from tests.supabase_auth import SignedInUser


async def _lose_the_connection(database: Database, case: CaseFixture, session_id: UUID) -> None:
    """Open a connection, then end its call as the agent does when the participant leaves."""
    async with build_voice_adapter(settings=get_voice_settings()) as adapter:
        opened = await open_voice_connection(
            database, adapter, participant=participant_of(case), interview_session_id=session_id
        )
        claimed = await claim_job(
            database, job_types=[VOICE_AGENT], worker_id="reconnect-test", lease_seconds=60
        )
        assert claimed is not None
        await record_call_ended(
            database, subject=await subject_from_job(database, claimed), reason="participant_left"
        )
        await adapter.rooms.delete_room(room_name=opened.provider_session_ref)


async def _live_session(
    database: Database, auth_users: dict[str, SignedInUser], name: str
) -> tuple[CaseFixture, UUID]:
    case = await routed_case(database, auth_users, name)
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )
    await record_session_readiness(
        database, interview_session_id=session_id, conversational=True, observation=True
    )
    return case, session_id


async def _run_the_window(database: Database) -> None:
    """Make the window's Job due and run it as the worker would."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "select extract(epoch from not_before - now()) as due_in from public.jobs "
            "where job_type = %s",
            (EXPIRE_RECONNECT_WINDOW,),
        )
        job = await cur.fetchone()
        assert job is not None
        # The window runs two minutes from the lost connection.
        assert 100 < float(job["due_in"]) <= 120
        await cur.execute(
            "update public.jobs set not_before = now() where job_type = %s",
            (EXPIRE_RECONNECT_WINDOW,),
        )
    claimed = await claim_job(
        database, job_types=[EXPIRE_RECONNECT_WINDOW], worker_id="reconnect-test", lease_seconds=60
    )
    assert claimed is not None
    await expire_reconnect_window(database, claimed)


async def _session(database: Database, session_id: UUID) -> Mapping[str, object]:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select sessions.state::text as state, sessions.closure_reason,
                   projections.projection ->> 'end_reason' as end_reason,
                   exists (
                       select 1 from public.events
                       where stream_id = sessions.product_signal_id
                         and event_type = 'case_return_deadline_recorded'
                   ) as return_deadline_recorded
            from public.interview_sessions as sessions
            join public.session_projections as projections
              on projections.session_id = sessions.id
            where sessions.id = %s
            """,
            (session_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    return row


async def test_a_lost_connection_not_replaced_in_time_checkpoints_the_encounter(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case, session_id = await _live_session(database, auth_users, "reconnect-expired")
    await _lose_the_connection(database, case, session_id)
    await _run_the_window(database)

    session = await _session(database, session_id)
    assert session["state"] == InterviewSessionState.CLOSED.value
    assert session["closure_reason"] == "reconnect_window_expired"
    assert session["end_reason"] == "connection_lost"
    assert session["return_deadline_recorded"] is True


async def test_a_replaced_connection_keeps_the_session_live(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case, session_id = await _live_session(database, auth_users, "reconnect-replaced")
    await _lose_the_connection(database, case, session_id)
    # The session tab reopens the call within the window.
    async with build_voice_adapter(settings=get_voice_settings()) as adapter:
        reopened = await open_voice_connection(
            database, adapter, participant=participant_of(case), interview_session_id=session_id
        )
        await adapter.rooms.delete_room(room_name=reopened.provider_session_ref)
    await _run_the_window(database)

    session = await _session(database, session_id)
    assert session["state"] == InterviewSessionState.LIVE.value
    assert session["closure_reason"] is None
    assert session["return_deadline_recorded"] is False
