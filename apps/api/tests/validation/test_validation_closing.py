"""The agent closes the conversation itself once validation is recorded, then the session ends."""

from __future__ import annotations

import asyncio
import base64
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Final

from utrace_contracts.domain.lifecycle import CriterionObservability, EvidenceClass, ProductSignalState

from app.auth.principals import ParticipantPrincipal
from app.db.pool import Database
from app.execution.jobs import claim_job
from app.ids import uuid7
from app.interview.handlers import run_voice_sideband_with
from app.interview.settings import get_interview_settings
from app.interview.voice.connections import VOICE_SIDEBAND, open_voice_connection
from app.interview.voice.delegation import DelegationRunner
from app.validation.completion import CriterionConfirmation, complete_validation
from app.validation.controller import begin_validation, open_validation_attempt
from app.validation.evidence import DurableEvidenceQuery, EvidenceStance
from tests.interview.gpt_live_server import ScriptedSession, run_test_server
from tests.interview.local_voice import local_voice_adapter
from tests.supabase_auth import SignedInUser
from tests.validation.scenario import add_criterion_observation, binding_of, build_scenario

_SIDEBAND_PORT: Final[int] = 8137
_OFFER_SDP: Final[str] = "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
_CUE_DEADLINE_SECONDS: Final[float] = 15.0
_CLOSING: Final[str] = "Validation is complete"


def _goodbye() -> list[dict[str, object]]:
    """A second of the agent's spoken goodbye, then the pause that ends its turn."""
    loud = base64.b64encode((8000).to_bytes(2, "little", signed=True) * 2400).decode()
    quiet = base64.b64encode(bytes(4800)).decode()
    return [
        {"type": "session.output_audio.delta", "start_ms": start, "end_ms": start + 100,
         "delta": loud if start < 1000 else quiet}
        for start in range(0, 1800, 100)
    ]


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


    # The sideband reads the case as it starts, so a connection made after validation
    # was recorded (or a reconnect) still closes the conversation.
    script = ScriptedSession(events=(), replies={"session.commentary.append": _goodbye()})
    async with run_test_server(port=_SIDEBAND_PORT, script=script) as server:
        adapter = local_voice_adapter(base_url=server.base_url)
        async with adapter:
            await open_voice_connection(
                database,
                adapter,
                participant=ParticipantPrincipal(
                    auth_user_id=uuid7(),
                    utrace_user_id=scenario.utrace_user_id,
                    client_user_id=scenario.client_user_id,
                    client_id=scenario.client_id,
                ),
                interview_session_id=scenario.interview_session_id,
                sdp_offer=_OFFER_SDP,
            )
            claimed = None
            while claimed is None:
                claimed = await claim_job(
                    database, job_types=[VOICE_SIDEBAND], worker_id="closing-cue", lease_seconds=120
                )
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
            await script.attached.wait()

            async def closing_cue() -> str:
                while True:
                    for command in script.received_commands:
                        content = str(command.get("content", ""))
                        if _CLOSING in content:
                            return content
                    await asyncio.sleep(0.1)

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
                    cue = await closing_cue()
                    closure_reason = await session_closed()
            finally:
                sideband.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sideband

    # The session ends once the goodbye has played, before the Case File exists.
    assert closure_reason == "completed"
    assert "will close now" in cue
    assert "Ask nothing more" in cue
