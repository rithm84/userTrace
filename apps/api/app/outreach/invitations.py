"""Opaque expiring invitations bound to one identity, client, case, and user.

The invitation carries no authoritative payload: knowing the URL or the Product
Signal id authorizes nothing. Only the digest is stored, and the row is bound
server-side to the participant's configured sign-in identity, so the claim must
prove control of that identity.

A replacement access link, such as a private return link, supersedes the previous
one for the same case, so each Product Signal keeps one live link.

Contract: docs/arch-components/identity-and-access.md#participant-invitation-and-sign-in
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.db.rows import as_uuid
from app.ids import uuid7
from app.lifecycle.claims import invitation_token_digest
from app.logging import get_logger

__all__ = [
    "MintedInvitation",
    "create_invitation",
    "link_supersession",
    "mint_token",
    "revoke_invitations",
    "revoke_live_invitation",
]

_log = get_logger(__name__)

_TOKEN_BYTES = 32


def mint_token() -> str:
    """Return one opaque invitation token.

    The token is returned once, to the caller that delivers it. Only its digest
    is stored, so a database read cannot reconstruct a working link.
    """
    return secrets.token_urlsafe(_TOKEN_BYTES)


@dataclass(frozen=True, slots=True)
class MintedInvitation:
    """One created invitation and the token that was delivered with it."""

    invitation_id: UUID
    token: str
    expires_at: datetime


async def create_invitation(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    outreach_attempt_id: UUID,
    product_signal_id: UUID,
    client_user_id: UUID,
    bound_provider: str,
    bound_provider_identity: str,
    issued_at: datetime,
    ttl_seconds: int,
) -> MintedInvitation:
    """Create one opaque expiring invitation and return its plaintext token."""
    token = mint_token()
    invitation_id = uuid7()
    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.invitations (
                id, client_id, outreach_attempt_id, product_signal_id, client_user_id,
                token_digest, bound_provider, bound_provider_identity, expires_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                invitation_id,
                client_id,
                outreach_attempt_id,
                product_signal_id,
                client_user_id,
                invitation_token_digest(token),
                bound_provider,
                bound_provider_identity,
                expires_at,
            ),
        )
    _log.info(
        "invitation_created",
        client_id=str(client_id),
        product_signal_id=str(product_signal_id),
        invitation_id=str(invitation_id),
        bound_provider=bound_provider,
    )
    return MintedInvitation(
        invitation_id=invitation_id, token=token, expires_at=expires_at
    )


async def revoke_live_invitation(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID
) -> UUID | None:
    """Revoke the case's live invitation so a replacement can take its place.

    Revoking first is what keeps exactly one live access link per case: the
    replacement is created only after the previous link stops working.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.invitations
            set revoked_at = now(), updated_at = now()
            where product_signal_id = %s and revoked_at is null
              and superseded_by_id is null
            returning id
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    return as_uuid(row, "id") if row is not None else None


async def link_supersession(
    conn: AsyncConnection[DictRow], *, previous_id: UUID, replacement_id: UUID
) -> None:
    """Record which invitation replaced a revoked one."""
    async with conn.cursor() as cur:
        await cur.execute(
            "update public.invitations set superseded_by_id = %s, updated_at = now() "
            "where id = %s",
            (replacement_id, previous_id),
        )


async def revoke_invitations(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID, reason: str
) -> int:
    """Revoke every live invitation for one case, for a cancellation or a stop."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.invitations
            set revoked_at = now(), updated_at = now()
            where product_signal_id = %s and revoked_at is null
            returning id
            """,
            (product_signal_id,),
        )
        rows = await cur.fetchall()
    _log.info(
        "invitations_revoked",
        product_signal_id=str(product_signal_id),
        revoked=len(rows),
        reason=reason,
    )
    return len(rows)
