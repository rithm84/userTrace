"""Live: a voice call through the real session tab, API, and worker, heard in Chrome.

Each test runs the local stack (`browser_call`): the API, the worker's Job loop,
and the web app, with Chrome driven through the session tab as the participant.
The participant's lines are synthesized speech played through a synthetic
microphone, and what the participant hears is measured from the tab's own audio
element under Chrome's real autoplay policy. The session starts where a
participant's real one does once setup passes: `preparing`, with the agent not yet
in the call.

They cost a minute or two of voice each and need the local Supabase stack, so they
run only when `UTRACE_REALTIME_E2E=1`.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from openai import AsyncOpenAI
from utrace_contracts.domain.lifecycle import (
    InterviewPhase,
    JobState,
    InterviewSessionState,
    ProductSignalState,
)

from app.db.pool import Database
from app.db.rows import as_int
from app.ids import uuid7
from app.interview.character import CharacterState, set_character
from app.interview.ports import RegisteredTarget
from app.interview.projection import publish_projection_on_connection
from app.interview.readiness import record_conversational_facts
from app.interview.voice.connections import VOICE_AGENT
from app.settings import get_settings
from tests.conftest import require_env
from tests.factories import create_case, create_interview_session
from tests.interview import browser_call
from tests.interview.fixtures import (
    create_binding,
    create_preview_allocation,
    link_installation,
    observe_document,
)
from tests.supabase_auth import SignedInUser

_DEVICE_KEY = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEvoicebrowsertestkeyfortheinstallation"
_INTRO = "Hi Wren. Every Monday I rebuild the same conversion filters. Let me show you."
_ASK = "Can you hear me okay?"


def _live() -> None:
    if os.environ.get("UTRACE_REALTIME_E2E") != "1":
        pytest.skip("set UTRACE_REALTIME_E2E=1 to run the live browser voice checks")
    require_env("OPENAI_API_KEY")


async def _session_after_setup(
    database: Database, auth_users: dict[str, SignedInUser], participant: browser_call.Participant
) -> UUID:
    """An Interview Session as participant setup leaves it: preparing, agent not in the call."""
    case = await create_case(
        database,
        name=f"browser-voice-{participant.auth_user_id.hex[:8]}",
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=participant.auth_user_id,
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.PREPARING, conversational_ready=False
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        # Passing participant setup moves the session to its greeting, as preflight does.
        await cur.execute(
            "update public.interview_sessions set phase = %s where id = %s",
            (InterviewPhase.GREETING.value, session_id),
        )
    await record_conversational_facts(
        database,
        interview_session_id=session_id,
        participant_authenticated=True,
        voice_transport_ready=False,
        sideband_attached=False,
        backend_context_current=True,
    )
    allocation_id = await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )
    installation_id = await link_installation(
        database,
        utrace_user_id=case.utrace_user_id,
        device_public_key_spki_b64u=_DEVICE_KEY,
        protocol_version="1.0",
    )
    _, attachment_id = await create_binding(
        database,
        case=case,
        interview_session_id=session_id,
        extension_installation_id=installation_id,
        preview_runtime_allocation_id=allocation_id,
    )
    await observe_document(
        database,
        case=case,
        interview_session_id=session_id,
        preview_runtime_allocation_id=allocation_id,
        document_attachment_id=attachment_id,
        targets=tuple(
            RegisteredTarget(
                target_id=f"filter-{name}",
                target_revision=1,
                description=f'conversion_filter_{name} showing "All"',
                placement="top left of the screen",
                role="button",
                visible=True,
            )
            for name in ("date_range", "channel", "segment", "review_topic", "sentiment")
        ),
    )
    async with database.transaction() as conn:
        await set_character(conn, interview_session_id=session_id, state=CharacterState.IDLE)
        await publish_projection_on_connection(conn, interview_session_id=session_id)
    return session_id


@dataclass(frozen=True, slots=True)
class _CallRecord:
    """How many connections and agents the call used, and what the agent said in each run."""

    connections: int
    agents: int
    agent_runs: tuple[str, ...]


async def _call_record(database: Database, session_id: UUID) -> _CallRecord:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as n from public.voice_connections where interview_session_id = %s",
            (session_id,),
        )
        connections = await cur.fetchone()
        await cur.execute(
            "select count(*) as n from public.jobs where job_type = %s and input ->> 'interview_session_id' = %s",
            (VOICE_AGENT, str(session_id)),
        )
        agents = await cur.fetchone()
        await cur.execute(
            """
            select voice_run_id, string_agg(fragment_text, '' order by start_ms) as said
            from public.voice_transcript_fragments
            where interview_session_id = %s and speaker = 'agent'
            group by voice_run_id
            """,
            (session_id,),
        )
        runs = tuple(str(row["said"]) for row in await cur.fetchall())
    assert connections is not None and agents is not None
    return _CallRecord(
        connections=as_int(connections, "n"), agents=as_int(agents, "n"), agent_runs=runs
    )


def _greetings(said: tuple[str, ...]) -> int:
    """How many times the agent introduced itself by name."""
    return sum(len(re.findall(r"I['’]m Wren|I am Wren", text)) for text in said)


async def _clips(work: Path) -> dict[str, Path]:
    async with AsyncOpenAI(api_key=require_env("OPENAI_API_KEY")) as client:
        return {
            "intro": await browser_call.synthesize(client, _INTRO, work / "intro.pcm"),
            "ask": await browser_call.synthesize(client, _ASK, work / "ask.pcm"),
        }


def _participant() -> browser_call.Participant:
    settings = get_settings()
    return browser_call.participant_user(
        supabase_url=settings.supabase_url,
        secret_key=settings.supabase_secret_key,
        publishable_key=settings.supabase_publishable_key,
    )


async def test_the_participant_hears_one_greeting_and_a_reload_resumes_the_call(
    database: Database, auth_users: dict[str, SignedInUser], tmp_path: Path
) -> None:
    _live()
    participant = _participant()
    session_id = await _session_after_setup(database, auth_users, participant)
    clips = await _clips(tmp_path)
    async with browser_call.local_stack(tmp_path) as stack:
        report = browser_call.run_browser(
            stack=stack,
            session_id=session_id,
            participant=participant,
            clips=clips,
            steps=[
                {"do": "open"},
                {"do": "gesture"},
                {"do": "waitAgentSpeech", "label": "greeting", "timeoutMs": 30000},
                {"do": "waitAgentQuiet", "quietMs": 1500, "timeoutMs": 40000},
                {"do": "say", "clip": "intro"},
                {"do": "sleep", "ms": 6000},
                {"do": "mark", "label": "reload"},
                {"do": "reload"},
                {"do": "sleep", "ms": 4000},
                {"do": "gesture"},
                {"do": "say", "clip": "ask"},
                {"do": "waitAgentSpeech", "label": "answer after reload", "timeoutMs": 20000},
                {"do": "waitAgentQuiet", "quietMs": 1500, "timeoutMs": 30000},
                {"do": "sleep", "ms": 5000},
            ],
            work=tmp_path,
            timeout_seconds=300,
        )
        record = await _call_record(database, session_id)

    print(f"\nbrowser voice report ({tmp_path})")
    for event in report.events:
        print(f"  +{float(str(event['at'])):7.2f} {event['kind']}: {str(event['detail'])[:160]}")
    print(f"  record: {record}")
    print(f"  transcript shown: {report.transcript[:600]!r}")

    greeting = report.first("heard", after=0.0)
    assert greeting is not None, "the participant never heard the agent"
    assert report.text("heard")[:1] == ["greeting"]
    assert not report.kinds("session-voice-failure"), report.text("session-voice-failure")
    assert _greetings(record.agent_runs) == 1
    assert "answer after reload" in report.text("heard")
    # The reload rejoined the call the agent was still holding.
    assert record.connections == 1, record
    assert record.agents == 1, record


async def _hold_agent_slot(database: Database, session_id: UUID) -> UUID:
    """Lease a stand-in Job on the session's voice agent concurrency key."""
    holder = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.jobs (
                id, client_id, job_type, state, input, deadline_at, timeout_seconds,
                max_runs, resource_budget, retryable_categories,
                backoff_base_seconds, backoff_max_seconds, concurrency_key
            )
            select %s, client_id, 'test.voice_agent_holder', 'leased', '{}'::jsonb,
                   now() + interval '1 hour', 60, 1, '{}'::jsonb, '{}', 1, 2,
                   'voice_agent:' || id::text
            from public.interview_sessions where id = %s
            """,
            (holder, session_id),
        )
    return holder


def _release_agent_slot(holder: UUID) -> None:
    """Finish the stand-in Job, from a timer thread while the browser drives the call."""
    with psycopg.connect(get_settings().database_url) as conn:
        conn.execute(
            "update public.jobs set state = %s where id = %s", (JobState.SUCCEEDED.value, holder)
        )


async def test_a_late_agent_joins_the_call_without_a_loop(
    database: Database, auth_users: dict[str, SignedInUser], tmp_path: Path
) -> None:
    _live()
    participant = _participant()
    session_id = await _session_after_setup(database, auth_users, participant)
    clips = await _clips(tmp_path)
    # Another agent holds the interview's call for longer than the tab used to wait,
    # as the previous connection's agent does until it leaves, so this call's agent
    # joins only once that one lets go.
    holder = await _hold_agent_slot(database, session_id)
    threading.Timer(40.0, _release_agent_slot, args=(holder,)).start()
    async with browser_call.local_stack(tmp_path) as stack:
        report = browser_call.run_browser(
            stack=stack,
            session_id=session_id,
            participant=participant,
            clips=clips,
            steps=[
                {"do": "open"},
                {"do": "gesture"},
                {"do": "waitAgentSpeech", "label": "greeting", "timeoutMs": 90000},
                {"do": "waitAgentQuiet", "quietMs": 1500, "timeoutMs": 40000},
                {"do": "sleep", "ms": 15000},
            ],
            work=tmp_path,
            timeout_seconds=300,
        )
        record = await _call_record(database, session_id)

    print(f"\nlate agent report ({tmp_path})")
    for event in report.events:
        print(f"  +{float(str(event['at'])):7.2f} {event['kind']}: {str(event['detail'])[:160]}")
    print(f"  record: {record}")

    assert "greeting" in report.text("heard")
    assert record.agents == 1, record
    assert _greetings(record.agent_runs) == 1
