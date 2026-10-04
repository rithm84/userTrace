"""Shared setup for the evidence tests: real telemetry behind a real session."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from uuid import UUID

from app.db.pool import Database
from app.ids import uuid7
from app.settings import Settings
from tests.ingestion.activation_client import activate_document
from tests.ingestion.browser_client import BrowserExporter, observation, request_started
from tests.ingestion.factories import (
    POLICY_VERSION,
    REDACTION_VERSION,
    PreviewFixture,
    create_preview,
    front_door_credential,
)
from tests.ingestion.keys import SigningKeys

__all__ = ["ObservedSession", "OpenedDocument", "observe_session", "open_document"]


@dataclass(frozen=True, slots=True)
class ObservedSession:
    """One session with committed browser telemetry behind it."""

    preview: PreviewFixture
    activation_id: UUID
    browser_stream_id: UUID
    trace_id: str
    target_id: str
    browser_request_id: str


@dataclass(frozen=True, slots=True)
class OpenedDocument:
    """One activated preview document and the exporter streaming from it."""

    exporter: BrowserExporter
    activation_id: UUID
    document_attachment_id: UUID
    browser_stream_id: UUID
    runtime_id: UUID


async def open_document(
    settings: Settings,
    keys: SigningKeys,
    base_url: str,
    websocket_url: str,
    *,
    preview: PreviewFixture,
    preview_version_id: UUID | None,
) -> OpenedDocument:
    """Activate one document on the preview's allocation through real ingestion.

    A candidate allocation's activation records its Preview Version, and the stream
    scope has to claim the same one.
    """
    document = await activate_document(
        base_url,
        service_token=settings.front_door_service_token,
        session_credential=front_door_credential(settings, preview),
        public_preview_host=preview.origin_host,
        document_origin=preview.document_origin,
        document_nonce=secrets.token_hex(24),
        sdk_installation_id="shopalytics-browser-sdk",
        public_key=keys.attestation_public,
    )
    attachment = UUID(str(document.attestation["document_attachment_id"]))
    browser_stream_id = uuid7()
    runtime_id = uuid7()
    scope: dict[str, str] = {
        "client_id": str(preview.case.client_id),
        "client_user_id": str(preview.case.client_user_id),
        "customer_account_id": str(preview.case.customer_account_id),
        "session_id": str(preview.interview_session_id),
        "preview_environment_id": str(preview.preview_environment_id),
        "preview_runtime_allocation_id": str(preview.preview_runtime_allocation_id),
        "browser_stream_id": str(browser_stream_id),
        "schema_version": "1",
        "policy_version": POLICY_VERSION,
        "redaction_version": REDACTION_VERSION,
    }
    if preview_version_id is not None:
        scope["preview_version_id"] = str(preview_version_id)
    exporter = BrowserExporter(
        websocket_url,
        token=document.token,
        scope=scope,
        browser_stream_id=browser_stream_id,
        runtime_id=runtime_id,
        document_attachment_id=attachment,
    )
    return OpenedDocument(
        exporter=exporter,
        activation_id=UUID(str(document.attestation["attestation_id"])),
        document_attachment_id=attachment,
        browser_stream_id=browser_stream_id,
        runtime_id=runtime_id,
    )


async def observe_session(
    database: Database,
    settings: Settings,
    keys: SigningKeys,
    base_url: str,
    websocket_url: str,
    *,
    name: str,
    authorizing_auth_user_id: UUID,
    record_count: int,
) -> ObservedSession:
    """Activate a document and export a bounded run of real observations."""
    preview = await create_preview(
        database, name=name, authorizing_auth_user_id=authorizing_auth_user_id
    )
    opened = await open_document(
        settings, keys, base_url, websocket_url, preview=preview, preview_version_id=None
    )
    exporter = opened.exporter
    attachment = opened.document_attachment_id
    runtime_id = opened.runtime_id
    trace_id = secrets.token_hex(16)
    target_id = "tgt_7_save_view"
    browser_request_id = "req_saved_view"

    records = [
        observation(
            observation_type="route.change",
            runtime_id=runtime_id,
            document_attachment_id=attachment,
            source_sequence=1,
            export_sequence=1,
            payload={
                "route": {
                    "route_template": "/dashboard/conversion",
                    "unknown_route": False,
                    "segment_count": 2,
                    "has_query": False,
                    "has_fragment": False,
                    "params": [],
                },
                "navigation_kind": "push",
                "settled": True,
            },
            trace_id=None,
            span_id=None,
            target_id=None,
            occurred_at=None,
        ),
        observation(
            observation_type="request.started",
            runtime_id=runtime_id,
            document_attachment_id=attachment,
            source_sequence=2,
            export_sequence=2,
            payload=request_started(browser_request_id),
            trace_id=trace_id,
            span_id=secrets.token_hex(8),
            target_id=None,
            occurred_at=None,
        ),
    ]
    for index in range(3, record_count + 1):
        records.append(
            observation(
                observation_type="interaction.activation",
                runtime_id=runtime_id,
                document_attachment_id=attachment,
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
                target_id=target_id,
                occurred_at=None,
            )
        )
    async with exporter:
        await exporter.open_stream(resume_from=0)
        await exporter.send_batch(records, batch_id=None)

    return ObservedSession(
        preview=preview,
        activation_id=opened.activation_id,
        browser_stream_id=opened.browser_stream_id,
        trace_id=trace_id,
        target_id=target_id,
        browser_request_id=browser_request_id,
    )
