"""The read-only query surface: every shape, and the authorization it enforces."""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest
import pytest_asyncio

from app.db.pool import Database
from app.evidence.queries import (
    QueryScope,
    before_and_after_comparison,
    browser_request_and_server_chain,
    delivery_gap_report,
    product_state_projection,
    registered_operation_errors,
    session_timeline,
    target_history,
    trace_with_completeness,
)
from app.evidence.windows import freeze_window, open_rolling_window
from app.ids import uuid7
from app.ingestion.errors import QueryAuthorizationError
from app.settings import Settings
from tests.evidence.helpers import ObservedSession, observe_session
from tests.ingestion.keys import SigningKeys
from tests.supabase_auth import SignedInUser


def _scope(
    observed: ObservedSession,
    *,
    projection: tuple[str, ...],
    source_classes: tuple[str, ...],
) -> QueryScope:
    """Build a scope naming the session, the purpose, and the projection."""
    return QueryScope(
        client_id=observed.preview.case.client_id,
        purpose="live_interview",
        interview_session_id=observed.preview.interview_session_id,
        allowed_source_classes=source_classes,  # type: ignore[arg-type]
        projection=projection,
        limit=50,
    )


@pytest_asyncio.fixture
async def observed(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
) -> ObservedSession:
    """One session with committed browser telemetry behind it."""
    return await observe_session(
        telemetry_database,
        settings,
        keys,
        ingest_server,
        ingest_websocket_url,
        name=f"query-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        record_count=5,
    )


async def test_every_query_shape_answers_with_its_freshness_and_provenance(
    telemetry_database: Database, observed: ObservedSession
) -> None:
    """Each shape returns canonical IDs plus the watermarks it was read at."""
    state = await product_state_projection(
        telemetry_database,
        _scope(
            observed,
            projection=("id", "observation_type", "route_revision"),
            source_classes=("browser_observation",),
        ),
    )
    assert state.shape == "product_state_projection"
    assert state.browser_accepted_through == 5
    assert any(row["observation_type"] == "route.change" for row in state.rows)

    timeline = await session_timeline(
        telemetry_database,
        _scope(
            observed,
            projection=("id", "observation_type", "export_sequence"),
            source_classes=("browser_observation",),
        ),
    )
    assert [int(str(row["export_sequence"])) for row in timeline.rows] == [1, 2, 3, 4, 5]

    trace = await trace_with_completeness(
        telemetry_database,
        _scope(
            observed,
            projection=("trace_id", "status"),
            source_classes=("browser_observation", "server_span"),
        ),
        trace_id=observed.trace_id,
    )
    assert trace.completeness["status"] == "pending"

    chain = await browser_request_and_server_chain(
        telemetry_database,
        _scope(
            observed,
            projection=("id", "span_id", "span_kind"),
            source_classes=("browser_observation", "server_span"),
        ),
        browser_request_id=observed.browser_request_id,
    )
    assert chain.completeness["span_count"] == 0

    history = await target_history(
        telemetry_database,
        _scope(
            observed,
            projection=("id", "target_id", "target_revision"),
            source_classes=("browser_observation",),
        ),
        target_id=observed.target_id,
    )
    assert history.completeness["revision_count"] == 3
    assert all(row["target_id"] == observed.target_id for row in history.rows)

    errors = await registered_operation_errors(
        telemetry_database,
        _scope(
            observed,
            projection=("id", "span_name", "status_code"),
            source_classes=("server_span",),
        ),
    )
    assert errors.completeness["error_count"] == 0

    gaps = await delivery_gap_report(
        telemetry_database,
        _scope(
            observed,
            projection=("id", "reason", "record_count"),
            source_classes=("browser_observation",),
        ),
    )
    assert gaps.completeness["gap_count"] == 0
    assert gaps.completeness["unacknowledged_records"] == 0


async def test_a_query_must_name_a_session_or_a_frozen_window(
    telemetry_database: Database, observed: ObservedSession
) -> None:
    """A query with no exact scope is refused before any row is read."""
    with pytest.raises(QueryAuthorizationError, match="exact Interview Session"):
        await session_timeline(
            telemetry_database,
            QueryScope(
                client_id=observed.preview.case.client_id,
                purpose="live_interview",
                allowed_source_classes=("browser_observation",),
                projection=("id",),
                limit=10,
            ),
        )


async def test_a_query_cannot_read_a_source_class_it_did_not_name(
    telemetry_database: Database, observed: ObservedSession
) -> None:
    """A shape refuses when its source class is outside the query's authorization."""
    with pytest.raises(QueryAuthorizationError, match="source class"):
        await session_timeline(
            telemetry_database,
            _scope(observed, projection=("id",), source_classes=("server_span",)),
        )


async def test_a_query_cannot_project_a_field_outside_its_shape(
    telemetry_database: Database, observed: ObservedSession
) -> None:
    """The projection is checked against the shape, not against the raw table."""
    with pytest.raises(QueryAuthorizationError, match="field content_sha256"):
        await session_timeline(
            telemetry_database,
            _scope(
                observed,
                projection=("id", "content_sha256"),
                source_classes=("browser_observation",),
            ),
        )


async def test_a_window_from_another_client_is_refused_before_lookup(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
    observed: ObservedSession,
) -> None:
    """A cross-client reference mismatch fails before anything is fetched."""
    other = await observe_session(
        telemetry_database,
        settings,
        keys,
        ingest_server,
        ingest_websocket_url,
        name=f"query-other-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        record_count=3,
    )
    foreign_window = await open_rolling_window(
        telemetry_database,
        client_id=other.preview.case.client_id,
        interview_session_id=other.preview.interview_session_id,
        product_signal_id=other.preview.case.product_signal_id,
        activation_id=other.activation_id,
        purpose="live_interview",
        authorized_caller="interview_controller",
        allowed_source_classes=("browser_observation",),
        query_contract={"shape": "session_timeline", "limit": 10},
        domain_identities={},
    )
    with pytest.raises(QueryAuthorizationError, match="another client"):
        await session_timeline(
            telemetry_database,
            QueryScope(
                client_id=observed.preview.case.client_id,
                purpose="gate_decision",
                evidence_window_id=foreign_window.evidence_window_id,
                allowed_source_classes=("browser_observation",),
                projection=("id",),
                limit=10,
            ),
        )


async def test_a_frozen_window_bounds_what_a_later_query_can_see(
    telemetry_database: Database, observed: ObservedSession
) -> None:
    """A query through a frozen window reads that window's cutoff, not now."""
    rolling = await open_rolling_window(
        telemetry_database,
        client_id=observed.preview.case.client_id,
        interview_session_id=observed.preview.interview_session_id,
        product_signal_id=observed.preview.case.product_signal_id,
        activation_id=observed.activation_id,
        purpose="live_interview",
        authorized_caller="interview_controller",
        allowed_source_classes=("browser_observation",),
        query_contract={"shape": "session_timeline", "limit": 50},
        domain_identities={},
    )
    frozen = await freeze_window(
        telemetry_database,
        source_window_id=rolling.evidence_window_id,
        purpose="gate_decision",
        authorized_caller="implementation_gate",
        completeness={"state": "sufficient"},
    )
    result = await session_timeline(
        telemetry_database,
        QueryScope(
            client_id=observed.preview.case.client_id,
            purpose="gate_decision",
            evidence_window_id=frozen.evidence_window_id,
            allowed_source_classes=("browser_observation",),
            projection=("id", "export_sequence"),
            limit=50,
        ),
    )
    assert result.accepted_through == frozen.watermarks.accepted_through
    assert result.browser_accepted_through == 5


async def test_a_validation_comparison_needs_two_frozen_windows_of_one_signal(
    telemetry_database: Database, observed: ObservedSession
) -> None:
    """A rolling window cannot stand in for a frozen before-and-after citation."""
    rolling = await open_rolling_window(
        telemetry_database,
        client_id=observed.preview.case.client_id,
        interview_session_id=observed.preview.interview_session_id,
        product_signal_id=observed.preview.case.product_signal_id,
        activation_id=observed.activation_id,
        purpose="live_interview",
        authorized_caller="interview_controller",
        allowed_source_classes=("browser_observation",),
        query_contract={"shape": "validation_comparison", "limit": 20},
        domain_identities={},
    )
    before = await freeze_window(
        telemetry_database,
        source_window_id=rolling.evidence_window_id,
        purpose="validation_outcome",
        authorized_caller="validation_controller",
        completeness={"state": "sufficient"},
    )
    scope = QueryScope(
        client_id=observed.preview.case.client_id,
        purpose="validation_outcome",
        interview_session_id=observed.preview.interview_session_id,
        allowed_source_classes=("browser_observation",),
        projection=("id", "claim_ref", "supports"),
        limit=20,
    )
    with pytest.raises(QueryAuthorizationError, match="rolling"):
        await before_and_after_comparison(
            telemetry_database,
            scope,
            before_window_id=before.evidence_window_id,
            after_window_id=rolling.evidence_window_id,
        )

    after = await freeze_window(
        telemetry_database,
        source_window_id=rolling.evidence_window_id,
        purpose="validation_outcome",
        authorized_caller="validation_controller",
        completeness={"state": "sufficient"},
    )
    comparison = await before_and_after_comparison(
        telemetry_database,
        scope,
        before_window_id=before.evidence_window_id,
        after_window_id=after.evidence_window_id,
    )
    assert comparison.shape == "before_and_after_validation_comparison"
    assert comparison.completeness["before_evidence_window_id"] == str(
        before.evidence_window_id
    )


async def test_staff_authorization_is_enforced_on_the_http_surface(
    telemetry_database: Database,
    observed: ObservedSession,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A signed-in identity with no membership row reads nothing."""
    body = {
        "scope": {
            "client_id": str(observed.preview.case.client_id),
            "purpose": "live_interview",
            "interview_session_id": str(observed.preview.interview_session_id),
            "allowed_source_classes": ["browser_observation"],
            "projection": ["id", "observation_type"],
            "limit": 10,
        }
    }
    path = f"/v1/clients/{observed.preview.case.client_id}/telemetry/session-timeline"
    async with httpx.AsyncClient(base_url=ingest_server, timeout=30.0) as client:
        unauthenticated = await client.post(path, json=body)
        without_membership = await client.post(
            path,
            json=body,
            headers={
                "authorization": f"Bearer {auth_users['owner_b'].access_token}"
            },
        )
    assert unauthenticated.status_code == 403
    assert without_membership.status_code == 403


async def test_a_member_reads_its_own_client_telemetry(
    telemetry_database: Database,
    observed: ObservedSession,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """An active membership row is what grants the read, not the bearer token."""
    member_id = uuid7()
    async with telemetry_database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.client_members (id, client_id, auth_user_id, role, status)
            values (%s, %s, %s, 'owner', 'active')
            """,
            (
                member_id,
                observed.preview.case.client_id,
                auth_users["owner_a"].auth_user_id,
            ),
        )
    path = f"/v1/clients/{observed.preview.case.client_id}/telemetry/session-timeline"
    async with httpx.AsyncClient(base_url=ingest_server, timeout=30.0) as client:
        response = await client.post(
            path,
            json={
                "scope": {
                    "client_id": str(observed.preview.case.client_id),
                    "purpose": "live_interview",
                    "interview_session_id": str(
                        observed.preview.interview_session_id
                    ),
                    "allowed_source_classes": ["browser_observation"],
                    "projection": ["id", "observation_type"],
                    "limit": 10,
                }
            },
            headers={
                "authorization": f"Bearer {auth_users['owner_a'].access_token}"
            },
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["shape"] == "session_timeline"
    assert len(payload["rows"]) == 5


async def test_a_scope_cannot_name_a_client_other_than_its_path(
    telemetry_database: Database,
    observed: ObservedSession,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """The authenticated path is the authority; the body cannot widen it."""
    member_id = uuid7()
    async with telemetry_database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.client_members (id, client_id, auth_user_id, role, status)
            values (%s, %s, %s, 'owner', 'active')
            """,
            (
                member_id,
                observed.preview.case.client_id,
                auth_users["owner_a"].auth_user_id,
            ),
        )
    path = f"/v1/clients/{observed.preview.case.client_id}/telemetry/session-timeline"
    async with httpx.AsyncClient(base_url=ingest_server, timeout=30.0) as client:
        response = await client.post(
            path,
            json={
                "scope": {
                    "client_id": str(UUID(int=7)),
                    "purpose": "live_interview",
                    "interview_session_id": str(
                        observed.preview.interview_session_id
                    ),
                    "allowed_source_classes": ["browser_observation"],
                    "projection": ["id"],
                    "limit": 10,
                }
            },
            headers={
                "authorization": f"Bearer {auth_users['owner_a'].access_token}"
            },
        )
    assert response.status_code == 403
    assert response.json()["error"] == "QueryAuthorizationError"
