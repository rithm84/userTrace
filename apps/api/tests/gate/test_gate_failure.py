"""A gate that stops the agreed change tells the live agent, whether it failed or declined.

A failing gate runs the real handler in the worker against a case whose pinned
policy cannot be read, which fails every run. A declining gate commits the gate's
own no-implementation outcome. In both, the voice agent's gate-outcome cue runs
unmodified and is already listening when the outcome commits.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Final
from uuid import UUID

from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    FlowType,
    GateDecisionKind,
    InterviewSessionState,
    ProductSignalState,
)

from app.context.bundles import AssembledBundle, assemble_bundle
from app.context.purposes import BundlePurpose
from app.db.pool import Database
from app.execution.handlers import build_exhausted_handlers
from app.execution.worker import Worker, WorkerConfig
from app.gate.controller import GateOutcome
from app.gate.job_types import DECIDE_GATE, GATE_FAILED_EVENT, GATE_NO_IMPLEMENTATION_EVENT
from app.gate.jobs import _close_without_implementation, run_gate
from app.gate.proposal import GateBlocker
from app.ids import uuid7
from app.interview.agreements import (
    CriterionDraft,
    record_acceptance_criteria_agreement,
    record_flow_classification,
)
from app.interview.voice.cues import announce_gate_outcome
from app.interview.voice.session_changes import SessionChanges
from app.outputs.job_types import ASSEMBLE_CASE_FILE
from tests.factories import CaseFixture, create_case, create_interview_session
from tests.gate.factories import approve_agreed_change
from tests.interview.recording_agent import RecordingAgent
from tests.supabase_auth import SignedInUser

_LISTENING_SECONDS: Final[float] = 1.0
"""Long enough for the cue's watcher to finish its first read before the outcome."""
_CUE_DEADLINE_SECONDS: Final[float] = 15.0


async def _gate_pending_case(database: Database, auth_users: dict[str, SignedInUser]) -> tuple[
    CaseFixture, UUID
]:
    """Agree a scope on the real path, which commits the gate Job beside `gate_pending`."""
    case = await create_case(
        database,
        name="gate-failure",
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )

    async def bundle(purpose: BundlePurpose) -> AssembledBundle:
        return await assemble_bundle(
            database,
            client_id=case.client_id,
            product_signal_id=case.product_signal_id,
            interview_session_id=session_id,
            purpose=purpose,
            budget_items=32,
            observations=(),
            extra_items=(),
            recent_fragment_limit=10,
            prior_command_limit=5,
        )

    await record_flow_classification(
        database,
        bundle=await bundle(BundlePurpose.FLOW_CLASSIFICATION),
        flow_type=FlowType.FEATURE_REQUEST_FLOW,
        rationale="the participant asks to save and reopen a filter stack",
        supporting_evidence_ids=(),
        semantic_judgement_id=None,
    )
    await record_acceptance_criteria_agreement(
        database,
        bundle=await bundle(BundlePurpose.VOICE_TOOLS),
        agreed_scope={"intended_outcome": "save and reopen a conversion view"},
        criteria=[
            CriterionDraft(
                ordinal=0,
                statement="the saved view reopens with every filter restored",
                observability=CriterionObservability.OBSERVED,
            )
        ],
        supporting_evidence_ids=(),
    )
    return case, session_id


async def _cue_heard_after(
    database: Database,
    case: CaseFixture,
    session_id: UUID,
    commit: Callable[[], Awaitable[None]],
) -> RecordingAgent:
    """Run the case's gate-outcome cue, commit the outcome once it listens, and return
    the agent once the outcome reached it, spoken or noted."""
    changes = SessionChanges(database, interview_session_id=session_id)
    listener = asyncio.create_task(changes.run())
    await changes.listening()
    agent = RecordingAgent()
    agent_quiet = asyncio.Event()
    agent_quiet.set()
    cue = asyncio.create_task(
        announce_gate_outcome(
            database,
            changes,
            product_signal_id=case.product_signal_id,
            agent=agent,
            agent_quiet=agent_quiet,
        )
    )
    try:
        await asyncio.sleep(_LISTENING_SECONDS)
        assert agent.said == [] and agent.noted == []
        await commit()
        async with asyncio.timeout(_CUE_DEADLINE_SECONDS):
            await agent.told.wait()
    finally:
        for task in (cue, listener):
            task.cancel()
        await asyncio.gather(cue, listener, return_exceptions=True)
    return agent


async def test_a_failed_gate_closes_with_a_blocker_and_the_agent_hears_it(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case, session_id = await _gate_pending_case(database, auth_users)
    worker = Worker(
        database,
        WorkerConfig(
            worker_id="gate-failure-worker",
            lease_seconds=60,
            heartbeat_seconds=30,
            poll_interval_seconds=0.1,
            wakeup_batch_size=10,
            outbox_scan_limit=100,
            stale_outbox_seconds=300,
            stale_dispatch_seconds=900,
            concurrency=1,
        ),
        {DECIDE_GATE: run_gate},
        build_exhausted_handlers(),
    )

    async def fail_the_gate() -> None:
        assert await worker.run_once() is True

    (cue,) = (await _cue_heard_after(database, case, session_id, fail_the_gate)).said
    assert "cannot be started" in cue

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state::text as state from public.jobs where job_type = %s", (DECIDE_GATE,)
        )
        gate_job = await cur.fetchone()
        await cur.execute(
            "select state::text as state from public.product_signals where id = %s",
            (case.product_signal_id,),
        )
        signal = await cur.fetchone()
        await cur.execute(
            "select payload from public.events where stream_id = %s and event_type = %s",
            (case.product_signal_id, GATE_FAILED_EVENT),
        )
        failed = await cur.fetchone()
        await cur.execute(
            "select input from public.jobs where job_type = %s", (ASSEMBLE_CASE_FILE,)
        )
        case_file_job = await cur.fetchone()
        await cur.execute(
            """
            select detail from public.audit_records
            where action = 'job_run_failed' and subject_kind = 'job'
            """
        )
        failure_audit = await cur.fetchone()
    assert gate_job is not None and gate_job["state"] == "failed"
    # The blocker Case File closes the case when it is published.
    assert signal is not None and signal["state"] == ProductSignalState.GATE_PENDING.value
    assert failed is not None
    assert failed["payload"]["outcome"] == "case_blocked"
    assert failed["payload"]["failure_code"] == "job_handler_raised"
    assert case_file_job is not None
    assert case_file_job["input"]["variant"] == "blocked"
    assert case_file_job["input"]["closure_trigger"] == "close_blocked"
    assert failure_audit is not None
    assert failure_audit["detail"]["details"]["error_class"] == "PolicyUnreadableError"


async def test_a_gate_that_chooses_no_implementation_tells_the_agent(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case, session_id = await _gate_pending_case(database, auth_users)

    async def decline() -> None:
        await _close_without_implementation(
            database,
            client_id=case.client_id,
            product_signal_id=case.product_signal_id,
            outcome=GateOutcome(
                gate_decision_id=uuid7(),
                decision=GateDecisionKind.NO_IMPLEMENTATION,
                approved_scope=None,
                blocker=GateBlocker.INSUFFICIENT_EVIDENCE,
                blocker_detail="the agreed checks cite no observed behavior",
                findings=(),
                evidence_window_id=uuid7(),
            ),
        )

    (cue,) = (await _cue_heard_after(database, case, session_id, decline)).said

    assert "will not be built" in cue
    assert "do not say a new version is coming" in cue
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select 1 from public.events where stream_id = %s and event_type = %s",
            (case.product_signal_id, GATE_NO_IMPLEMENTATION_EVENT),
        )
        assert await cur.fetchone() is not None


async def test_an_approved_gate_tells_the_agent_without_a_second_line(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Once the gate authorizes an attempt the agent is told it is building, and says nothing:
    the agreement's one line already said it will get the change started."""
    case, session_id = await _gate_pending_case(database, auth_users)

    async def approve() -> None:
        await approve_agreed_change(
            database,
            client_id=case.client_id,
            product_signal_id=case.product_signal_id,
            configuration_release_id=case.configuration_release_id,
        )

    agent = await _cue_heard_after(database, case, session_id, approve)

    assert agent.said == []
    (cue,) = agent.noted
    assert "being built now" in cue
