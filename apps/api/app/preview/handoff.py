"""Authentication handoffs: into the front door, and into the client product.

Two different one-time handoffs meet here.

The **front-door claim** is what the session tab requests. The code is opaque
random bytes, not a signed credential: it carries no claims at all, and every
fact the exchange needs is in the durable row recorded beside its digest. That
keeps it inside the front door's published code length and makes single use a
property of the database rather than of a token, so a replay loses across
processes. Exchanging it returns the origin-bound session credential the door
stores in its ``__Host-`` cookie.

The **product handoff** is what signs the originating user into the client
product. It is minted with the secret injected into that one runtime at sandbox
creation, is audience-bound to the session origin, lives at most two minutes, and
the runtime enforces single use itself. uTrace never holds a product password.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.auth.lane import Lane, LaneClaims, mint_lane_credential, verify_lane_credential
from app.db.pool import Database
from app.db.rows import as_str, as_uuid
from app.errors import LaneCredentialError
from app.ids import uuid7
from app.logging import get_logger

__all__ = [
    "PRODUCT_HANDOFF_MAX_LIFETIME_SECONDS",
    "ExchangedHandoff",
    "FrontDoorClaim",
    "ProductHandoffToken",
    "claim_url",
    "consume_front_door_code",
    "mint_front_door_claim",
    "mint_product_handoff",
    "verify_session_credential",
]

_log = get_logger(__name__)

PRODUCT_HANDOFF_SCHEMA_VERSION = "shopalytics-preview-handoff-v1"
PRODUCT_HANDOFF_TOKEN_VERSION = "v1"
PRODUCT_HANDOFF_MAX_LIFETIME_SECONDS = 120

# 32 random bytes render as 43 base64url characters, inside the front door's
# published claim-code length and far beyond guessing.
_CLAIM_CODE_BYTES = 32


@dataclass(frozen=True, slots=True)
class ProductHandoffToken:
    """A one-time token that signs the originating user into the preview product."""

    token: str
    token_id: UUID
    audience: str
    subject: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class FrontDoorClaim:
    """The one-time claim the session tab hands to the participant."""

    url: str
    code: str
    expires_at: datetime
    preview_route_id: UUID


@dataclass(frozen=True, slots=True)
class ExchangedHandoff:
    """What the front door receives when it exchanges a claim code."""

    client_id: UUID
    interview_session_id: UUID
    public_preview_host: str
    session_credential: str
    session_credential_expires_at: datetime


def _base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def mint_product_handoff(
    *,
    handoff_secret: str,
    audience_host: str,
    subject_email: str,
    lifetime_seconds: int,
) -> ProductHandoffToken:
    """Mint the token the preview runtime exchanges for a product session.

    The envelope is ``v1.<base64url(claims)>.<base64url(HMAC-SHA256(payload))>``
    and the signature covers the encoded payload bytes exactly as transmitted, so
    verification never re-serializes JSON. The runtime refuses a lifetime longer
    than two minutes, so this refuses it first rather than minting authority the
    product will reject.
    """
    if lifetime_seconds <= 0 or lifetime_seconds > PRODUCT_HANDOFF_MAX_LIFETIME_SECONDS:
        raise ValueError(
            "a preview product handoff lives between 1 and "
            f"{PRODUCT_HANDOFF_MAX_LIFETIME_SECONDS} seconds, not {lifetime_seconds}"
        )
    issued_at = datetime.now(tz=UTC)
    expires_at = issued_at + timedelta(seconds=lifetime_seconds)
    token_id = uuid7()
    claims = {
        "schema_version": PRODUCT_HANDOFF_SCHEMA_VERSION,
        "token_id": str(token_id),
        "subject_email": subject_email,
        "audience": audience_host,
        "issued_at": issued_at.isoformat(timespec="milliseconds"),
        "expires_at": expires_at.isoformat(timespec="milliseconds"),
    }
    payload = _base64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signature = _base64url(
        hmac.new(
            handoff_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).digest()
    )
    return ProductHandoffToken(
        token=f"{PRODUCT_HANDOFF_TOKEN_VERSION}.{payload}.{signature}",
        token_id=token_id,
        audience=audience_host,
        subject=subject_email,
        expires_at=expires_at,
    )


def claim_url(*, public_preview_host: str, code: str) -> str:
    """Return the front-door claim URL for one one-time code."""
    return f"https://{public_preview_host}/__utrace/claim?code={code}"


def _code_digest(code: str) -> str:
    """Return the digest recorded for a claim code. The code itself is never stored."""
    return f"sha256:{hashlib.sha256(code.encode('utf-8')).hexdigest()}"


async def mint_front_door_claim(
    database: Database,
    *,
    client_id: UUID,
    interview_session_id: UUID,
    preview_route_id: UUID,
    public_preview_host: str,
    lifetime_seconds: int,
) -> FrontDoorClaim:
    """Mint and record one one-time front-door claim for a session's current route.

    The code is opaque: it proves nothing by itself and only the recorded row
    grants it authority, for one origin, once, until it expires.
    """
    code = secrets.token_urlsafe(_CLAIM_CODE_BYTES)
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=lifetime_seconds)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.preview_handoff_codes (
                code_digest, client_id, interview_session_id, preview_route_id,
                public_preview_host, expires_at
            )
            values (%s, %s, %s, %s, %s, %s)
            """,
            (
                _code_digest(code),
                client_id,
                interview_session_id,
                preview_route_id,
                public_preview_host,
                expires_at,
            ),
        )
    _log.info(
        "preview_front_door_claim_minted",
        client_id=str(client_id),
        interview_session_id=str(interview_session_id),
        preview_route_id=str(preview_route_id),
    )
    return FrontDoorClaim(
        url=claim_url(public_preview_host=public_preview_host, code=code),
        code=code,
        expires_at=expires_at,
        preview_route_id=preview_route_id,
    )


async def consume_front_door_code(
    database: Database,
    *,
    signing_key: str,
    code: str,
    public_preview_host: str,
    session_credential_lifetime_seconds: int,
) -> ExchangedHandoff:
    """Exchange one claim code for the door's origin-bound session credential.

    Single use is enforced by the recorded digest: the consuming update matches
    only an unconsumed, unexpired row for this exact origin, so a replay and a
    code presented on another origin both lose, and they lose across processes
    rather than within one.
    """
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_handoff_codes
            set consumed_at = now()
            where code_digest = %s
              and consumed_at is null
              and expires_at > now()
              and public_preview_host = %s
            returning client_id, interview_session_id, public_preview_host
            """,
            (_code_digest(code), public_preview_host),
        )
        row = await cur.fetchone()
    if row is None:
        raise LaneCredentialError(
            audience=Lane.FRONT_DOOR.value,
            reason="the handoff code is unknown, already consumed, or expired",
        )

    session_credential = mint_lane_credential(
        signing_key=signing_key,
        lane=Lane.FRONT_DOOR,
        subject_id=as_uuid(row, "interview_session_id"),
        client_id=as_uuid(row, "client_id"),
        interview_session_id=as_uuid(row, "interview_session_id"),
        binding=public_preview_host,
        lifetime_seconds=session_credential_lifetime_seconds,
    )
    _log.info(
        "preview_front_door_claim_consumed",
        client_id=str(as_uuid(row, "client_id")),
        interview_session_id=str(as_uuid(row, "interview_session_id")),
    )
    return ExchangedHandoff(
        client_id=as_uuid(row, "client_id"),
        interview_session_id=as_uuid(row, "interview_session_id"),
        public_preview_host=as_str(row, "public_preview_host"),
        session_credential=session_credential.token,
        session_credential_expires_at=session_credential.expires_at,
    )


def verify_session_credential(
    *, signing_key: str, credential: str, public_preview_host: str
) -> LaneClaims:
    """Verify the door's origin-bound session credential for one preview origin.

    A claim code is not a session credential: its subject is the route it opens,
    not the Interview Session, so presenting one here is refused.
    """
    claims = verify_lane_credential(
        signing_key=signing_key, token=credential, lane=Lane.FRONT_DOOR
    )
    if claims.binding != public_preview_host:
        raise LaneCredentialError(
            audience=Lane.FRONT_DOOR.value,
            reason="the session credential is bound to a different preview origin",
        )
    if (
        claims.interview_session_id is None
        or claims.subject_id != claims.interview_session_id
    ):
        raise LaneCredentialError(
            audience=Lane.FRONT_DOOR.value,
            reason="this credential is a one-time claim code, not a session credential",
        )
    return claims
