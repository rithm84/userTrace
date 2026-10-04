"""Drives the two relay endpoints the preview front door calls.

It sends exactly what `apps/preview-front-door` sends: the door's service token in
`Authorization`, the forwarded session context in `x-utrace-session-credential`,
and the relay body. It then verifies the returned attestation with the packaged
public key, exactly as the embedded browser SDK's activation gate does.
"""

from __future__ import annotations

from base64 import urlsafe_b64decode
from dataclasses import dataclass
from hashlib import sha256
from json import loads
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

__all__ = ["ActivatedDocument", "activate_document", "verify_envelope"]


@dataclass(frozen=True, slots=True)
class ActivatedDocument:
    """One activated document: its verified claims, credential, and limits."""

    attestation_envelope: dict[str, Any]
    attestation: dict[str, Any]
    manifest: dict[str, Any]
    page_proof: dict[str, Any]
    token: str
    ingest_url: str
    limits: dict[str, Any]


def _b64url(value: str) -> bytes:
    """Decode unpadded base64url."""
    return urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_envelope(envelope: dict[str, Any], public_key: Ed25519PublicKey) -> dict[str, Any]:
    """Verify one detached envelope the way the SDK does and return its payload."""
    payload = _b64url(str(envelope["payload_b64url"]))
    public_key.verify(_b64url(str(envelope["signature_b64url"])), payload)
    decoded = loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise TypeError("a signed envelope carried a payload that is not an object")
    return decoded


def payload_digest(envelope: dict[str, Any]) -> str:
    """Return the digest of one envelope's signed payload bytes."""
    return f"sha256:{sha256(_b64url(str(envelope['payload_b64url']))).hexdigest()}"


async def activate_document(
    base_url: str,
    *,
    service_token: str,
    session_credential: str,
    public_preview_host: str,
    document_origin: str,
    document_nonce: str,
    sdk_installation_id: str,
    public_key: Ed25519PublicKey,
) -> ActivatedDocument:
    """Run the whole activation gate: attestation, checks, and the one exchange."""
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        headers = {
            "authorization": f"Bearer {service_token}",
            "x-utrace-session-credential": session_credential,
        }
        activation = await client.post(
            "/v1/preview/activations",
            headers=headers,
            json={
                "schema_version": "utrace_preview_activation_relay_request_v1",
                "public_preview_host": public_preview_host,
                "activation_request": {
                    "schema_version": "1",
                    "sdk_installation_id": sdk_installation_id,
                    "environment_name": "preview",
                    "client_release_id": "shopalytics-release",
                    "protocol_version": "1",
                    "document_nonce": document_nonce,
                    "document_origin": document_origin,
                    "observation_schema_version": "1",
                },
            },
        )
        activation.raise_for_status()
        body = activation.json()
        attestation = verify_envelope(body["attestation"], public_key)
        manifest = verify_envelope(body["annotation_manifest"], public_key)
        page_proof = verify_envelope(body["page_proof"], public_key)
        if attestation["annotation_manifest_digest"] != payload_digest(
            body["annotation_manifest"]
        ):
            raise AssertionError("the attestation does not pin the manifest it returned")

        credential = await client.post(
            "/v1/preview/telemetry-credentials",
            headers=headers,
            json={
                "schema_version": (
                    "utrace_preview_telemetry_credential_relay_request_v1"
                ),
                "public_preview_host": public_preview_host,
                "credential_request": {
                    "attestation": body["attestation"],
                    "document_nonce": document_nonce,
                },
            },
        )
        credential.raise_for_status()
        issued = credential.json()
    return ActivatedDocument(
        attestation_envelope=body["attestation"],
        attestation=attestation,
        manifest=manifest,
        page_proof=page_proof,
        token=issued["credential"]["token"],
        ingest_url=issued["credential"]["ingest_url"],
        limits=issued["limits"],
    )
