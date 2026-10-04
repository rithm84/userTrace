"""Proof of possession of an installation's device key.

The extension holds a non-exportable ECDSA P-256 private key and never a
reusable bearer credential. To obtain authority it signs a challenge it mints
itself, in the style of a DPoP proof, and the control plane enforces freshness
and single use.

A browser WebSocket cannot set headers, so the control socket carries the proof
in its subprotocol tokens. Verification is identical either way: the same
canonical challenge bytes, the same key, the same freshness and replay rules.

WebCrypto emits an ECDSA signature in the IEEE P1363 fixed-width form, so the
raw pair is converted to the DER encoding the verifier expects. A signature that
is neither is refused rather than reinterpreted.
"""

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from pydantic import BaseModel, ConfigDict, ValidationError

__all__ = [
    "PossessionChallenge",
    "PossessionError",
    "PossessionPurpose",
    "claimed_installation_id",
    "decode_forwarded_proof",
    "decode_subprotocol_proof",
    "verify_possession",
]

_P256_SIGNATURE_BYTES = 64
_COORDINATE_BYTES = 32
_SUBPROTOCOL_PREFIX = "utrace.pop."

type PossessionPurpose = Literal[
    "installation_registration",
    "control_socket",
    "installation_revocation",
    "session_preflight",
]


class PossessionError(ValueError):
    """A proof of possession that cannot authenticate this request."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


class PossessionChallenge(BaseModel):
    """The claims the extension signed. The control plane checks every one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audience: str
    purpose: PossessionPurpose
    jti: UUID
    issued_at: datetime
    installation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class _RawProof:
    """The two base64url strings a proof is carried as."""

    challenge_b64u: str
    signature_b64u: str


def decode_subprotocol_proof(subprotocols: tuple[str, ...]) -> _RawProof:
    """Read the proof out of the control socket's subprotocol tokens.

    The extension sends `utrace.pop.<challenge>.<signature>`; base64url contains
    no dot, so the two parts split unambiguously.
    """
    for token in subprotocols:
        if not token.startswith(_SUBPROTOCOL_PREFIX):
            continue
        remainder = token.removeprefix(_SUBPROTOCOL_PREFIX)
        challenge, separator, signature = remainder.partition(".")
        if not separator or not challenge or not signature:
            raise PossessionError(
                "malformed_possession_subprotocol",
                "the proof token is not utrace.pop.<challenge>.<signature>",
            )
        return _RawProof(challenge_b64u=challenge, signature_b64u=signature)
    raise PossessionError(
        "missing_possession_subprotocol",
        "the connection carried no utrace.pop.<challenge>.<signature> token",
    )


class _ForwardedProof(BaseModel):
    """The proof the extension hands the session tab to forward."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    algorithm: Literal["ES256"]
    challenge_b64u: str
    signature_b64u: str


def decode_forwarded_proof(encoded: str) -> _RawProof:
    """Read a proof the session tab forwarded: `base64url(JSON(proof_of_possession))`."""
    try:
        proof = _ForwardedProof.model_validate_json(_b64u_decode(encoded, field="proof"))
    except ValidationError as error:
        raise PossessionError(
            "malformed_proof",
            f"the forwarded proof failed validation with {error.error_count()} errors",
        ) from error
    return _RawProof(challenge_b64u=proof.challenge_b64u, signature_b64u=proof.signature_b64u)


def claimed_installation_id(challenge_b64u: str) -> UUID:
    """Return the installation a challenge names, before its signature is checked.

    The claim only selects which registered key the proof must verify against;
    `verify_possession` then refuses it unless that key signed it.
    """
    installation_id = _decode_challenge(
        _b64u_decode(challenge_b64u, field="challenge")
    ).installation_id
    if installation_id is None:
        raise PossessionError("malformed_challenge", "the challenge names no installation")
    return installation_id


def verify_possession(
    *,
    device_public_key_spki_b64u: str,
    challenge_b64u: str,
    signature_b64u: str,
    expected_audience: str,
    expected_purpose: PossessionPurpose,
    expected_installation_id: UUID | None,
    now: datetime,
    max_age_seconds: int,
) -> PossessionChallenge:
    """Verify one proof against the installation's registered public key.

    Returns the decoded challenge so the caller can record its ``jti`` as spent.
    Single use is durable, not a property of this function: recording the jti is
    what stops a replay.
    """
    challenge_bytes = _b64u_decode(challenge_b64u, field="challenge")
    signature_bytes = _b64u_decode(signature_b64u, field="signature")
    public_key = _load_public_key(device_public_key_spki_b64u)
    challenge = _decode_challenge(challenge_bytes)

    if challenge.audience != expected_audience:
        raise PossessionError(
            "audience_mismatch",
            f"the challenge names audience {challenge.audience!r}",
        )
    if challenge.purpose != expected_purpose:
        raise PossessionError(
            "purpose_mismatch", f"the challenge names purpose {challenge.purpose!r}"
        )
    if expected_installation_id is not None and (
        challenge.installation_id != expected_installation_id
    ):
        raise PossessionError(
            "installation_mismatch",
            "the challenge names a different installation than the request",
        )
    _require_fresh(challenge.issued_at, now=now, max_age_seconds=max_age_seconds)

    try:
        public_key.verify(
            _to_der(signature_bytes),
            challenge_bytes,
            ec.ECDSA(hashes.SHA256()),
        )
    except InvalidSignature as error:
        raise PossessionError(
            "invalid_signature",
            "the signature does not verify against the registered device key",
        ) from error
    return challenge


def _decode_challenge(challenge_bytes: bytes) -> PossessionChallenge:
    """Parse the signed challenge bytes into their claims."""
    try:
        payload = json.loads(challenge_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PossessionError("malformed_challenge", "the challenge is not UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise PossessionError("malformed_challenge", "the challenge is not an object")
    try:
        return PossessionChallenge.model_validate(payload)
    except ValidationError as error:
        raise PossessionError(
            "malformed_challenge",
            f"the challenge failed validation with {error.error_count()} errors",
        ) from error


def _require_fresh(
    issued_at: datetime, *, now: datetime, max_age_seconds: int
) -> None:
    """Refuse a challenge that is too old or dated in the future."""
    if issued_at.tzinfo is None:
        raise PossessionError(
            "malformed_challenge", "issued_at carries no timezone offset"
        )
    age = now - issued_at
    if age > timedelta(seconds=max_age_seconds):
        raise PossessionError(
            "stale_challenge",
            f"the challenge is older than {max_age_seconds} seconds",
        )
    if age < -timedelta(seconds=max_age_seconds):
        raise PossessionError(
            "future_challenge", "the challenge is dated further ahead than the allowance"
        )


def _load_public_key(spki_b64u: str) -> ec.EllipticCurvePublicKey:
    """Load the registered SPKI public key, refusing anything but P-256."""
    der = _b64u_decode(spki_b64u, field="device_public_key")
    try:
        key = serialization.load_der_public_key(der)
    except (ValueError, UnsupportedAlgorithm) as error:
        raise PossessionError(
            "unusable_device_key", "the registered device key is not a DER SPKI key"
        ) from error
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise PossessionError(
            "unusable_device_key", "the registered device key is not an ECDSA P-256 key"
        )
    return key


def _to_der(signature: bytes) -> bytes:
    """Convert a WebCrypto P1363 signature to the DER form the verifier takes."""
    if len(signature) != _P256_SIGNATURE_BYTES:
        raise PossessionError(
            "malformed_signature",
            f"an ES256 signature is {_P256_SIGNATURE_BYTES} bytes, got {len(signature)}",
        )
    r = int.from_bytes(signature[:_COORDINATE_BYTES], "big")
    s = int.from_bytes(signature[_COORDINATE_BYTES:], "big")
    return utils.encode_dss_signature(r, s)


def _b64u_decode(value: str, *, field: str) -> bytes:
    """Decode unpadded base64url, refusing anything that is not."""
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError) as error:
        raise PossessionError(
            "malformed_base64url", f"{field} is not unpadded base64url"
        ) from error


def utc_now() -> datetime:
    """The clock every possession check reads, in one place."""
    return datetime.now(tz=UTC)
