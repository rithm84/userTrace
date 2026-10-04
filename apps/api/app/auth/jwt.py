"""Supabase Auth access-token verification.

The API verifies the issuer, audience, signature against the project's published
JWKS, subject, and expiry before any authorization runs. Only asymmetric
algorithms are accepted, so a published verification key can never be replayed as
an HMAC secret. Verification failure is a refusal, never a downgrade.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import jwt
from jwt import PyJWKClient

from app.errors import AuthorizationError
from app.logging import get_logger
from app.settings import Settings

__all__ = ["SupabaseJwtVerifier", "VerifiedIdentity"]

_log = get_logger(__name__)

_ASYMMETRIC_ALGORITHMS = frozenset({"ES256", "ES384", "ES512", "RS256", "RS384", "RS512", "EdDSA"})
_REQUIRED_CLAIMS = ("exp", "iat", "sub", "aud", "iss")


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """A proven Supabase Auth identity. It grants no tenant authority by itself."""

    auth_user_id: UUID
    session_id: str | None
    expires_at: datetime


class SupabaseJwtVerifier:
    """Connector to the project's JWKS endpoint with the verification policy."""

    def __init__(self, settings: Settings, jwks_lifespan_seconds: int) -> None:
        self._issuer = settings.supabase_jwt_issuer
        self._audience = settings.supabase_jwt_audience
        self._client = PyJWKClient(
            uri=settings.supabase_jwks_url,
            cache_keys=True,
            cache_jwk_set=True,
            lifespan=jwks_lifespan_seconds,
        )

    async def verify(self, token: str) -> VerifiedIdentity:
        """Verify one access token and return the proven identity."""
        signing_key = await asyncio.to_thread(self._signing_key, token)
        algorithm = signing_key.algorithm_name
        if algorithm is None or algorithm not in _ASYMMETRIC_ALGORITHMS:
            raise AuthorizationError(
                subject="supabase_access_token",
                reason=f"signing algorithm '{algorithm}' is not an accepted asymmetric algorithm",
            )
        try:
            claims = jwt.decode(
                token,
                key=signing_key.key,
                algorithms=[algorithm],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": list(_REQUIRED_CLAIMS), "verify_aud": True},
            )
        except jwt.InvalidTokenError as error:
            raise AuthorizationError(
                subject="supabase_access_token", reason=str(error)
            ) from error

        subject = claims.get("sub")
        if not isinstance(subject, str):
            raise AuthorizationError(
                subject="supabase_access_token", reason="the subject claim is missing"
            )
        try:
            auth_user_id = UUID(subject)
        except ValueError as error:
            raise AuthorizationError(
                subject="supabase_access_token",
                reason="the subject claim is not a Supabase user identifier",
            ) from error

        session_id = claims.get("session_id")
        return VerifiedIdentity(
            auth_user_id=auth_user_id,
            session_id=session_id if isinstance(session_id, str) else None,
            expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
        )

    def _signing_key(self, token: str) -> jwt.PyJWK:
        try:
            return self._client.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientError as error:
            raise AuthorizationError(
                subject="supabase_access_token",
                reason=f"no published signing key matches this token: {error}",
            ) from error
        except jwt.DecodeError as error:
            raise AuthorizationError(
                subject="supabase_access_token", reason=f"malformed token: {error}"
            ) from error
