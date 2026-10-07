import asyncio
import base64
import contextlib

"""The voice runtime, end to end against a real socket and the real database.

The SDP exchange, the connection lifecycle, the sideband worker, and the
delegation round trip all run unmodified; only the provider on the far end is
the local test server from `gpt_live_server`. The connector itself is proven
against the real OpenAI API by `tests/connectors/test_openai_voice_live.py`.
"""

from collections.abc import Mapping
from uuid import UUID

import pytest
from utrace_contracts.domain.lifecycle import (
    ImplementationProgress,
    InterviewPhase,
    InterviewSessionState,
    ProductSignalState,
    VoiceConnectionState,
)

from app.auth.principals import ParticipantPrincipal
from app.db.pool import Database
from app.envelope import JsonValue
from app.errors import GuardRejectedError
from app.execution.jobs import ClaimedJob, claim_job
from app.ids import uuid7
from app.interview.handlers import run_voice_sideband_with
from app.interview.readiness import load_readiness
from app.interview.settings import get_interview_settings
from app.interview.voice.connections import (
    CLOSE_PROVIDER_SESSION,
    VOICE_SIDEBAND,
    open_voice_connection,
)
from app.interview.voice.delegation import DelegationRunner
from tests.factories import (
    CaseFixture,
    create_case,
    create_interview_session,
    create_ready_version,
)
from tests.helpers import record_session_readiness
from tests.interview.fixtures import create_preview_allocation
from tests.interview.gpt_live_server import ScriptedSession, run_test_server
from tests.interview.local_voice import local_voice_adapter
from tests.supabase_auth import SignedInUser

_OFFER_SDP = "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"

_SETUP_PORT = 8130
_TAKEOVER_PORT = 8131
_SIDEBAND_PORT = 8132
_DELEGATION_PORT = 8133


def _participant(case: CaseFixture) -> ParticipantPrincipal:
    """The originating user's resolved participation identity."""
    return ParticipantPrincipal(
        auth_user_id=uuid7(),
        utrace_user_id=case.utrace_user_id,
        client_user_id=case.client_user_id,
        client_id=case.client_id,
    )


async def _case(
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


def _fragment(kind: str, start: int, end: int, delta: str) -> Mapping[str, JsonValue]:
    """One scripted transcript fragment event."""
    return {"type": kind, "delta": delta, "start_ms": start, "end_ms": end}


async def test_sdp_exchange_binds_the_provider_session_to_the_voice_run(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "voice-setup")
    session_id = await create_interview_session(
        database,
        case=case,
        state=InterviewSessionState.PREPARING,
        conversational_ready=False,
    )
    script = ScriptedSession(events=())
    async with run_test_server(port=_SETUP_PORT, script=script) as server:
        adapter = local_voice_adapter(base_url=server.base_url)
        async with adapter:
            opened = await open_voice_connection(
                database,
                adapter,
                participant=_participant(case),
                interview_session_id=session_id,
                sdp_offer=_OFFER_SDP,
            )

    assert opened.sdp_answer.startswith("v=0")
    assert opened.provider_session_ref.startswith("live_")

    created = script.created_sessions[0]
    assert created["transport"]["type"] == "webrtc"
    assert created["session"]["model"] == "gpt-live-1"
    assert created["session"]["audio"] == {"output": {"voice": "marin"}}
    assert created["session"]["delegation"] == {"type": "client"}
    assert created["session"]["client"]["data_channel"]["allowed_client_events"] == []
    assert "Never expose internal implementation detail" in created["session"]["instructions"]

    rows = await _connections(database, session_id)
    assert len(rows) == 1
    assert rows[0]["state"] == VoiceConnectionState.ACTIVE.value
    assert rows[0]["voice_run_id"] == opened.voice_run_id
    assert rows[0]["provider_session_ref"] == opened.provider_session_ref

    sideband_jobs = await _jobs(database, VOICE_SIDEBAND)
    assert len(sideband_jobs) == 1
    assert sideband_jobs[0]["input"]["voice_run_id"] == str(opened.voice_run_id)

    readiness = await load_readiness(database, interview_session_id=session_id)
    assert readiness.components["voice_transport_ready"] is True
    assert readiness.components["sideband_attached"] is False
    assert readiness.conversational is False
    assert "sideband_not_attached" in readiness.blockers


async def test_a_takeover_supersedes_the_previous_connection_and_cleans_it_up(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "voice-takeover")
    session_id = await create_interview_session(
        database,
        case=case,
        state=InterviewSessionState.PREPARING,
        conversational_ready=False,
    )
    script = ScriptedSession(events=())
    async with run_test_server(port=_TAKEOVER_PORT, script=script) as server:
        adapter = local_voice_adapter(base_url=server.base_url)
        async with adapter:
            first = await open_voice_connection(
                database,
                adapter,
                participant=_participant(case),
                interview_session_id=session_id,
                sdp_offer=_OFFER_SDP,
            )
            second = await open_voice_connection(
                database,
                adapter,
                participant=_participant(case),
                interview_session_id=session_id,
                sdp_offer=_OFFER_SDP,
            )

    rows = await _connections(database, session_id)
    assert [row["state"] for row in rows] == [
        VoiceConnectionState.CLOSED.value,
        VoiceConnectionState.ACTIVE.value,
    ]
    assert rows[0]["superseded_by_id"] == second.connection_id
    assert first.voice_run_id != second.voice_run_id

    cleanup = await _jobs(database, CLOSE_PROVIDER_SESSION)
    assert len(cleanup) == 1
    assert cleanup[0]["input"]["provider_session_ref"] == first.provider_session_ref
    assert cleanup[0]["input"]["reason"] == "superseded_by_authenticated_takeover"


async def test_a_voice_connection_needs_a_preparing_or_live_session(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "voice-state-guard")
    session_id = await create_interview_session(
        database,
        case=case,
        state=InterviewSessionState.CLAIMED,
        conversational_ready=False,
    )
    script = ScriptedSession(events=())
    async with run_test_server(port=_SETUP_PORT, script=script) as server:
        adapter = local_voice_adapter(base_url=server.base_url)
        async with adapter:
            with pytest.raises(GuardRejectedError) as refusal:
                await open_voice_connection(
                    database,
                    adapter,
                    participant=_participant(case),
                    interview_session_id=session_id,
                    sdp_offer=_OFFER_SDP,
                )
    assert "preparing or live" in str(refusal.value)
    assert script.created_sessions == []


_GREETING_DEADLINE_SECONDS = 15.0


async def test_the_sideband_cues_the_greeting_on_a_call_nobody_has_spoken_on(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """GPT-Live waits for the caller, so the backend cues the agent to open the call."""
    case = await _case(database, auth_users, "voice-greeting")
    session_id = await create_interview_session(
        database,
        case=case,
        state=InterviewSessionState.LIVE,
        conversational_ready=True,
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.interview_sessions set phase = %s where id = %s",
            (InterviewPhase.GREETING.value, session_id),
        )
    await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )
    script = ScriptedSession(events=(), close_after_command_types=("session.commentary.append",))
    async with run_test_server(port=_SIDEBAND_PORT, script=script) as server:
        adapter = local_voice_adapter(base_url=server.base_url)
        async with adapter:
            await open_voice_connection(
                database,
                adapter,
                participant=_participant(case),
                interview_session_id=session_id,
                sdp_offer=_OFFER_SDP,
            )
            claimed = await _claim_sideband_job(database)
            # The server ends the session on the greeting cue; without one, the run
            # would wait for a close that never comes.
            async with asyncio.timeout(_GREETING_DEADLINE_SECONDS):
                await run_voice_sideband_with(
                    database,
                    claimed,
                    adapter=adapter,
                    delegations=DelegationRunner(
                        database=database,
                        reasoning=None,
                        jev=None,
                        targets=None,
                        settings=get_interview_settings(),
                    ),
                )

    greeting = script.received_commands[0]
    assert greeting["type"] == "session.commentary.append"
    assert greeting["delegation_id"] is None


async def test_the_sideband_announces_a_preview_ready_to_validate(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The agent hears that the candidate is ready without having to ask for it."""
    case = await _case(database, auth_users, "voice-ready")
    session_id = await create_interview_session(
        database,
        case=case,
        state=InterviewSessionState.LIVE,
        conversational_ready=True,
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.interview_sessions set implementation_progress = %s where id = %s",
            (ImplementationProgress.READY_FOR_VALIDATION.value, session_id),
        )
    await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )
    await create_ready_version(database, case=case)
    script = ScriptedSession(events=(), close_after_command_types=("session.commentary.append",))
    async with run_test_server(port=_SIDEBAND_PORT, script=script) as server:
        adapter = local_voice_adapter(base_url=server.base_url)
        async with adapter:
            await open_voice_connection(
                database,
                adapter,
                participant=_participant(case),
                interview_session_id=session_id,
                sdp_offer=_OFFER_SDP,
            )
            claimed = await _claim_sideband_job(database)
            # The server ends the session on the announcement; without one, the run
            # would wait for a close that never comes.
            async with asyncio.timeout(_GREETING_DEADLINE_SECONDS):
                await run_voice_sideband_with(
                    database,
                    claimed,
                    adapter=adapter,
                    delegations=DelegationRunner(
                        database=database,
                        reasoning=None,
                        jev=None,
                        targets=None,
                        settings=get_interview_settings(),
                    ),
                )

    announcement = script.received_commands[0]
    assert announcement["type"] == "session.commentary.append"
    assert announcement["delegation_id"] is None
    assert "ready" in str(announcement["content"])


async def test_the_sideband_persists_fragments_playback_and_a_delegation(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "voice-sideband")
    session_id = await create_interview_session(
        database,
        case=case,
        state=InterviewSessionState.LIVE,
        conversational_ready=True,
    )
    await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )
    # The participant already has the preview open, so no open-preview cue is sent and
    # the one instructions append the script closes on is the delegation's refusal.
    await record_session_readiness(
        database, interview_session_id=session_id, conversational=True, observation=True
    )
    script = ScriptedSession(
        events=(
            _fragment("session.output_transcript.delta", 0, 900, "Hi, I am Wren."),
            _fragment("session.input_transcript.delta", 950, 2100, "I rebuild filters."),
            {
                "type": "session.output_audio.delta",
                "start_ms": 0,
                "end_ms": 900,
                "delta": "AAAAAAAAAAA=",
            },
            {"type": "session.input_audio.append", "audio": "AAAAAAAAAAA="},
            {
                "type": "session.delegation.created",
                "event_id": "evt_delegation_001",
                "delegation": {"id": "del_0001", "type": "delegation", "target": "client"},
                "offset_ms": 2200,
            },
        ),
        close_after_command_types=("session.instructions.append",),
    )
    async with run_test_server(port=_SIDEBAND_PORT, script=script) as server:
        adapter = local_voice_adapter(base_url=server.base_url)
        async with adapter:
            opened = await open_voice_connection(
                database,
                adapter,
                participant=_participant(case),
                interview_session_id=session_id,
                sdp_offer=_OFFER_SDP,
            )
            claimed = await _claim_sideband_job(database)
            await run_voice_sideband_with(
                database,
                claimed,
                adapter=adapter,
                delegations=DelegationRunner(
                    database=database,
                    reasoning=None,
                    jev=None,
                    targets=None,
                    settings=get_interview_settings(),
                ),
            )

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select speaker, provider_event_type, start_ms, end_ms, fragment_text,
                   provenance, voice_run_id
            from public.voice_transcript_fragments
            where interview_session_id = %s
            order by start_ms
            """,
            (session_id,),
        )
        fragments = [dict(row) for row in await cur.fetchall()]
        await cur.execute(
            """
            select start_ms, end_ms, audio_byte_length, delivery
            from public.voice_playback_segments
            where interview_session_id = %s
            """,
            (session_id,),
        )
        playback = [dict(row) for row in await cur.fetchall()]
        await cur.execute(
            """
            select provider_delegation_id, state, refusal_reason, context_bundle_id
            from public.voice_delegations
            where interview_session_id = %s
            """,
            (session_id,),
        )
        delegations = [dict(row) for row in await cur.fetchall()]
        await cur.execute(
            "select revision, projection from public.session_projections where session_id = %s",
            (session_id,),
        )
        projection = await cur.fetchone()

    assert [row["speaker"] for row in fragments] == ["agent", "participant"]
    assert fragments[0]["provider_event_type"] == "session.output_transcript.delta"
    assert fragments[1]["fragment_text"] == "I rebuild filters."
    assert fragments[0]["voice_run_id"] == opened.voice_run_id
    assert fragments[0]["provenance"]["provider"] == "openai_gpt_live"

    assert len(playback) == 1
    assert playback[0]["delivery"] == "generated"
    assert playback[0]["audio_byte_length"] == 8

    assert len(delegations) == 1
    assert delegations[0]["provider_delegation_id"] == "del_0001"
    assert delegations[0]["state"] == "refused"
    # The participant already spoke on this session, so the agent is not cued to greet.
    assert not [
        command
        for command in script.received_commands
        if command["type"] == "session.commentary.append" and command["delegation_id"] is None
    ]
    assert delegations[0]["refusal_reason"] == "reasoning_path_unavailable"
    assert delegations[0]["context_bundle_id"] is not None

    assert projection is not None
    assert projection["revision"] >= 2
    assert projection["projection"]["character"]["state"] in {"listening", "reconnecting"}

    commands = [str(command.get("type")) for command in script.received_commands]
    assert "session.instructions.append" in commands
    assert "session.start" not in commands

    rows = await _connections(database, session_id)
    assert rows[0]["state"] == VoiceConnectionState.CLOSED.value


_CLAIM_ATTEMPTS = 50
_CLAIM_POLL_SECONDS = 0.1


def _agent_audio(start_ms: int, *, loud: bool) -> Mapping[str, JsonValue]:
    """100 ms of generated agent audio on the service timeline, loud or silent."""
    sample = (8000).to_bytes(2, "little", signed=True) if loud else bytes(2)
    return {
        "type": "session.output_audio.delta",
        "start_ms": start_ms,
        "end_ms": start_ms + 100,
        "delta": base64.b64encode(sample * 2400).decode(),
    }


async def test_the_character_speaks_until_the_agent_audio_has_played(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The model generates audio faster than it plays; the turn ends when it is heard."""
    case = await _case(database, auth_users, "voice-playback-turn")
    session_id = await create_interview_session(
        database,
        case=case,
        state=InterviewSessionState.LIVE,
        conversational_ready=True,
    )
    await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )
    await record_session_readiness(
        database, interview_session_id=session_id, conversational=True, observation=True
    )
    # Three seconds of speech and the pause after it, all delivered at once.
    script = ScriptedSession(
        events=(
            *(_agent_audio(start, loud=True) for start in range(0, 3000, 100)),
            *(_agent_audio(start, loud=False) for start in range(3000, 3800, 100)),
        ),
    )

    async def character() -> str:
        async with database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "select character_state::text as state from public.interview_sessions where id = %s",
                (session_id,),
            )
            row = await cur.fetchone()
        assert row is not None
        return str(row["state"])

    async with run_test_server(port=_SIDEBAND_PORT, script=script) as server:
        adapter = local_voice_adapter(base_url=server.base_url)
        async with adapter:
            await open_voice_connection(
                database,
                adapter,
                participant=_participant(case),
                interview_session_id=session_id,
                sdp_offer=_OFFER_SDP,
            )
            claimed = await _claim_sideband_job(database)
            sideband = asyncio.create_task(
                run_voice_sideband_with(
                    database,
                    claimed,
                    adapter=adapter,
                    delegations=DelegationRunner(
                        database=database,
                        reasoning=None,
                        jev=None,
                        targets=None,
                        settings=get_interview_settings(),
                    ),
                )
            )
            try:
                await script.attached.wait()
                await asyncio.sleep(1.5)
                while_playing = await character()
                await asyncio.sleep(2.5)
                after_playing = await character()
            finally:
                sideband.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sideband

    assert (while_playing, after_playing) == ("speaking", "listening")


async def _claim_sideband_job(database: Database) -> ClaimedJob:
    """Claim the sideband Job the SDP route queued, polling as a worker does.

    The route stamps the Job due with the API host's clock, which can run ahead of
    the database's by tens of milliseconds, so a claim the instant after the route
    returns may find it not yet due.
    """
    for _ in range(_CLAIM_ATTEMPTS):
        claimed = await claim_job(
            database,
            job_types=[VOICE_SIDEBAND],
            worker_id="interview-orchestration-test",
            lease_seconds=120,
        )
        if claimed is not None:
            return claimed
        await asyncio.sleep(_CLAIM_POLL_SECONDS)
    raise AssertionError("the SDP route queued no sideband Job")
