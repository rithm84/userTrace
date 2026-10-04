"""Evidence windows: rolling advance, freezing, promotion, and materialization."""

from __future__ import annotations

import secrets
from uuid import UUID

import pytest

from app.db.pool import Database
from app.evidence.promotion import PromotionRequest, materialize_window, promote_items
from app.evidence.windows import (
    advance_rolling_window,
    freeze_window,
    load_window,
    open_rolling_window,
)
from app.ids import uuid7
from app.ingestion.artifacts import ArtifactStore
from app.ingestion.errors import QueryAuthorizationError, WindowFrozenError
from app.settings import Settings
from tests.evidence.helpers import ObservedSession, observe_session
from tests.ingestion.activation_client import activate_document
from tests.ingestion.browser_client import BrowserExporter, observation
from tests.ingestion.factories import (
    POLICY_VERSION,
    REDACTION_VERSION,
    front_door_credential,
)
from tests.ingestion.keys import SigningKeys
from tests.supabase_auth import SignedInUser


async def _open_window(
    database: Database, session_activation_id: UUID, client_id: UUID, **identities: UUID
) -> UUID:
    """Open one rolling live window over a session."""
    window = await open_rolling_window(
        database,
        client_id=client_id,
        interview_session_id=identities["interview_session_id"],
        product_signal_id=identities["product_signal_id"],
        activation_id=session_activation_id,
        purpose="live_interview",
        authorized_caller="interview_controller",
        allowed_source_classes=("browser_observation", "server_span"),
        query_contract={"shape": "session_timeline", "limit": 100},
        domain_identities={
            "interview_session_id": str(identities["interview_session_id"]),
            "product_signal_id": str(identities["product_signal_id"]),
        },
    )
    return window.evidence_window_id


async def test_a_frozen_window_is_immune_to_later_telemetry(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Data that arrives after the freeze cannot reach the frozen selection."""
    observed = await observe_session(
        telemetry_database,
        settings,
        keys,
        ingest_server,
        ingest_websocket_url,
        name=f"freeze-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        record_count=4,
    )
    rolling_id = await _open_window(
        telemetry_database,
        observed.activation_id,
        observed.preview.case.client_id,
        interview_session_id=observed.preview.interview_session_id,
        product_signal_id=observed.preview.case.product_signal_id,
    )
    await advance_rolling_window(telemetry_database, evidence_window_id=rolling_id)
    frozen = await freeze_window(
        telemetry_database,
        source_window_id=rolling_id,
        purpose="gate_decision",
        authorized_caller="implementation_gate",
        completeness={"state": "sufficient"},
    )
    assert frozen.watermarks.browser_accepted_through == 4

    reloaded_frozen = await load_window(
        telemetry_database, evidence_window_id=frozen.evidence_window_id
    )
    reloaded_rolling = await load_window(
        telemetry_database, evidence_window_id=rolling_id
    )
    assert reloaded_frozen.is_frozen()
    assert reloaded_frozen.watermarks.browser_accepted_through == 4
    assert reloaded_rolling.is_frozen() is False

    with pytest.raises(WindowFrozenError):
        await advance_rolling_window(
            telemetry_database, evidence_window_id=frozen.evidence_window_id
        )
    with pytest.raises(WindowFrozenError):
        await freeze_window(
            telemetry_database,
            source_window_id=frozen.evidence_window_id,
            purpose="validation_outcome",
            authorized_caller="validation_controller",
            completeness={"state": "sufficient"},
        )


async def test_promotion_respects_the_window_source_classes(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A window that admits only browser observations cannot cite a server span."""
    observed = await observe_session(
        telemetry_database,
        settings,
        keys,
        ingest_server,
        ingest_websocket_url,
        name=f"promote-class-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        record_count=3,
    )
    window = await open_rolling_window(
        telemetry_database,
        client_id=observed.preview.case.client_id,
        interview_session_id=observed.preview.interview_session_id,
        product_signal_id=observed.preview.case.product_signal_id,
        activation_id=observed.activation_id,
        purpose="live_interview",
        authorized_caller="interview_controller",
        allowed_source_classes=("browser_observation",),
        query_contract={"shape": "session_timeline", "limit": 50},
        domain_identities={"interview_session_id": str(observed.preview.interview_session_id)},
    )
    with pytest.raises(QueryAuthorizationError):
        await promote_items(
            telemetry_database,
            evidence_window_id=window.evidence_window_id,
            requests=[
                PromotionRequest(
                    source_class="server_span",
                    source_record_id=uuid7(),
                    claim_ref="criterion.save",
                    supports=True,
                )
            ],
        )


async def test_promoted_items_materialize_as_immutable_artifacts(
    telemetry_database: Database,
    artifacts: ArtifactStore,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A citation survives its source record by becoming its own artifact."""
    observed = await observe_session(
        telemetry_database,
        settings,
        keys,
        ingest_server,
        ingest_websocket_url,
        name=f"materialize-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        record_count=3,
    )
    rolling_id = await _open_window(
        telemetry_database,
        observed.activation_id,
        observed.preview.case.client_id,
        interview_session_id=observed.preview.interview_session_id,
        product_signal_id=observed.preview.case.product_signal_id,
    )
    frozen = await freeze_window(
        telemetry_database,
        source_window_id=rolling_id,
        purpose="validation_outcome",
        authorized_caller="validation_controller",
        completeness={"state": "sufficient"},
    )
    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select id from public.browser_observations where browser_stream_id = %s "
            "order by export_sequence limit 1",
            (observed.browser_stream_id,),
        )
        record = await cur.fetchone()
    assert record is not None

    promoted = await promote_items(
        telemetry_database,
        evidence_window_id=frozen.evidence_window_id,
        requests=[
            PromotionRequest(
                source_class="browser_observation",
                source_record_id=UUID(str(record["id"])),
                claim_ref="criterion.filters_restored",
                supports=True,
            )
        ],
    )
    assert len(promoted) == 1

    materialized = await materialize_window(
        telemetry_database,
        artifacts=artifacts,
        evidence_window_id=frozen.evidence_window_id,
    )
    assert len(materialized) == 1
    assert materialized[0].artifact_id is not None
    body = await artifacts.resolve(
        telemetry_database,
        client_id=observed.preview.case.client_id,
        artifact_id=materialized[0].artifact_id,
        expected_class="promoted_evidence",
    )
    assert b"criterion.filters_restored" in body
    assert b"browser_observation" in body

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select source_class, artifact_id, watermarks from "
            "public.telemetry_evidence_items where evidence_window_id = %s",
            (frozen.evidence_window_id,),
        )
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["source_class"] == "browser_observation"
    assert rows[0]["artifact_id"] is not None
    assert rows[0]["watermarks"]["browser_accepted_through"] == 3


async def test_a_promoted_item_cannot_be_rewritten(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Only the materialization write is allowed; the citation itself is frozen."""
    observed = await observe_session(
        telemetry_database,
        settings,
        keys,
        ingest_server,
        ingest_websocket_url,
        name=f"immutable-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        record_count=3,
    )
    rolling_id = await _open_window(
        telemetry_database,
        observed.activation_id,
        observed.preview.case.client_id,
        interview_session_id=observed.preview.interview_session_id,
        product_signal_id=observed.preview.case.product_signal_id,
    )
    frozen = await freeze_window(
        telemetry_database,
        source_window_id=rolling_id,
        purpose="case_file_core",
        authorized_caller="client_outputs",
        completeness={"state": "sufficient"},
    )
    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select id from public.browser_observations where browser_stream_id = %s "
            "order by export_sequence limit 1",
            (observed.browser_stream_id,),
        )
        record = await cur.fetchone()
    assert record is not None
    await promote_items(
        telemetry_database,
        evidence_window_id=frozen.evidence_window_id,
        requests=[
            PromotionRequest(
                source_class="browser_observation",
                source_record_id=UUID(str(record["id"])),
                claim_ref="criterion.save",
                supports=True,
            )
        ],
    )
    with pytest.raises(Exception, match="materialization write"):
        async with telemetry_database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                "update public.telemetry_evidence_items set supports = false "
                "where evidence_window_id = %s",
                (frozen.evidence_window_id,),
            )


async def test_late_telemetry_advances_a_rolling_window_only(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """The live view keeps moving while the decision's selection stays put."""
    observed = await observe_session(
        telemetry_database,
        settings,
        keys,
        ingest_server,
        ingest_websocket_url,
        name=f"late-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        record_count=3,
    )
    rolling_id = await _open_window(
        telemetry_database,
        observed.activation_id,
        observed.preview.case.client_id,
        interview_session_id=observed.preview.interview_session_id,
        product_signal_id=observed.preview.case.product_signal_id,
    )
    await advance_rolling_window(telemetry_database, evidence_window_id=rolling_id)
    frozen = await freeze_window(
        telemetry_database,
        source_window_id=rolling_id,
        purpose="gate_decision",
        authorized_caller="implementation_gate",
        completeness={"state": "sufficient"},
    )
    assert frozen.watermarks.browser_accepted_through == 3

    later = await _export_more_on_a_new_document(
        settings, keys, ingest_server, ingest_websocket_url, observed=observed
    )
    assert later == 4

    advanced = await advance_rolling_window(
        telemetry_database, evidence_window_id=rolling_id
    )
    still_frozen = await load_window(
        telemetry_database, evidence_window_id=frozen.evidence_window_id
    )
    assert advanced.watermarks.browser_accepted_through == 4
    assert still_frozen.watermarks.browser_accepted_through == 3


async def _export_more_on_a_new_document(
    settings: Settings,
    keys: SigningKeys,
    base_url: str,
    websocket_url: str,
    *,
    observed: ObservedSession,
) -> int:
    """Activate a replacement document and export a fresh run of observations.

    A reload starts a new stream at sequence one, which is exactly the late
    telemetry a frozen window must not absorb.
    """
    document = await activate_document(
        base_url,
        service_token=settings.front_door_service_token,
        session_credential=front_door_credential(settings, observed.preview),
        public_preview_host=observed.preview.origin_host,
        document_origin=observed.preview.document_origin,
        document_nonce=secrets.token_hex(24),
        sdk_installation_id="shopalytics-browser-sdk",
        public_key=keys.attestation_public,
    )
    browser_stream_id = uuid7()
    exporter = BrowserExporter(
        websocket_url,
        token=document.token,
        scope={
            "client_id": str(observed.preview.case.client_id),
            "client_user_id": str(observed.preview.case.client_user_id),
            "customer_account_id": str(observed.preview.case.customer_account_id),
            "session_id": str(observed.preview.interview_session_id),
            "preview_environment_id": str(
                observed.preview.preview_environment_id
            ),
            "preview_runtime_allocation_id": str(
                observed.preview.preview_runtime_allocation_id
            ),
            "browser_stream_id": str(browser_stream_id),
            "schema_version": "1",
            "policy_version": POLICY_VERSION,
            "redaction_version": REDACTION_VERSION,
        },
        browser_stream_id=browser_stream_id,
        runtime_id=uuid7(),
        document_attachment_id=UUID(str(document.attestation["document_attachment_id"])),
    )
    async with exporter:
        await exporter.open_stream(resume_from=0)
        ack = await exporter.send_batch(
            [
                observation(
                    observation_type="interaction.activation",
                    runtime_id=exporter.runtime_id,
                    document_attachment_id=exporter.document_attachment_id,
                    source_sequence=index,
                    export_sequence=index,
                    payload={
                        "actor": "user",
                        "activation_kind": "click",
                        "role": "button",
                        "label": None,
                        "entity_ref": None,
                        "surface": "conversion_dashboard",
                    },
                    trace_id=None,
                    span_id=None,
                    target_id=observed.target_id,
                    occurred_at=None,
                )
                for index in range(1, 5)
            ],
            batch_id=None,
        )
    return int(str(ack["accepted_through_export_sequence"]))
