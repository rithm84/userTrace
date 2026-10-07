"""Short-lived, write-only telemetry credentials.

A credential is minted for exactly one observation activation. It cannot read
telemetry, resolve artifact references, call control-plane APIs, or authorize
product actions: it only names an activation row, and every edge resolves the
claims it presents against that row.

Only the credential's digest is stored, so a database read never yields a usable
credential. Revocation is durable rather than a token property, so withdrawing
authority stops new writes immediately instead of waiting for expiry.

Contract: docs/arch-components/telemetry-and-evidence.md#authentication-quotas-and-payload-limits
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.db.pool import Database
from app.db.rows import as_datetime, as_str, as_uuid
from app.ids import uuid7
from app.ingestion.errors import TelemetryAuthenticationError
from app.logging import get_logger

__all__ = [
    "BROWSER_CREDENTIAL_LIFETIME_SECONDS",
    "SERVER_CREDENTIAL_LIFETIME_SECONDS",
    "IssuedCredential",
    "ResolvedCredential",
    "issue_credential",
    "resolve_credential",
]

_log = get_logger(__name__)

#: The browser refreshes through a fresh attestation, so its credential is short.
BROWSER_CREDENTIAL_LIFETIME_SECONDS = 900
#: The server SDK rotates on its control connection without re-activating.
SERVER_CREDENTIAL_LIFETIME_SECONDS = 1_800

#: 32 bytes of entropy, rendered so the value is a legal WebSocket subprotocol
#: token as well as a legal HTTP bearer token.
_TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class IssuedCredential:
    """A freshly minted credential. The plaintext exists only in this object."""

    telemetry_credential_id: UUID
    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    """An accepted credential and the activation its claims resolve against."""

    telemetry_credential_id: UUID
    activation_id: UUID
    client_id: UUID
    kind: str
    expires_at: datetime


def _digest(token: str) -> str:
    """Return the stored digest of one credential."""
    return sha256(token.encode("ascii")).hexdigest()


async def issue_credential(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    activation_id: UUID,
    kind: str,
    lifetime_seconds: int,
) -> IssuedCredential:
    """Mint one write-only credential for an activation and store only its digest."""
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    credential_id = uuid7()
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=lifetime_seconds)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.telemetry_credentials (
                id, client_id, activation_id, kind, token_digest, expires_at
            )
            values (%s, %s, %s, %s, %s, %s)
            """,
            (credential_id, client_id, activation_id, kind, _digest(token), expires_at),
        )
    _log.info(
        "telemetry_credential_issued",
        client_id=str(client_id),
        activation_id=str(activation_id),
        telemetry_credential_id=str(credential_id),
        kind=kind,
        expires_at=expires_at.isoformat(),
    )
    return IssuedCredential(
        telemetry_credential_id=credential_id, token=token, expires_at=expires_at
    )


async def resolve_credential(
    database: Database, *, token: str, expected_kind: str
) -> ResolvedCredential:
    """Resolve a presented credential, or refuse with the exact reason.

    The lane, the activation state, expiry, and durable revocation are all checked
    here. A credential minted for the other edge is refused even when it is
    otherwise current.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select credentials.id,
                   credentials.client_id,
                   credentials.activation_id,
                   credentials.kind,
                   credentials.state,
                   credentials.expires_at,
                   activations.state as activation_state
            from public.telemetry_credentials as credentials
            join public.telemetry_activations as activations
              on activations.id = credentials.activation_id
            where credentials.token_digest = %s
            """,
            (_digest(token),),
        )
        row = await cur.fetchone()
    if row is None:
        raise TelemetryAuthenticationError("the presented credential is not known")
    kind = as_str(row, "kind")
    if kind != expected_kind:
        raise TelemetryAuthenticationError(
            f"the credential belongs to the {kind} edge, not the {expected_kind} edge"
        )
    if as_str(row, "state") != "active":
        raise TelemetryAuthenticationError("the credential has been revoked")
    expires_at = as_datetime(row, "expires_at")
    if expires_at <= datetime.now(tz=UTC):
        raise TelemetryAuthenticationError("the credential has expired")
    activation_state = as_str(row, "activation_state")
    if activation_state != "active":
        raise TelemetryAuthenticationError(
            f"the bound observation activation is {activation_state}"
        )
    return ResolvedCredential(
        telemetry_credential_id=as_uuid(row, "id"),
        activation_id=as_uuid(row, "activation_id"),
        client_id=as_uuid(row, "client_id"),
        kind=kind,
        expires_at=expires_at,
    )


