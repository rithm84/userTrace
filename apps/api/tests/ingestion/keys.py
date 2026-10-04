"""Signing keys for a test run, in the environment before settings are read.

The manifest key pair is generated per run: uTrace holds the release key, so a
manifest signed in a test is usable only by a preview whose environment names
the run's key as its trust anchor.

The attestation key is different. The embedded browser SDK ships only public
keys and refuses an attestation that names one it does not package, so a real
browser can only verify an attestation signed with the key the repository
configures. When ``UTRACE_ATTESTATION_SIGNING_KEY`` is set the run uses it and
verifies against its own public half; only without one does it generate a pair,
which no packaged SDK trusts.
"""

import os
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

__all__ = ["SigningKeys", "install_test_keys"]


@dataclass(frozen=True, slots=True)
class SigningKeys:
    """The two key pairs the ingestion tests need."""

    attestation_key_id: str
    attestation_public: Ed25519PublicKey
    manifest_key_id: str
    manifest_private: Ed25519PrivateKey


def _b64url(raw: bytes) -> str:
    """Encode raw key bytes as unpadded base64url."""
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def install_test_keys() -> SigningKeys:
    """Install the run's keys and export them the way deployment would."""
    attestation, attestation_key_id = _attestation_key()
    manifest = Ed25519PrivateKey.generate()
    os.environ["UTRACE_ATTESTATION_SIGNING_KEY"] = _b64url(
        attestation.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    )
    os.environ["UTRACE_ATTESTATION_SIGNING_KEY_ID"] = attestation_key_id
    os.environ["UTRACE_SERVER_MANIFEST_TRUST_ANCHOR"] = _b64url(
        manifest.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    os.environ["UTRACE_SERVER_MANIFEST_TRUST_ANCHOR_KEY_ID"] = "utrace-manifest-test"
    return SigningKeys(
        attestation_key_id=attestation_key_id,
        attestation_public=attestation.public_key(),
        manifest_key_id="utrace-manifest-test",
        manifest_private=manifest,
    )


def _attestation_key() -> tuple[Ed25519PrivateKey, str]:
    """Return the configured attestation key, or a generated one with its id."""
    configured = os.environ.get("UTRACE_ATTESTATION_SIGNING_KEY", "")
    key_id = os.environ.get("UTRACE_ATTESTATION_SIGNING_KEY_ID", "")
    if not configured or not key_id:
        return Ed25519PrivateKey.generate(), "utrace-browser-test"
    padded = configured + "=" * (-len(configured) % 4)
    return (
        Ed25519PrivateKey.from_private_bytes(urlsafe_b64decode(padded)),
        key_id,
    )
