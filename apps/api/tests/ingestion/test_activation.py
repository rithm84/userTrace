"""Activation: signed attestation, pinned manifest, and one credential exchange."""

from __future__ import annotations

import secrets

import httpx

from app.db.pool import Database
from app.extension.protocol import PageProofClaims
from app.settings import Settings
from tests.conftest import require_env
from tests.ingestion.activation_client import activate_document
from tests.ingestion.factories import create_preview, front_door_credential
from tests.ingestion.keys import SigningKeys
from tests.supabase_auth import SignedInUser


def _nonce() -> str:
    """Return a per-document nonce in the exact shape the SDK mints."""
    return secrets.token_hex(24)


async def test_activation_returns_verifiable_envelopes_and_one_credential(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """The gate returns three signed envelopes and exchanges the attestation once."""
    require_env("UTRACE_SUPABASE_SECRET_KEY")
    preview = await create_preview(
        telemetry_database,
        name="activation-happy",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    nonce = _nonce()
    activated = await activate_document(
        ingest_server,
        service_token=settings.front_door_service_token,
        session_credential=front_door_credential(settings, preview),
        public_preview_host=preview.origin_host,
        document_origin=preview.document_origin,
        document_nonce=nonce,
        sdk_installation_id="shopalytics-browser-sdk",
        public_key=keys.attestation_public,
    )

    assert activated.attestation["document_nonce"] == nonce
    assert activated.attestation["origin"] == preview.document_origin
    assert activated.attestation["session_id"] == str(preview.interview_session_id)
    assert activated.attestation["client_id"] == str(preview.case.client_id)
    assert activated.attestation["preview_runtime_allocation_id"] == str(
        preview.preview_runtime_allocation_id
    )
    assert activated.attestation["protocol_version"] == "1"
    assert activated.manifest["manifest_version"] == (
        activated.attestation["annotation_manifest_version"]
    )
    assert activated.page_proof["document_nonce"] == nonce
    # The extension validates the claim set strictly before forwarding the proof,
    # and the binding verifier reads the same model back out of the signed bytes.
    claims = PageProofClaims.model_validate(activated.page_proof)
    assert claims.sdk_protocol_version == "1.0"
    assert str(claims.preview_environment_id) == activated.attestation["preview_environment_id"]
    assert activated.ingest_url.endswith("/v1/ingest/browser")
    assert activated.limits["max_observation_bytes"] == 262_144
    assert activated.limits["max_batch_bytes"] == 1_048_576

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select kind, state, document_nonce, attestation_exchanged_at
            from public.telemetry_activations
            where interview_session_id = %s
            """,
            (preview.interview_session_id,),
        )
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["kind"] == "browser"
    assert rows[0]["document_nonce"] == nonce
    assert rows[0]["attestation_exchanged_at"] is not None


async def test_attestation_cannot_be_exchanged_twice(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A second exchange of the same attestation is refused, not a refresh."""
    preview = await create_preview(
        telemetry_database,
        name="activation-replay",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    credential = front_door_credential(settings, preview)
    activated = await activate_document(
        ingest_server,
        service_token=settings.front_door_service_token,
        session_credential=credential,
        public_preview_host=preview.origin_host,
        document_origin=preview.document_origin,
        document_nonce=_nonce(),
        sdk_installation_id="shopalytics-browser-sdk",
        public_key=keys.attestation_public,
    )
    async with httpx.AsyncClient(base_url=ingest_server, timeout=30.0) as client:
        replay = await client.post(
            "/v1/preview/telemetry-credentials",
            headers={
                "authorization": f"Bearer {settings.front_door_service_token}",
                "x-utrace-session-credential": credential,
            },
            json={
                "schema_version": (
                    "utrace_preview_telemetry_credential_relay_request_v1"
                ),
                "public_preview_host": preview.origin_host,
                "credential_request": {
                    "attestation": activated.attestation_envelope,
                    "document_nonce": _nonce(),
                },
            },
        )
    assert replay.status_code == 409
    assert replay.json()["error"] == "IntegrityViolationError"


async def test_activation_refuses_a_session_credential_for_another_origin(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """The forwarded session context has to be bound to the preview origin."""
    first = await create_preview(
        telemetry_database,
        name="activation-origin-a",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    second = await create_preview(
        telemetry_database,
        name="activation-origin-b",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    async with httpx.AsyncClient(base_url=ingest_server, timeout=30.0) as client:
        response = await client.post(
            "/v1/preview/activations",
            headers={
                "authorization": f"Bearer {settings.front_door_service_token}",
                "x-utrace-session-credential": front_door_credential(settings, first),
            },
            json={
                "schema_version": "utrace_preview_activation_relay_request_v1",
                "public_preview_host": second.origin_host,
                "activation_request": {
                    "schema_version": "1",
                    "sdk_installation_id": "shopalytics-browser-sdk",
                    "environment_name": "preview",
                    "client_release_id": "shopalytics-release",
                    "protocol_version": "1",
                    "document_nonce": _nonce(),
                    "document_origin": second.document_origin,
                    "observation_schema_version": "1",
                },
            },
        )
    assert response.status_code == 409
    assert response.json()["error"] == "IntegrityViolationError"


async def test_activation_requires_the_front_door_service_token(
    telemetry_database: Database,
    settings: Settings,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A participant credential never stands in for the door's service token."""
    preview = await create_preview(
        telemetry_database,
        name="activation-unauthorized",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    async with httpx.AsyncClient(base_url=ingest_server, timeout=30.0) as client:
        response = await client.post(
            "/v1/preview/activations",
            headers={
                "authorization": "Bearer not-the-front-door",
                "x-utrace-session-credential": front_door_credential(settings, preview),
            },
            json={
                "schema_version": "utrace_preview_activation_relay_request_v1",
                "public_preview_host": preview.origin_host,
                "activation_request": {
                    "schema_version": "1",
                    "sdk_installation_id": "shopalytics-browser-sdk",
                    "environment_name": "preview",
                    "client_release_id": "shopalytics-release",
                    "protocol_version": "1",
                    "document_nonce": _nonce(),
                    "document_origin": preview.document_origin,
                    "observation_schema_version": "1",
                },
            },
        )
    assert response.status_code == 403


async def test_the_propagation_probe_echo_reports_a_single_injector(
    telemetry_database: Database,
    settings: Settings,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """The echo proves what crossed the preview boundary, and how many injectors ran."""
    preview = await create_preview(
        telemetry_database,
        name="probe-echo",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    traceparent = f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"
    async with httpx.AsyncClient(base_url=ingest_server, timeout=30.0) as client:
        headers = {
            "authorization": f"Bearer {settings.front_door_service_token}",
            "x-utrace-session-credential": front_door_credential(settings, preview),
        }
        single = await client.post(
            "/v1/preview/propagation-probes",
            headers=headers,
            json={
                "schema_version": "utrace_preview_propagation_probe_request_v1",
                "public_preview_host": preview.origin_host,
                "traceparent": traceparent,
                "tracestate": "utrace=1",
                "traceparent_count": 1,
            },
        )
        duplicated = await client.post(
            "/v1/preview/propagation-probes",
            headers=headers,
            json={
                "schema_version": "utrace_preview_propagation_probe_request_v1",
                "public_preview_host": preview.origin_host,
                "traceparent": traceparent,
                "tracestate": "utrace=1",
                "traceparent_count": 2,
            },
        )
    assert single.status_code == 200
    assert single.json()["traceparent"] == traceparent
    assert single.json()["single_injector"] is True
    assert duplicated.status_code == 200
    assert duplicated.json()["single_injector"] is False
