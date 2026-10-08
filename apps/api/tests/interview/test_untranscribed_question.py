"""A delegation whose question GPT-Live never finished transcribing.

The scripted GPT-Live server plays a participant who spoke well past their last
transcribed word, then delegates; the transcript never catches up.
"""

import base64
from collections.abc import Mapping

from utrace_contracts.domain.lifecycle import InterviewSessionState

from app.db.pool import Database
from app.envelope import JsonValue
from app.interview.handlers import run_voice_sideband_with
from app.interview.settings import get_interview_settings
from app.interview.voice.connections import open_voice_connection
from app.interview.voice.delegation import DelegationRunner
from tests.factories import create_interview_session
from tests.helpers import record_session_readiness
from tests.interview.fixtures import create_preview_allocation
from tests.interview.gpt_live_server import ScriptedSession, run_test_server
from tests.interview.local_voice import local_voice_adapter
from tests.interview.test_voice_runtime import (
    _OFFER_SDP,
    _case,
    _claim_sideband_job,
    _fragment,
    _participant,
)
from tests.supabase_auth import SignedInUser

_PORT = 8139
#: 125 ms of loud 24 kHz PCM16: long enough to count as speech.
_SPEECH = base64.b64encode((8000).to_bytes(2, "little", signed=True) * 3000).decode()


def _delegation(delegation_id: str, offset_ms: int) -> Mapping[str, JsonValue]:
    return {
        "type": "session.delegation.created",
        "event_id": f"evt_{delegation_id}",
        "delegation": {"id": delegation_id, "type": "delegation", "target": "client"},
        "offset_ms": offset_ms,
    }


async def test_a_question_cut_off_in_the_transcript_is_answered_from_the_session_state(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "voice-untranscribed")
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )
    await record_session_readiness(
        database, interview_session_id=session_id, conversational=True, observation=True
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        # Past the greeting, so the only commentary is the delegation's answer.
        await cur.execute(
            "update public.interview_sessions set phase = 'discovery' where id = %s",
            (session_id,),
        )
    script = ScriptedSession(
        events=(
            # "Sorry, I'm not sure which one is the" is transcribed; the speech goes on
            # until just before the delegation at 6000 ms, and the rest never arrives.
            _fragment("session.input_transcript.delta", 1000, 2500, "Sorry, which one is the"),
            {"type": "session.input_audio.append", "audio": _SPEECH},
            _delegation("del_cut_off", 6000),
        ),
        # A later delegation, with nothing more said, judges from what is there.
        replies={"session.commentary.append": (_delegation("del_after", 7600),)},
        close_after_command_types=("session.instructions.append",),
    )
    async with run_test_server(port=_PORT, script=script) as server:
        adapter = local_voice_adapter(base_url=server.base_url)
        async with adapter:
            await open_voice_connection(
                database,
                adapter,
                participant=_participant(case),
                interview_session_id=session_id,
                sdp_offer=_OFFER_SDP,
            )
            await run_voice_sideband_with(
                database,
                await _claim_sideband_job(database),
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
            "select provider_delegation_id, state, finding, refusal_reason "
            "from public.voice_delegations where interview_session_id = %s "
            "order by created_at",
            (session_id,),
        )
        delegations = {row["provider_delegation_id"]: row for row in await cur.fetchall()}
    cut_off = delegations["del_cut_off"]
    assert cut_off["state"] == "answered"
    assert str(cut_off["finding"]).startswith("The end of what they just asked")
    commentary = [
        command["content"]
        for command in script.received_commands
        if command.get("type") == "session.commentary.append"
    ]
    assert commentary == [cut_off["finding"]]
    # The next delegation is not held on the same missing words: it goes to the
    # delegation model, absent here, and is refused for that reason alone.
    assert delegations["del_after"]["refusal_reason"] == "reasoning_path_unavailable"
