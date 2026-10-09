"""Same-user validation driven from the live session, over real telemetry.

The three operations the voice tools call run against real rows, and the evidence
they cite is a product milestone exported through the real ingestion socket from
a document activated on the candidate allocation. Only the model's proposals are
supplied directly, because they are arguments rather than state.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    CaseFileVariant,
    CriterionObservability,
    ImplementationAttemptState,
    ProductSignalState,
    ValidationAttemptState,
    ValidationOutcome,
)

from app.adapters.telemetry_evidence import TelemetryEvidenceQuery
from app.context.bundles import assemble_bundle
from app.db.pool import Database
from app.db.rows import as_str, as_uuid
from app.errors import GuardRejectedError
from app.ids import uuid7
from app.ingestion.preview_environments import resolve_preview_environment_id
from app.interview.agreements import CriterionDraft, record_change_request
from app.lifecycle.session_controls import ControlAction
from app.settings import Settings
from app.validation.controller import EXPIRE_VALIDATION_RETURN, record_departure
from app.validation.evidence import EvidenceStance
from app.validation.session import (
    CriterionAnswer,
    SpokenAnswer,
    cite_session_observation,
    finish_session_validation,
    start_session_validation,
)
from tests.evidence.helpers import OpenedDocument, open_document
from tests.factories import create_gate_decision
from tests.helpers import count_rows, record_session_readiness
from tests.ingestion.browser_client import observation
from tests.ingestion.factories import PreviewFixture, create_preview
from tests.ingestion.keys import SigningKeys
from tests.interview.fixtures import observe_ready_page
from tests.supabase_auth import SignedInUser

WORKFLOW_REF = "saved_conversion_views"
CRITERION = "reopening the saved view restores every filter"
IDENTITY = "maya@dudulemon.test"
SEED_DIGEST = "sha256:seed-session-validation"


#: How far off the participant's own clock is in these tests.
_CLOCK_ERROR = timedelta(minutes=10)


async def test_a_cited_candidate_milestone_and_a_confirmation_validate_the_case(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Begin, cite, and finish: the outcome rests on the cited observation.

    The participant's own clock is ten minutes off, ahead before validation and
    behind after it: when each milestone happened is read on the server's clock,
    so neither the early one nor the evidence is misjudged.
    """
    database = telemetry_database
    candidate = await _awaiting_validation(database, auth_users, name="session-validates")
    early = await _export_milestone(
        database,
        settings,
        keys,
        ingest_server,
        ingest_websocket_url,
        candidate,
        browser_clock=datetime.now(tz=UTC) + _CLOCK_ERROR,
    )
    # Validation waits for the page the participant has open to be observed.
    await observe_ready_page(
        database,
        case=candidate.case,
        interview_session_id=candidate.interview_session_id,
        preview_runtime_allocation_id=candidate.preview_runtime_allocation_id,
    )

    started = await start_session_validation(
        database, interview_session_id=candidate.interview_session_id
    )
    assert [criterion.statement for criterion in started.criteria] == [CRITERION]
    assert (
        await _state(database, table="product_signals", row_id=candidate.case.product_signal_id)
        == ProductSignalState.VALIDATING.value
    )
    # The session tab now shows validation rather than the discovery it came from.
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select phase from public.interview_sessions where id = %s",
            (candidate.interview_session_id,),
        )
        session = await cur.fetchone()
    assert session is not None
    assert session["phase"] == "validation"
    # A repeated call from the agent is the same attempt, not a second one.
    again = await start_session_validation(
        database, interview_session_id=candidate.interview_session_id
    )
    assert again.validation_attempt_id == started.validation_attempt_id

    evidence = TelemetryEvidenceQuery(database)
    with pytest.raises(GuardRejectedError, match="since the Validation Attempt opened"):
        await cite_session_observation(
            database,
            evidence,
            interview_session_id=candidate.interview_session_id,
            ordinal=1,
            observation_id=early,
            stance=EvidenceStance.SUPPORTS,
            summary="an observation from before validation began",
        )

    observed = await _export_milestone(
        database,
        settings,
        keys,
        ingest_server,
        ingest_websocket_url,
        candidate,
        browser_clock=datetime.now(tz=UTC) - _CLOCK_ERROR,
    )
    await cite_session_observation(
        database,
        evidence,
        interview_session_id=candidate.interview_session_id,
        ordinal=1,
        observation_id=observed,
        stance=EvidenceStance.SUPPORTS,
        summary="the participant reopened the saved view and every filter returned",
    )
    completion = await finish_session_validation(
        database,
        evidence,
        interview_session_id=candidate.interview_session_id,
        answers=[
            SpokenAnswer(
                ordinal=1,
                answer=CriterionAnswer.CONFIRMED,
                statement="yes, every filter came back when I reopened it",
            )
        ],
        source_ref="live_session:test",
        context_revision=0,
    )
    assert completion.outcome is ValidationOutcome.VALIDATED
    assert completion.case_file_variant is CaseFileVariant.VALIDATED_IMPLEMENTATION
    assert (
        await _state(database, table="validation_attempts", row_id=started.validation_attempt_id)
        == ValidationAttemptState.COMPLETED.value
    )


async def test_an_unproven_confirmation_does_not_validate(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """The user's yes alone is not affirmative proof of observed behaviour."""
    database = telemetry_database
    candidate = await _awaiting_validation(database, auth_users, name="session-unproven")
    await start_session_validation(database, interview_session_id=candidate.interview_session_id)
    await _export_milestone(
        database,
        settings,
        keys,
        ingest_server,
        ingest_websocket_url,
        candidate,
        browser_clock=datetime.now(tz=UTC),
    )

    completion = await finish_session_validation(
        database,
        TelemetryEvidenceQuery(database),
        interview_session_id=candidate.interview_session_id,
        answers=[
            SpokenAnswer(ordinal=1, answer=CriterionAnswer.CONFIRMED, statement="looks right")
        ],
        source_ref="live_session:test",
        context_revision=0,
    )
    assert completion.outcome is not ValidationOutcome.VALIDATED
    assert completion.case_file_variant is CaseFileVariant.BLOCKED


async def test_an_action_held_back_from_before_validation_is_not_evidence(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A page that delivers an earlier action late cannot pass it off as evidence.

    The page's first delivery places its clock on the server's. The held-back
    milestone happened a few milliseconds after that, before the attempt opened,
    though it arrives afterwards and the browser's own clock dates it later still.
    """
    database = telemetry_database
    candidate = await _awaiting_validation(database, auth_users, name="session-held-back")
    opened = await _open_candidate_page(
        database, settings, keys, ingest_server, ingest_websocket_url, candidate
    )
    first = _view_saved(opened, sequence=1, browser_clock=datetime.now(tz=UTC))
    held_back = _view_saved(
        opened, sequence=2, browser_clock=datetime.now(tz=UTC) + _CLOCK_ERROR
    )
    async with opened.exporter as exporter:
        await exporter.open_stream(resume_from=0)
        await exporter.send_batch([first], batch_id=None)
        # Validation waits for the page the participant has open to be observed.
        await observe_ready_page(
            database,
            case=candidate.case,
            interview_session_id=candidate.interview_session_id,
            preview_runtime_allocation_id=candidate.preview_runtime_allocation_id,
        )
        await start_session_validation(
            database, interview_session_id=candidate.interview_session_id
        )
        await exporter.send_batch([held_back], batch_id=None)

    with pytest.raises(GuardRejectedError, match="since the Validation Attempt opened"):
        await cite_session_observation(
            database,
            TelemetryEvidenceQuery(database),
            interview_session_id=candidate.interview_session_id,
            ordinal=1,
            observation_id=await _committed_id(database, held_back),
            stance=EvidenceStance.SUPPORTS,
            summary="a saved view from before validation began",
        )


async def test_departure_interrupts_validation_and_schedules_expiry_once(
    telemetry_database: Database,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Leaving mid-validation interrupts it; the deadline and its Job are recorded once."""
    database = telemetry_database
    candidate = await _awaiting_validation(database, auth_users, name="session-departs")
    started = await start_session_validation(
        database, interview_session_id=candidate.interview_session_id
    )
    for _ in range(2):
        async with database.transaction() as conn:
            await record_departure(
                conn,
                product_signal_id=candidate.case.product_signal_id,
                reason=ControlAction.FINISH_LATER.value,
                return_window_seconds=3600,
            )
    assert (
        await _state(database, table="validation_attempts", row_id=started.validation_attempt_id)
        == ValidationAttemptState.INTERRUPTED.value
    )
    assert (
        await count_rows(
            database,
            table="jobs",
            where="job_type = %s and input ->> 'product_signal_id' = %s",
            params=(EXPIRE_VALIDATION_RETURN, str(candidate.case.product_signal_id)),
        )
        == 1
    )
    resumed = await start_session_validation(
        database, interview_session_id=candidate.interview_session_id
    )
    assert resumed.validation_attempt_id == started.validation_attempt_id
    assert (
        await _state(database, table="validation_attempts", row_id=started.validation_attempt_id)
        == ValidationAttemptState.IN_PROGRESS.value
    )


async def test_a_change_asked_for_in_the_new_version_goes_back_through_the_gate(
    telemetry_database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The change and its checks freeze, the version's validation stops, and the gate runs."""
    database = telemetry_database
    candidate = await _awaiting_validation(database, auth_users, name="session-change")
    started = await start_session_validation(
        database, interview_session_id=candidate.interview_session_id
    )
    bundle = await assemble_bundle(
        database,
        client_id=candidate.case.client_id,
        product_signal_id=candidate.case.product_signal_id,
        interview_session_id=candidate.interview_session_id,
        purpose="voice_delegation",
        budget_items=32,
        observations=(),
        extra_items=(),
        recent_fragment_limit=10,
        prior_command_limit=5,
    )
    criteria = (
        CriterionDraft(ordinal=0, statement=CRITERION, observability=CriterionObservability.OBSERVED),
        CriterionDraft(
            ordinal=1,
            statement="the save control sits in the sidebar",
            observability=CriterionObservability.OBSERVED,
        ),
    )
    scope = {"intended_outcome": "saved views, saved from the sidebar", "workflow_ref": WORKFLOW_REF}

    # The case's one implementation is used, so a limit of one leaves no change.
    with pytest.raises(GuardRejectedError, match="already used all 1"):
        await record_change_request(
            database,
            bundle=bundle,
            agreed_scope=scope,
            criteria=criteria,
            supporting_evidence_ids=(),
            implementation_limit=1,
        )

    request = await record_change_request(
        database,
        bundle=bundle,
        agreed_scope=scope,
        criteria=criteria,
        supporting_evidence_ids=(),
        implementation_limit=3,
    )

    assert request.implementation_number == 2
    assert (
        await _state(database, table="product_signals", row_id=candidate.case.product_signal_id)
        == ProductSignalState.GATE_PENDING.value
    )
    assert (
        await _state(
            database, table="validation_attempts", row_id=started.validation_attempt_id
        )
        == "abandoned"
    )
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select phase, implementation_progress from public.interview_sessions where id = %s",
            (candidate.interview_session_id,),
        )
        session = await cur.fetchone()
        await cur.execute(
            "select count(*) as total from public.jobs "
            "where job_type = 'gate.decide' and input ->> 'product_signal_id' = %s",
            (str(candidate.case.product_signal_id),),
        )
        jobs = await cur.fetchone()
    assert session is not None
    assert session["phase"] == "implementation"
    assert session["implementation_progress"] == "reproduction_captured"
    assert jobs is not None
    assert jobs["total"] == 1


async def _awaiting_validation(
    database: Database, auth_users: dict[str, SignedInUser], *, name: str
) -> PreviewFixture:
    """Take a live session's case to `awaiting_validation` behind a ready candidate.

    The rows are the ones the gate, the implementation Job, and the candidate
    deployment commit on the real path; the returned fixture points at the
    candidate allocation, so a document activated on it is observed there.
    """
    preview = await create_preview(
        database,
        name=f"{name}-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    case = preview.case
    gate_id = await create_gate_decision(
        database,
        case=case,
        decision="approved_live",
        classification_revision=1,
        blocker_reason=None,
    )
    attempt_id, version_id, allocation_id, bundle_id, criterion_id = (
        uuid7(),
        uuid7(),
        uuid7(),
        uuid7(),
        uuid7(),
    )
    origin_host = f"{uuid7().hex[:20]}.preview.utrace.dev"
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.implementation_attempts (
                id, client_id, product_signal_id, gate_decision_id, state,
                repository_ref, base_revision, consumed_budgets
            )
            values (%s, %s, %s, %s, %s, 'ShopalyticsStore/shopalytics', 'base', %s)
            """,
            (
                attempt_id,
                case.client_id,
                case.product_signal_id,
                gate_id,
                ImplementationAttemptState.CANDIDATE_READY.value,
                Jsonb({}),
            ),
        )
        await cur.execute(
            """
            insert into public.preview_versions (
                id, client_id, product_signal_id, implementation_attempt_id, state,
                source_commit, source_digest, build_digest, base_revision,
                seed_manifest_digest, ready_at
            )
            values (%s, %s, %s, %s, 'ready', 'candidate', 'sha256:source',
                    'sha256:build', 'base', %s, now())
            """,
            (version_id, case.client_id, case.product_signal_id, attempt_id, SEED_DIGEST),
        )
        await cur.execute(
            """
            insert into public.preview_runtime_allocations (
                id, client_id, product_signal_id, allocation_kind, pool_role, state,
                interview_session_id, preview_version_id, seed_manifest_digest,
                authenticated_identity, origin_host, fencing_token,
                preview_runtime_template_id, pinned_configuration_release_id
            )
            values (%s, %s, %s, 'candidate', 'candidate', 'bound', %s, %s, %s, %s, %s,
                    utrace.next_fencing_token(), %s, %s)
            """,
            (
                allocation_id,
                case.client_id,
                case.product_signal_id,
                preview.interview_session_id,
                version_id,
                SEED_DIGEST,
                IDENTITY,
                origin_host,
                preview.preview_runtime_template_id,
                preview.configuration_release_id,
            ),
        )
        await cur.execute(
            "update public.interview_sessions set preview_runtime_allocation_id = %s where id = %s",
            (allocation_id, preview.interview_session_id),
        )
        environment_id = await resolve_preview_environment_id(
            conn, preview_runtime_allocation_id=allocation_id
        )
        await cur.execute(
            """
            insert into public.context_bundles (
                id, client_id, product_signal_id, interview_session_id, purpose,
                context_revision, cutoff_at, budget_items, included_items,
                omitted_items, dependencies, digest
            )
            values (%s, %s, %s, %s, 'voice_delegation', 1, now(), 1, 0, 0, %s, 'digest')
            """,
            (
                bundle_id,
                case.client_id,
                case.product_signal_id,
                preview.interview_session_id,
                Jsonb({}),
            ),
        )
        await cur.execute(
            """
            insert into public.validation_criteria (
                id, client_id, product_signal_id, criteria_revision, ordinal,
                statement, observability
            )
            values (%s, %s, %s, 1, 1, %s, 'observed')
            """,
            (criterion_id, case.client_id, case.product_signal_id, CRITERION),
        )
        await cur.execute(
            """
            insert into public.acceptance_criteria_agreements (
                id, client_id, product_signal_id, interview_session_id,
                acceptance_criteria_revision, agreed_scope, criterion_ids,
                supporting_evidence_ids, context_bundle_id
            )
            values (%s, %s, %s, %s, 1, %s, %s, '{}', %s)
            """,
            (
                uuid7(),
                case.client_id,
                case.product_signal_id,
                preview.interview_session_id,
                Jsonb({"intended_outcome": "saved conversion views", "workflow_ref": WORKFLOW_REF}),
                [criterion_id],
                bundle_id,
            ),
        )
        await cur.execute(
            """
            update public.product_signals
            set state = %s, acceptance_criteria_revision = 1, revision = revision + 1
            where id = %s
            """,
            (ProductSignalState.AWAITING_VALIDATION.value, case.product_signal_id),
        )
    await record_session_readiness(
        database,
        interview_session_id=preview.interview_session_id,
        conversational=True,
        observation=True,
    )
    # The participant has the candidate's page open, and its path is observed.
    await observe_ready_page(
        database,
        case=case,
        interview_session_id=preview.interview_session_id,
        preview_runtime_allocation_id=allocation_id,
    )
    return dataclasses.replace(
        preview,
        preview_runtime_allocation_id=allocation_id,
        preview_environment_id=environment_id,
        origin_host=origin_host,
    )


async def _open_candidate_page(
    database: Database,
    settings: Settings,
    keys: SigningKeys,
    base_url: str,
    websocket_url: str,
    candidate: PreviewFixture,
) -> OpenedDocument:
    """Activate one page on the candidate through real ingestion."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select preview_version_id from public.preview_runtime_allocations where id = %s",
            (candidate.preview_runtime_allocation_id,),
        )
        allocation = await cur.fetchone()
    assert allocation is not None
    return await open_document(
        settings,
        keys,
        base_url,
        websocket_url,
        preview=candidate,
        preview_version_id=as_uuid(allocation, "preview_version_id"),
    )


def _view_saved(
    opened: OpenedDocument, *, sequence: int, browser_clock: datetime
) -> dict[str, object]:
    """One `view_saved` milestone, stamped with whatever time the browser's own clock gives."""
    return observation(
        observation_type="product.milestone",
        runtime_id=opened.runtime_id,
        document_attachment_id=opened.document_attachment_id,
        source_sequence=sequence,
        export_sequence=sequence,
        payload={"milestone_id": "view_saved", "attributes": [], "actor": "user"},
        trace_id=None,
        span_id=None,
        target_id=None,
        occurred_at=browser_clock,
    )


async def _committed_id(database: Database, record: Mapping[str, object]) -> UUID:
    """The row ingestion committed for one exported record."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select id from public.browser_observations where observation_id = %s",
            (UUID(str(record["observation_id"])),),
        )
        row = await cur.fetchone()
    assert row is not None, "the ingestion socket committed no row for the milestone"
    return as_uuid(row, "id")


async def _export_milestone(
    database: Database,
    settings: Settings,
    keys: SigningKeys,
    base_url: str,
    websocket_url: str,
    candidate: PreviewFixture,
    *,
    browser_clock: datetime,
) -> UUID:
    """Export one `view_saved` milestone from a new candidate page and return its row id."""
    opened = await _open_candidate_page(
        database, settings, keys, base_url, websocket_url, candidate
    )
    record = _view_saved(opened, sequence=1, browser_clock=browser_clock)
    async with opened.exporter as exporter:
        await exporter.open_stream(resume_from=0)
        await exporter.send_batch([record], batch_id=None)
    return await _committed_id(database, record)


async def _state(database: Database, *, table: str, row_id: UUID) -> str:
    """Read one row's lifecycle state."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(f"select state from public.{table} where id = %s", (row_id,))
        row = await cur.fetchone()
    assert row is not None, f"{table}.{row_id} does not exist"
    return as_str(row, "state")
