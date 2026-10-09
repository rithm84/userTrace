"""Criterion evidence read back from telemetry the ingestion path committed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from utrace_contracts.domain.lifecycle import EvidenceClass

from app.adapters.telemetry_evidence import TelemetryEvidenceQuery
from app.db.pool import Database
from app.ids import uuid7
from app.ingestion.errors import QueryAuthorizationError
from app.settings import Settings
from app.validation.evidence import ADMISSIBLE_OBSERVED_CLASSES, EvidenceStance
from tests.evidence.helpers import ObservedSession, observe_session
from tests.ingestion.keys import SigningKeys
from tests.supabase_auth import SignedInUser


async def _one_observation(database: Database, *, session: ObservedSession) -> UUID:
    """Return one committed browser observation to cite."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.browser_observations
            where activation_id = %s and observation_type = 'interaction.activation'
            order by source_sequence limit 1
            """,
            (session.activation_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    return UUID(str(row["id"]))


async def _criterion(database: Database, *, session: ObservedSession) -> UUID:
    """Insert one frozen acceptance criterion for the case."""
    criterion_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.validation_criteria (
                id, client_id, product_signal_id, criteria_revision, ordinal,
                statement, observability
            )
            values (%s, %s, %s, 1, 1, %s, 'observed')
            """,
            (
                criterion_id,
                session.preview.case.client_id,
                session.preview.case.product_signal_id,
                "the saved view reopens with every filter restored",
            ),
        )
    return criterion_id


async def test_a_frozen_window_links_the_telemetry_state_it_saw(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Freezing writes both windows and records which one cites the other."""
    session = await observe_session(
        telemetry_database,
        settings,
        keys,
        ingest_server,
        ingest_websocket_url,
        name=f"freeze-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        record_count=4,
    )
    query = TelemetryEvidenceQuery(telemetry_database)
    now = datetime.now(tz=UTC)
    window = await query.freeze_window(
        client_id=session.preview.case.client_id,
        product_signal_id=session.preview.case.product_signal_id,
        interview_session_id=session.preview.interview_session_id,
        purpose="validation_outcome",
        starts_at=now - timedelta(minutes=30),
        ends_at=now,
    )

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, mode, browser_accepted_through, correlation_revision
            from public.telemetry_evidence_windows
            where control_plane_evidence_window_id = %s
            """,
            (window.evidence_window_id,),
        )
        frozen = await cur.fetchone()
    assert frozen is not None
    assert frozen["mode"] == "frozen"
    # The window records the watermark the decision actually saw.
    assert int(str(frozen["browser_accepted_through"])) == 4


async def test_promoted_telemetry_returns_as_criterion_evidence(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A promoted observation is readable through the validation port's query."""
    session = await observe_session(
        telemetry_database,
        settings,
        keys,
        ingest_server,
        ingest_websocket_url,
        name=f"promote-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        record_count=4,
    )
    query = TelemetryEvidenceQuery(telemetry_database)
    criterion_id = await _criterion(telemetry_database, session=session)
    source_record_id = await _one_observation(telemetry_database, session=session)

    rolling = await query.open_window(
        client_id=session.preview.case.client_id,
        product_signal_id=session.preview.case.product_signal_id,
        interview_session_id=session.preview.interview_session_id,
        purpose="live_interview",
    )
    record_id = await query.promote_observation(
        client_id=session.preview.case.client_id,
        product_signal_id=session.preview.case.product_signal_id,
        interview_session_id=session.preview.interview_session_id,
        telemetry_window_id=rolling.evidence_window_id,
        source_class="browser_observation",
        source_record_id=source_record_id,
        criterion_id=criterion_id,
        stance=EvidenceStance.SUPPORTS,
        summary="the participant reopened the saved view and the filters returned",
        workflow_ref="saved_conversion_views",
        milestone="view_reopened",
        preview_version_id=None,
        coverage_limit=None,
        observed_at=None,
    )

    now = datetime.now(tz=UTC)
    window = await query.freeze_window(
        client_id=session.preview.case.client_id,
        product_signal_id=session.preview.case.product_signal_id,
        interview_session_id=session.preview.interview_session_id,
        purpose="validation_outcome",
        starts_at=now - timedelta(minutes=30),
        ends_at=now + timedelta(minutes=1),
    )
    items = await query.criterion_evidence(
        client_id=session.preview.case.client_id,
        evidence_window_id=window.evidence_window_id,
        validation_criterion_id=criterion_id,
        allowed_source_classes=ADMISSIBLE_OBSERVED_CLASSES,
        limit=20,
    )
    assert len(items) == 1
    item = items[0]
    assert item.evidence_record_id == record_id
    assert item.evidence_class is EvidenceClass.BROWSER_OBSERVATION
    assert item.stance is EvidenceStance.SUPPORTS
    assert item.is_observed_product_behaviour()
    assert item.source_ref == f"telemetry:browser_observation/{source_record_id}"

    # The telemetry half keeps the provenance the citation needs.
    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select source_class, source_record_id, claim_ref, supports,
                   policy_version, watermarks
            from public.telemetry_evidence_items
            where evidence_window_id = %s
            """,
            (rolling.evidence_window_id,),
        )
        promoted = await cur.fetchone()
    assert promoted is not None
    assert promoted["source_class"] == "browser_observation"
    assert UUID(str(promoted["source_record_id"])) == source_record_id
    assert str(promoted["claim_ref"]) == str(criterion_id)
    assert promoted["supports"] is True
    assert isinstance(promoted["watermarks"], dict)


async def test_a_workflow_comparison_separates_base_from_candidate(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Promoted observations sort onto the Preview Version they were seen on."""
    session = await observe_session(
        telemetry_database,
        settings,
        keys,
        ingest_server,
        ingest_websocket_url,
        name=f"compare-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        record_count=5,
    )
    query = TelemetryEvidenceQuery(telemetry_database)
    criterion_id = await _criterion(telemetry_database, session=session)
    rolling = await query.open_window(
        client_id=session.preview.case.client_id,
        product_signal_id=session.preview.case.product_signal_id,
        interview_session_id=session.preview.interview_session_id,
        purpose="live_interview",
    )
    candidate_id = uuid7()

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.browser_observations
            where activation_id = %s and observation_type = 'interaction.activation'
            order by source_sequence limit 2
            """,
            (session.activation_id,),
        )
        rows = list(await cur.fetchall())
        await cur.execute(
            """
            select id from public.browser_observations
            where activation_id = %s and id <> all(%s)
            order by source_sequence limit 1
            """,
            (session.activation_id, [row["id"] for row in rows]),
        )
        reload = await cur.fetchone()
    assert len(rows) == 2
    assert reload is not None

    await query.promote_observation(
        client_id=session.preview.case.client_id,
        product_signal_id=session.preview.case.product_signal_id,
        interview_session_id=session.preview.interview_session_id,
        telemetry_window_id=rolling.evidence_window_id,
        source_class="browser_observation",
        source_record_id=UUID(str(rows[0]["id"])),
        criterion_id=criterion_id,
        stance=EvidenceStance.CONTRADICTS,
        summary="the filters were lost on reload",
        workflow_ref="saved_conversion_views",
        milestone="view_reopened",
        preview_version_id=None,
        coverage_limit="no server span accompanied the reload",
        observed_at=None,
    )
    await query.promote_observation(
        client_id=session.preview.case.client_id,
        product_signal_id=session.preview.case.product_signal_id,
        interview_session_id=session.preview.interview_session_id,
        telemetry_window_id=rolling.evidence_window_id,
        source_class="browser_observation",
        source_record_id=UUID(str(rows[1]["id"])),
        criterion_id=criterion_id,
        stance=EvidenceStance.SUPPORTS,
        summary="the filters returned on the candidate",
        workflow_ref="saved_conversion_views",
        milestone="view_reopened",
        preview_version_id=candidate_id,
        coverage_limit=None,
        observed_at=None,
    )
    # A criterion may cite an observation that is no product milestone, such as the
    # page reload that shows a saved view survives a refresh.
    await query.promote_observation(
        client_id=session.preview.case.client_id,
        product_signal_id=session.preview.case.product_signal_id,
        interview_session_id=session.preview.interview_session_id,
        telemetry_window_id=rolling.evidence_window_id,
        source_class="browser_observation",
        source_record_id=UUID(str(reload["id"])),
        criterion_id=criterion_id,
        stance=EvidenceStance.SUPPORTS,
        summary="the page reloaded",
        workflow_ref="saved_conversion_views",
        milestone=None,
        preview_version_id=candidate_id,
        coverage_limit=None,
        observed_at=None,
    )

    now = datetime.now(tz=UTC)
    window = await query.freeze_window(
        client_id=session.preview.case.client_id,
        product_signal_id=session.preview.case.product_signal_id,
        interview_session_id=session.preview.interview_session_id,
        purpose="case_file_core",
        starts_at=now - timedelta(minutes=30),
        ends_at=now + timedelta(minutes=1),
    )
    comparison = await query.before_after_comparison(
        client_id=session.preview.case.client_id,
        evidence_window_id=window.evidence_window_id,
        workflow_ref="saved_conversion_views",
        base_preview_version_id=None,
        candidate_preview_version_id=candidate_id,
    )
    assert [o.milestone for o in comparison.candidate_observations] == ["view_reopened", None]
    assert {o.preview_version_id for o in comparison.candidate_observations} == {candidate_id}
    # An observation with no Preview Version is the base run of the workflow.
    assert len(comparison.base_observations) == 1
    assert comparison.coverage_limits == ("no server span accompanied the reload",)


async def test_a_source_class_the_window_does_not_admit_is_refused(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A promotion cannot name a class the window was not opened for."""
    session = await observe_session(
        telemetry_database,
        settings,
        keys,
        ingest_server,
        ingest_websocket_url,
        name=f"refuse-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        record_count=3,
    )
    query = TelemetryEvidenceQuery(telemetry_database)
    rolling = await query.open_window(
        client_id=session.preview.case.client_id,
        product_signal_id=session.preview.case.product_signal_id,
        interview_session_id=session.preview.interview_session_id,
        purpose="live_interview",
    )
    with pytest.raises(QueryAuthorizationError):
        await query.promote_observation(
            client_id=session.preview.case.client_id,
            product_signal_id=session.preview.case.product_signal_id,
            interview_session_id=session.preview.interview_session_id,
            telemetry_window_id=rolling.evidence_window_id,
            source_class="transcript_fragment",
            source_record_id=uuid7(),
            criterion_id=uuid7(),
            stance=EvidenceStance.SUPPORTS,
            summary="a user statement is not telemetry",
            workflow_ref=None,
            milestone=None,
            preview_version_id=None,
            coverage_limit=None,
            observed_at=None,
        )
