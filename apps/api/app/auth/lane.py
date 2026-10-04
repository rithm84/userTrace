"""Audience-bound short-lived lane credentials.

Two lanes mint a credential of this kind: the Server SDK bootstrap the preview
stream injects into a sandbox, and the preview front door's origin-bound session
credential. A credential for one lane cannot acknowledge commands or write
evidence in the other: the audience is part of the signature, and every check
compares it to the lane that is being entered.

The other session credential lanes authenticate differently and mint nothing
here. The session tab and every session route authorize the participant's own
Supabase identity, the extension proves possession of its bound key, and browser
telemetry presents the ingestion credential it was issued.

Revocation is durable, not a token property. A credential is refused once its
bound Interview Session is closed, so ending a session stops refresh and queued
effects without waiting for the token to expire.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

import jwt
from utrace_contracts.domain.lifecycle import InterviewSessionState

from app.db.pool import Database
from app.db.rows import as_str, as_uuid
from app.errors import LaneCredentialError
from app.ids import uuid7
from app.logging import get_logger

__all__ = [
    "Lane",
    "LaneClaims",
    "LaneCredential",
    "check_lane_revocation",
    "mint_lane_credential",
    "verify_lane_credential",
]

_log = get_logger(__name__)

_ISSUER = "https://utrace.dev/control-plane"
_ALGORITHM = "HS256"


class Lane(StrEnum):
    """The credential lanes this module mints and verifies."""

    SERVER_SDK = "utrace:lane:server_sdk"
    FRONT_DOOR = "utrace:lane:front_door"


@dataclass(frozen=True, slots=True)
class LaneClaims:
    """The verified contents of one lane credential."""

    lane: Lane
    subject_id: UUID
    client_id: UUID
    interview_session_id: UUID | None
    binding: str | None
    token_id: UUID
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class LaneCredential:
    """A minted credential and the moment it stops being usable."""

    token: str
    expires_at: datetime


def mint_lane_credential(
    *,
    signing_key: str,
    lane: Lane,
    subject_id: UUID,
    client_id: UUID,
    interview_session_id: UUID | None,
    binding: str | None,
    lifetime_seconds: int,
) -> LaneCredential:
    """Mint one short-lived credential bound to a single lane."""
    issued_at = datetime.now(tz=UTC)
    expires_at = issued_at + timedelta(seconds=lifetime_seconds)
    token = jwt.encode(
        {
            "iss": _ISSUER,
            "aud": lane.value,
            "sub": str(subject_id),
            "client_id": str(client_id),
            "interview_session_id": (
                str(interview_session_id) if interview_session_id is not None else None
            ),
            "binding": binding,
            "jti": str(uuid7()),
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
        },
        key=signing_key,
        algorithm=_ALGORITHM,
    )
    return LaneCredential(token=token, expires_at=expires_at)


def verify_lane_credential(*, signing_key: str, token: str, lane: Lane) -> LaneClaims:
    """Verify one credential against exactly one expected lane."""
    try:
        claims = jwt.decode(
            token,
            key=signing_key,
            algorithms=[_ALGORITHM],
            audience=lane.value,
            issuer=_ISSUER,
            options={"require": ["exp", "iat", "sub", "aud", "iss", "jti"]},
        )
    except jwt.InvalidTokenError as error:
        raise LaneCredentialError(audience=lane.value, reason=str(error)) from error

    session_id = claims.get("interview_session_id")
    binding = claims.get("binding")
    return LaneClaims(
        lane=lane,
        subject_id=UUID(str(claims["sub"])),
        client_id=UUID(str(claims["client_id"])),
        interview_session_id=UUID(str(session_id)) if session_id is not None else None,
        binding=binding if isinstance(binding, str) else None,
        token_id=UUID(str(claims["jti"])),
        expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
    )


async def check_lane_revocation(database: Database, claims: LaneClaims) -> None:
    """Refuse a credential whose bound Interview Session is no longer eligible."""
    if claims.interview_session_id is None:
        return
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state, client_id from public.interview_sessions where id = %s",
            (claims.interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise LaneCredentialError(
            audience=claims.lane.value,
            reason="the bound Interview Session no longer exists",
        )
    if as_uuid(row, "client_id") != claims.client_id:
        raise LaneCredentialError(
            audience=claims.lane.value,
            reason="the credential's client scope does not match the bound session",
        )
    if as_str(row, "state") == InterviewSessionState.CLOSED.value:
        raise LaneCredentialError(
            audience=claims.lane.value,
            reason="the bound Interview Session is closed, so refresh is revoked",
        )
