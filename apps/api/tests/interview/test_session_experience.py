"""Preflight, controls, character state, and the projection Realtime carries.

These are the facts the session tab and the extension render. Each one is read
back from `public.session_projections`, which is the row Supabase Realtime
distributes, so the test proves what a participant would actually receive.
"""

import pytest
from utrace_contracts.domain.lifecycle import (
    InterviewPhase,
    InterviewSessionState,
    ProductSignalState,
)

from app.auth.principals import ParticipantPrincipal
from app.db.pool import Database
from app.errors import GuardRejectedError
from app.ids import uuid7
from app.ingestion.control_hub import ServerControlHub
from app.interview.character import CharacterState
from app.interview.controls import SET_INPUT_MUTED, apply_participant_control, session_paused
from app.interview.preflight import (
    MINIMUM_CHROME_VERSION,
    ParticipantEnvironment,
    run_participant_preflight,
)
from app.interview.projection import load_projection, publish_projection
from app.interview.readiness import (
    load_readiness,
    record_conversational_facts,
)
from app.interview.settings import get_interview_settings
from app.lifecycle.session_controls import ControlAction
from app.offers.job_types import EVALUATE_ENTITLEMENT
from app.preview.routing import commit_route
from app.validation.controller import EXPIRE_VALIDATION_RETURN
from tests.extension.keys import DeviceKey
from tests.factories import CaseFixture, create_case, create_interview_session
from tests.helpers import count_rows, record_session_readiness
from tests.interview.fixtures import (
    RecordingObservationReadiness,
    complete_readiness,
    create_binding,
    create_live_session,
    create_preview_allocation,
    link_installation,
)
from tests.supabase_auth import SignedInUser

_SUPPORTED = ("1.0",)
_DEVICE_KEY = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEtestkeyfortheinstallationrecordonly"


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


def _participant(case: CaseFixture) -> ParticipantPrincipal:
    """The originating user's resolved participation identity."""
    return ParticipantPrincipal(
        auth_user_id=uuid7(),
        utrace_user_id=case.utrace_user_id,
        client_user_id=case.client_user_id,
        client_id=case.client_id,
    )


def _environment(**overrides: object) -> ParticipantEnvironment:
    """A passing participant environment, narrowed by the overrides."""
    base: dict[str, object] = {
        "browser_name": "Chrome",
        "browser_major_version": MINIMUM_CHROME_VERSION + 37,
        "extension_installed": True,
        "extension_installation_proof": None,
        "reported_protocol_version": "1.0",
        "host_permission_granted": True,
        "microphone_permission_granted": True,
    }
    base.update(overrides)
    return ParticipantEnvironment.model_validate(base)


async def test_preflight_admits_the_session_and_records_every_fact(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "preflight-pass")
    session_id = await create_interview_session(
        database,
        case=case,
        state=InterviewSessionState.CLAIMED,
        conversational_ready=False,
    )
    device_key = DeviceKey.generate()
    installation_id = await link_installation(
        database,
        utrace_user_id=case.utrace_user_id,
        device_public_key_spki_b64u=device_key.public_key_spki_b64u,
        protocol_version="1.0",
    )
    interview = get_interview_settings()
    outcome = await run_participant_preflight(
        database,
        participant=_participant(case),
        interview_session_id=session_id,
        environment=_environment(
            browser_name="Google Chrome",
            extension_installation_proof=device_key.forwarded_preflight_proof(
                audience=interview.extension_api_audience, installation_id=installation_id
            ),
        ),
        supported_protocol_versions=_SUPPORTED,
        possession_audience=interview.extension_api_audience,
        possession_max_age_seconds=interview.possession_proof_lifetime_seconds,
    )

    assert outcome.passed is True
    assert outcome.failed_checks == ()
    assert outcome.projection is not None
    # Setup passed, so the tab shows the call and opens voice; the session is not
    # live yet, so only the ways to leave it are offered.
    assert outcome.projection.state == "interviewing"
    assert "pause" not in outcome.projection.controls["available"]

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select browser_supported, extension_installed, account_linked,
                   host_permission_granted, microphone_permission_granted,
                   control_plane_reachable, protocol_supported, passed,
                   failed_checks, extension_installation_id
            from public.participant_preflight_records
            where interview_session_id = %s
            """,
            (session_id,),
        )
        record = await cur.fetchone()
        await cur.execute(
            "select state, phase from public.interview_sessions where id = %s",
            (session_id,),
        )
        session = await cur.fetchone()

    assert record is not None
    assert record["passed"] is True
    assert record["failed_checks"] == []
    assert record["control_plane_reachable"] is True
    assert record["extension_installation_id"] == installation_id
    assert session is not None
    assert session["state"] == InterviewSessionState.PREPARING.value
    assert session["phase"] == InterviewPhase.GREETING.value

    projection = await load_projection(database, interview_session_id=session_id)
    assert projection.projection["readiness"] == {
        "conversational": False,
        "observation": False,
    }
    assert projection.projection["character"]["state"] == CharacterState.IDLE.value


async def test_preflight_failure_records_guidance_without_admitting(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "preflight-fail")
    session_id = await create_interview_session(
        database,
        case=case,
        state=InterviewSessionState.CLAIMED,
        conversational_ready=False,
    )
    outcome = await run_participant_preflight(
        database,
        participant=_participant(case),
        interview_session_id=session_id,
        environment=_environment(
            browser_name="Safari",
            browser_major_version=18,
            host_permission_granted=False,
        ),
        supported_protocol_versions=_SUPPORTED,
        possession_audience=get_interview_settings().extension_api_audience,
        possession_max_age_seconds=get_interview_settings().possession_proof_lifetime_seconds,
    )

    assert outcome.passed is False
    assert set(outcome.failed_checks) == {
        "supported_browser",
        "extension_account_linked",
        "extension_host_permission",
    }
    detail = {check.check_id: check.detail for check in outcome.checks}
    assert str(MINIMUM_CHROME_VERSION) in detail["supported_browser"]

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state from public.interview_sessions where id = %s", (session_id,)
        )
        session = await cur.fetchone()
        await cur.execute(
            "select passed, failed_checks from public.participant_preflight_records "
            "where interview_session_id = %s",
            (session_id,),
        )
        record = await cur.fetchone()
    assert session is not None
    assert session["state"] == InterviewSessionState.CLAIMED.value
    assert record is not None
    assert record["passed"] is False
    assert sorted(record["failed_checks"]) == sorted(outcome.failed_checks)


async def test_pause_withdraws_observation_and_resume_rechecks_it(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "controls-pause")
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
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
    await create_binding(
        database,
        case=case,
        interview_session_id=session_id,
        extension_installation_id=installation_id,
        preview_runtime_allocation_id=allocation_id,
    )
    await record_session_readiness(
        database,
        interview_session_id=session_id,
        conversational=True,
        observation=True,
    )
    assert (await load_readiness(database, interview_session_id=session_id)).observation

    observation = RecordingObservationReadiness(readiness=complete_readiness())
    paused = await apply_participant_control(
        database,
        participant=_participant(case),
        interview_session_id=session_id,
        action=ControlAction.PAUSE,
        return_window_seconds=3600,
        observation=observation,
        control_hub=ServerControlHub(),
    )

    assert paused.paused is True
    assert paused.projection.state == "paused"
    assert paused.projection.projection["character"]["state"] == CharacterState.PAUSED.value
    assert paused.projection.projection["controls"]["available"] == [
        "resume",
        "finish_later",
        "end_confirm",
    ]
    readiness = await load_readiness(database, interview_session_id=session_id)
    assert readiness.observation is False
    assert "paused_by_participant" in readiness.blockers
    async with database.connection() as conn:
        assert await session_paused(conn, interview_session_id=session_id) is True

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select payload from public.commands where command_type = %s and target_id = %s",
            (SET_INPUT_MUTED, session_id),
        )
        mute_commands = [dict(row) for row in await cur.fetchall()]
        await cur.execute(
            """
            select event_type from public.events
            where stream_id = %s and event_type like 'session_paused%%'
            """,
            (session_id,),
        )
        paused_events = [dict(row) for row in await cur.fetchall()]
    assert [command["payload"]["muted"] for command in mute_commands] == [True]
    assert paused_events == [{"event_type": "session_paused_live_implementation_held"}]

    resumed = await apply_participant_control(
        database,
        participant=_participant(case),
        interview_session_id=session_id,
        action=ControlAction.RESUME,
        return_window_seconds=3600,
        observation=observation,
        control_hub=ServerControlHub(),
    )
    assert resumed.paused is False
    assert resumed.projection.revision > paused.projection.revision
    assert resumed.projection.state == "interviewing"
    assert "readiness" in observation.seen_queries
    restored = await load_readiness(database, interview_session_id=session_id)
    assert restored.observation is True


async def test_finish_later_starts_the_case_return_deadline_once(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "controls-finish-later")
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )
    result = await apply_participant_control(
        database,
        participant=_participant(case),
        interview_session_id=session_id,
        action=ControlAction.FINISH_LATER,
        return_window_seconds=3600,
        observation=None,
        control_hub=ServerControlHub(),
    )
    assert result.session_state == InterviewSessionState.CLOSED.value
    assert result.projection.state == "ended"
    assert result.projection.projection["end_reason"] == "finish_later"
    assert result.projection.projection["controls"]["available"] == []

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select return_deadline_at from public.product_signals where id = %s",
            (case.product_signal_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["return_deadline_at"] is not None
    # The deadline is enforced: the Job that closes the case at it is committed.
    assert (
        await count_rows(
            database,
            table="jobs",
            where="job_type = %s and input ->> 'product_signal_id' = %s",
            params=(EXPIRE_VALIDATION_RETURN, str(case.product_signal_id)),
        )
        == 1
    )


async def test_end_confirmation_cancels_the_signal_scope(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "controls-end")
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.jobs (
                id, client_id, job_type, state, input, deadline_at, timeout_seconds,
                max_runs, resource_budget, retryable_categories,
                backoff_base_seconds, backoff_max_seconds, cancellation_scope_id
            )
            values (%s, %s, 'test.pending_work', 'ready', '{}'::jsonb,
                    now() + interval '1 hour', 60, 3, '{}'::jsonb, '{}', 1, 2, %s)
            """,
            (uuid7(), case.client_id, case.cancellation_scope_id),
        )

    result = await apply_participant_control(
        database,
        participant=_participant(case),
        interview_session_id=session_id,
        action=ControlAction.END_CONFIRM,
        return_window_seconds=3600,
        observation=None,
        control_hub=ServerControlHub(),
    )
    assert result.projection.projection["end_reason"] == "ended_by_participant"

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state from public.jobs where cancellation_scope_id = %s",
            (case.cancellation_scope_id,),
        )
        jobs = [dict(row) for row in await cur.fetchall()]
        await cur.execute(
            """
            select detail from public.audit_records
            where action = 'participant_end_confirmation'
            """
        )
        audit = await cur.fetchone()
    assert [job["state"] for job in jobs] == ["canceled"]
    assert audit is not None
    assert audit["detail"]["canceled_jobs"] == 1
    # End is a cancellation: the case ends with no Case File, which is what lets
    # the teardown scan destroy its runtimes, and the offer settles unearned.
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state from public.product_signals where id = %s",
            (case.product_signal_id,),
        )
        signal = await cur.fetchone()
        await cur.execute(
            "select input from public.jobs where job_type = %s and client_id = %s",
            (EVALUATE_ENTITLEMENT, case.client_id),
        )
        settlement = await cur.fetchone()
    assert signal is not None and signal["state"] == "canceled"
    assert settlement is not None
    assert settlement["input"]["stop_reason"] == "participant_canceled"


async def test_the_projection_splits_discovery_by_the_observation_boundary(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "projection-phase")
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )
    allocation_id = await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="bound"
    )
    await record_conversational_facts(
        database,
        interview_session_id=session_id,
        participant_authenticated=True,
        voice_transport_ready=True,
        sideband_attached=True,
        backend_context_current=True,
    )
    before = await publish_projection(database, interview_session_id=session_id)
    assert before.projection["phase"] == "problem_discussion"
    assert before.projection["preview_available"] is False
    assert before.projection["readiness"]["unavailable_detail"].startswith(
        "We cannot see what is happening"
    )

    # The preview can be opened once its origin serves a committed route.
    await commit_route(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        preview_runtime_allocation_id=allocation_id,
        preview_version_id=None,
        public_preview_host=f"{allocation_id.hex[:12]}.preview.utrace.dev",
        upstream_port_url="https://sb-projection.vercel.run",
        application_port=3000,
        route_context_signing_secret="route-context-secret-projection-phase",
        ttl_seconds=600,
    )
    await record_session_readiness(
        database,
        interview_session_id=session_id,
        conversational=True,
        observation=True,
    )
    after = await publish_projection(database, interview_session_id=session_id)
    assert after.revision > before.revision
    assert after.projection["phase"] == "observed_workflow"
    assert after.projection["preview_available"] is True
    assert "unavailable_detail" not in after.projection["readiness"]


async def test_controls_apply_only_to_a_live_session(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "controls-guard")
    session_id = await create_interview_session(
        database,
        case=case,
        state=InterviewSessionState.PREPARING,
        conversational_ready=False,
    )
    with pytest.raises(GuardRejectedError) as refusal:
        await apply_participant_control(
            database,
            participant=_participant(case),
            interview_session_id=session_id,
            action=ControlAction.MUTE,
            return_window_seconds=3600,
            observation=None,
            control_hub=ServerControlHub(),
        )
    assert "live session" in str(refusal.value)
