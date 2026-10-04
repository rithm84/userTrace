"""Ed25519 signing and verification for the activation path.

The document attestation, annotation manifest, and extension page proof are
detached Ed25519 signatures over base64url JSON payload bytes, exactly the
``signed_envelope`` shape in ``browser-observation/common.v1``. The browser SDK
verifies them with a packaged public key, so the payload bytes that are signed
are the bytes the SDK hashes: the manifest digest the attestation pins is the
SHA-256 of the decoded payload bytes of the manifest envelope.

Signed server instrumentation manifests use RFC 8785 canonical JSON instead,
because the server SDK verifies a canonicalized object rather than an opaque
payload string.
"""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Mapping
from hashlib import sha256
from json import dumps

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import BaseModel, ConfigDict, Field

from app.envelope import JsonValue
from app.ingestion.errors import SourceValidationError

__all__ = [
    "SignedEnvelope",
    "Signer",
    "b64url_decode",
    "b64url_encode",
    "payload_digest",
    "public_key_b64url",
    "verify_canonical_signature",
]

_SIGNATURE_B64URL_LENGTH = 86


class SignedEnvelope(BaseModel):
    """A detached Ed25519 signature over base64url payload bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    algorithm: str = Field(pattern="^ed25519$")
    key_id: str = Field(min_length=1, max_length=128)
    payload_b64url: str = Field(pattern="^[A-Za-z0-9_-]+$")
    signature_b64url: str = Field(
        min_length=_SIGNATURE_B64URL_LENGTH, max_length=_SIGNATURE_B64URL_LENGTH
    )


def b64url_encode(raw: bytes) -> str:
    """Encode bytes as unpadded base64url."""
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(value: str, field: str) -> bytes:
    """Decode unpadded base64url, refusing anything that is not decodable."""
    padded = value + "=" * (-len(value) % 4)
    try:
        return urlsafe_b64decode(padded)
    except ValueError:
        raise SourceValidationError(field=field, reason="is not base64url") from None


def payload_digest(envelope: SignedEnvelope) -> str:
    """Return the ``sha256:<hex>`` digest of one envelope's signed payload bytes."""
    raw = b64url_decode(envelope.payload_b64url, "payload_b64url")
    return f"sha256:{sha256(raw).hexdigest()}"


class Signer:
    """Holds the uTrace private signing key for one process.

    The key never leaves this object: it is constructed from the configured seed,
    used to sign, and never serialized, logged, or returned.
    """

    def __init__(self, seed: bytes, key_id: str) -> None:
        self._key = Ed25519PrivateKey.from_private_bytes(seed)
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        """The key identifier the SDK matches against its packaged keys."""
        return self._key_id

    def public_key_b64url(self) -> str:
        """The raw public half, base64url, for the SDK's packaged key list."""
        return public_key_b64url(self._key.public_key())

    def sign(self, payload: Mapping[str, JsonValue]) -> SignedEnvelope:
        """Sign one JSON payload and return its detached envelope.

        The payload is serialized once, here, and the exact bytes that are signed
        are the bytes carried in ``payload_b64url``. No consumer re-serializes it.
        """
        raw = dumps(
            payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        return SignedEnvelope(
            algorithm="ed25519",
            key_id=self._key_id,
            payload_b64url=b64url_encode(raw),
            signature_b64url=b64url_encode(self._key.sign(raw)),
        )

    def verify(self, envelope: SignedEnvelope) -> bytes:
        """Verify an envelope this process signed and return its payload bytes."""
        if envelope.key_id != self._key_id:
            raise SourceValidationError(
                field="key_id", reason="names a key this control plane does not hold"
            )
        raw = b64url_decode(envelope.payload_b64url, "payload_b64url")
        signature = b64url_decode(envelope.signature_b64url, "signature_b64url")
        try:
            self._key.public_key().verify(signature, raw)
        except InvalidSignature:
            raise SourceValidationError(
                field="signature_b64url", reason="does not verify"
            ) from None
        return raw


def public_key_b64url(key: Ed25519PublicKey) -> str:
    """Return one Ed25519 public key as unpadded base64url raw bytes."""
    return b64url_encode(key.public_bytes(Encoding.Raw, PublicFormat.Raw))


def verify_canonical_signature(
    *,
    payload: Mapping[str, JsonValue],
    signature: bytes,
    public_key: bytes,
    field: str,
) -> None:
    """Verify an Ed25519 signature over the RFC 8785 canonical form of a payload.

    The signed server instrumentation manifest uses this form, so the control
    plane and the server SDK agree byte for byte on what was signed.
    """
    canonical = rfc8785.dumps(payload)
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, canonical)
    except InvalidSignature:
        raise SourceValidationError(field=field, reason="does not verify") from None
