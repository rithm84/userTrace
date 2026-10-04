"""The device key an installed extension holds.

The extension keeps a non-exportable ECDSA P-256 private key in its service
worker and signs challenges with WebCrypto, which emits the IEEE P1363
fixed-width signature form. That is reproduced here with a real key so the
control plane's verification runs unmodified.

Page proofs are not signed here: the control plane itself issues them at
document activation, so a test signs one with the process's own attestation
signer rather than with a key of its own.
"""

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from app.ids import uuid7

__all__ = ["DeviceKey", "base64url"]

_COORDINATE_BYTES = 32


def base64url(raw: bytes) -> str:
    """Render bytes as the unpadded base64url the protocol uses."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@dataclass(frozen=True, slots=True)
class DeviceKey:
    """One installation's device key pair, as the service worker holds it."""

    private_key: ec.EllipticCurvePrivateKey

    @classmethod
    def generate(cls) -> "DeviceKey":
        """Generate a fresh non-exportable-equivalent P-256 key pair."""
        return cls(private_key=ec.generate_private_key(ec.SECP256R1()))

    @property
    def public_key_spki_b64u(self) -> str:
        """The SPKI public half the control plane registers."""
        spki = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return base64url(spki)

    def prove(
        self,
        *,
        audience: str,
        purpose: str,
        installation_id: UUID | None,
        issued_at: datetime | None = None,
    ) -> tuple[str, str]:
        """Sign one challenge, returning its base64url challenge and signature."""
        challenge: dict[str, object] = {
            "audience": audience,
            "purpose": purpose,
            "jti": str(uuid7()),
            "issued_at": (issued_at or datetime.now(tz=UTC))
            .isoformat()
            .replace("+00:00", "Z"),
        }
        if installation_id is not None:
            challenge["installation_id"] = str(installation_id)
        encoded = json.dumps(challenge).encode("utf-8")
        der = self.private_key.sign(encoded, ec.ECDSA(hashes.SHA256()))
        r, s = utils.decode_dss_signature(der)
        raw = r.to_bytes(_COORDINATE_BYTES, "big") + s.to_bytes(_COORDINATE_BYTES, "big")
        return base64url(encoded), base64url(raw)

    def forwarded_preflight_proof(self, *, audience: str, installation_id: UUID) -> str:
        """The session-preflight proof the extension hands the session tab to forward."""
        challenge_b64u, signature_b64u = self.prove(
            audience=audience, purpose="session_preflight", installation_id=installation_id
        )
        proof = {
            "algorithm": "ES256",
            "challenge_b64u": challenge_b64u,
            "signature_b64u": signature_b64u,
        }
        return base64url(json.dumps(proof).encode("utf-8"))
