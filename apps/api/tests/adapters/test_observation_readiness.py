"""The preview stream's three observation probes, answered from ingestion state."""

from __future__ import annotations

from uuid import UUID

from app.adapters.observation_readiness import IngestionObservationReadiness
from app.db.pool import Database
from app.ids import uuid7
from app.ingestion.jobs import evaluate_readiness
from app.ingestion.readiness import withdraw_readiness
from app.settings import Settings
from tests.ingestion.factories import create_preview, start_server_activation
from tests.ingestion.keys import SigningKeys
from tests.supabase_auth import SignedInUser


async def test_an_unobserved_preview_reports_no_activation(
    telemetry_database: Database,
    settings: Settings,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Without a server activation the probe fails, naming what is missing."""
    preview = await create_preview(
        telemetry_database,
        name=f"unobserved-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    port = IngestionObservationReadiness(telemetry_database)
    activation = await port.activation(
        client_id=preview.case.client_id,
        interview_session_id=preview.interview_session_id,
        preview_runtime_allocation_id=preview.preview_runtime_allocation_id,
    )
    assert not activation.activated
    assert activation.preview_entry_token is None
    assert "has not activated" in activation.detail

    readiness = await port.readiness(
        client_id=preview.case.client_id,
        interview_session_id=preview.interview_session_id,
    )
    assert not readiness.browser_ready
    assert not readiness.server_ready
    assert not readiness.correlation_ready
    assert "no readiness projection" in readiness.detail


async def test_an_activated_preview_reports_its_entry_token_and_failures(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    auth_users: dict[str, SignedInUser],
) -> None:
    """The port reports the committed activation and the typed readiness failures."""
    preview = await create_preview(
        telemetry_database,
        name=f"observed-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    activation_id, _stream, _token, entry_token = await start_server_activation(
        telemetry_database, preview=preview, keys=keys
    )
    port = IngestionObservationReadiness(telemetry_database)

    observed = await port.activation(
        client_id=preview.case.client_id,
        interview_session_id=preview.interview_session_id,
        preview_runtime_allocation_id=preview.preview_runtime_allocation_id,
    )
    assert observed.activated
    assert observed.service_name == "shopalytics.api"
    assert observed.preview_entry_token == entry_token
    assert observed.activated_at is not None
    assert observed.detail == ""

    # No browser stream and no correlated trace: every kind reports its reason.
    await evaluate_readiness(
        telemetry_database,
        client_id=preview.case.client_id,
        activation_id=activation_id,
    )
    readiness = await port.readiness(
        client_id=preview.case.client_id,
        interview_session_id=preview.interview_session_id,
    )
    assert not readiness.browser_ready
    assert not readiness.correlation_ready
    assert "no_browser_stream" in readiness.detail
    assert "no_correlated_trace" in readiness.detail
    # The server stream exists and has nothing outstanding, so that kind is ready.
    assert readiness.server_ready


async def test_a_withdrawn_projection_is_not_readiness(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A later withdrawal supersedes an earlier ready projection."""
    preview = await create_preview(
        telemetry_database,
        name=f"withdrawn-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    activation_id, _stream, _token, _entry = await start_server_activation(
        telemetry_database, preview=preview, keys=keys
    )
    await evaluate_readiness(
        telemetry_database,
        client_id=preview.case.client_id,
        activation_id=activation_id,
    )
    port = IngestionObservationReadiness(telemetry_database)
    assert (
        await port.readiness(
            client_id=preview.case.client_id,
            interview_session_id=preview.interview_session_id,
        )
    ).server_ready

    withdrawn = await withdraw_readiness(
        telemetry_database,
        client_id=preview.case.client_id,
        activation_id=activation_id,
        interview_session_id=preview.interview_session_id,
        kind="server",
        failure_code="ingestion_unavailable",
        detail={"reason": "durable storage is unavailable"},
    )
    assert withdrawn.revision > 1

    readiness = await port.readiness(
        client_id=preview.case.client_id,
        interview_session_id=preview.interview_session_id,
    )
    assert not readiness.server_ready
    assert "ingestion_unavailable" in readiness.detail


async def test_a_stale_projection_never_authorizes_probing(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    auth_users: dict[str, SignedInUser],
) -> None:
    """An expired projection reads as not ready, with its expiry named."""
    preview = await create_preview(
        telemetry_database,
        name=f"stale-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    activation_id, _stream, _token, _entry = await start_server_activation(
        telemetry_database, preview=preview, keys=keys
    )
    async with telemetry_database.transaction() as conn, conn.cursor() as cur:
        for kind in ("browser", "server", "correlation"):
            await cur.execute(
                """
                insert into public.telemetry_readiness_projections (
                    id, client_id, activation_id, interview_session_id, kind,
                    revision, ready, contributing_resource_ids,
                    accepted_watermarks, failures, checked_at, expires_at
                )
                values (
                    %s, %s, %s, %s, %s, 1, true, '[]'::jsonb, '{}'::jsonb,
                    '[]'::jsonb, now() - interval '5 minutes',
                    now() - interval '4 minutes'
                )
                """,
                (
                    uuid7(),
                    preview.case.client_id,
                    activation_id,
                    preview.interview_session_id,
                    kind,
                ),
            )

    readiness = await IngestionObservationReadiness(telemetry_database).readiness(
        client_id=preview.case.client_id,
        interview_session_id=preview.interview_session_id,
    )
    assert not readiness.browser_ready
    assert not readiness.server_ready
    assert not readiness.correlation_ready
    assert readiness.detail.count("the projection expired at") == 3


async def test_another_clients_activation_is_never_reported(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    auth_users: dict[str, SignedInUser],
) -> None:
    """The port refuses to answer across a client boundary."""
    preview = await create_preview(
        telemetry_database,
        name=f"scoped-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    await start_server_activation(telemetry_database, preview=preview, keys=keys)
    other = await create_preview(
        telemetry_database,
        name=f"other-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_b"].auth_user_id,
    )
    activation = await IngestionObservationReadiness(telemetry_database).activation(
        client_id=other.case.client_id,
        interview_session_id=preview.interview_session_id,
        preview_runtime_allocation_id=preview.preview_runtime_allocation_id,
    )
    assert not activation.activated
    assert activation.preview_entry_token is None
    assert isinstance(preview.preview_runtime_allocation_id, UUID)
