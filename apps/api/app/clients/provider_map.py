"""Resolving provider references to uTrace identity.

A provider identity never substitutes for uTrace identity. These functions turn
a Discord guild into the client that connected it, and a Discord account
observed in that guild into the `client_user_id` and customer membership the
control plane decides with. An unresolved identity is an explicit refusal, not
an anonymous participant.

Contract: docs/arch-components/identity-and-access.md#identity-and-tenancy-model
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.db.pool import Database
from app.db.rows import as_bool, as_str, as_uuid
from app.errors import AggregateNotFoundError, AuthorizationError

__all__ = [
    "PROVIDER_DISCORD",
    "RESOURCE_CHANNEL",
    "RESOURCE_GITHUB_REPOSITORY",
    "RESOURCE_GUILD",
    "RESOURCE_SIGN_IN_IDENTITY",
    "RESOURCE_STRIPE_CUSTOMER",
    "RESOURCE_USER",
    "ResolvedAuthor",
    "provider_identity_for_client_user",
    "resolve_author",
    "resolve_author_on_connection",
    "resolve_client_for_installation",
    "sign_in_binding",
    "upsert_provider_ref",
]

PROVIDER_DISCORD = "discord"
RESOURCE_GUILD = "guild"
RESOURCE_CHANNEL = "channel"
RESOURCE_USER = "user"
RESOURCE_GITHUB_REPOSITORY = "github_repository"
RESOURCE_STRIPE_CUSTOMER = "stripe_customer"
RESOURCE_SIGN_IN_IDENTITY = "sign_in_identity"
"""The authentication identity an invitation binds to, whatever provider issues it."""

_OWNER_CLIENT = "client"
_OWNER_CLIENT_USER = "client_user"


@dataclass(frozen=True, slots=True)
class ResolvedAuthor:
    """One Customer Surface author resolved to client-scoped uTrace identity."""

    client_user_id: UUID
    utrace_user_id: UUID
    customer_account_id: UUID
    customer_membership_id: UUID
    external_account_key: str
    do_not_contact: bool


async def upsert_provider_ref(
    conn: AsyncConnection[DictRow],
    *,
    ref_id: UUID,
    client_id: UUID,
    provider: str,
    installation_ref: str,
    resource_type: str,
    provider_resource_id: str,
    owner_kind: str,
    owner_id: UUID,
) -> None:
    """Record one provider resource and the uTrace owner it resolves to.

    The natural key is the provider resource itself, so re-running client
    configuration rebinds an existing reference instead of duplicating it.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.provider_refs (
                id, client_id, provider, installation_ref, resource_type,
                provider_resource_id, owner_kind, owner_id
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (client_id, provider, installation_ref, resource_type,
                         provider_resource_id)
            do update set owner_kind = excluded.owner_kind,
                          owner_id = excluded.owner_id
            """,
            (
                ref_id,
                client_id,
                provider,
                installation_ref,
                resource_type,
                provider_resource_id,
                owner_kind,
                owner_id,
            ),
        )


async def resolve_client_for_installation(
    conn: AsyncConnection[DictRow], *, provider: str, installation_ref: str
) -> UUID:
    """Return the client that connected one provider installation.

    The guild-to-client binding is a provider reference owned by the client, so
    an unconfigured server resolves to nothing and never reaches routing.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select client_id from public.provider_refs
            where provider = %s and installation_ref = %s
              and resource_type = %s and owner_kind = %s
            """,
            (provider, installation_ref, RESOURCE_GUILD, _OWNER_CLIENT),
        )
        rows = await cur.fetchall()
    if not rows:
        raise AuthorizationError(
            subject=f"{provider} installation {installation_ref}",
            reason="no client has connected this Customer Surface",
        )
    if len(rows) > 1:
        raise AuthorizationError(
            subject=f"{provider} installation {installation_ref}",
            reason="the installation resolves to more than one client",
        )
    return as_uuid(rows[0], "client_id")


_SELECT_AUTHOR = """
select client_users.id as client_user_id,
       client_users.utrace_user_id,
       memberships.id as customer_membership_id,
       accounts.id as customer_account_id,
       accounts.external_account_key,
       users.do_not_contact
from public.provider_refs as refs
join public.client_users as client_users on client_users.id = refs.owner_id
join public.users as users on users.id = client_users.utrace_user_id
join public.customer_memberships as memberships
  on memberships.client_user_id = client_users.id
join public.customer_accounts as accounts
  on accounts.id = memberships.customer_account_id
where refs.client_id = %s
  and refs.provider = %s
  and refs.installation_ref = %s
  and refs.resource_type = %s
  and refs.provider_resource_id = %s
  and refs.owner_kind = %s
"""


async def resolve_author_on_connection(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    provider: str,
    installation_ref: str,
    provider_user_id: str,
) -> ResolvedAuthor:
    """Resolve one provider account to its client-scoped identity and membership."""
    async with conn.cursor() as cur:
        await cur.execute(
            _SELECT_AUTHOR,
            (
                client_id,
                provider,
                installation_ref,
                RESOURCE_USER,
                provider_user_id,
                _OWNER_CLIENT_USER,
            ),
        )
        rows = await cur.fetchall()
    if not rows:
        raise AuthorizationError(
            subject=f"{provider} account {provider_user_id}",
            reason=(
                f"no client-scoped identity and customer membership exist for client "
                f"{client_id}"
            ),
        )
    if len(rows) > 1:
        raise AuthorizationError(
            subject=f"{provider} account {provider_user_id}",
            reason="the account holds more than one customer membership for this client",
        )
    row = rows[0]
    return ResolvedAuthor(
        client_user_id=as_uuid(row, "client_user_id"),
        utrace_user_id=as_uuid(row, "utrace_user_id"),
        customer_account_id=as_uuid(row, "customer_account_id"),
        customer_membership_id=as_uuid(row, "customer_membership_id"),
        external_account_key=as_str(row, "external_account_key"),
        do_not_contact=as_bool(row, "do_not_contact"),
    )


async def resolve_author(
    database: Database,
    *,
    client_id: UUID,
    provider: str,
    installation_ref: str,
    provider_user_id: str,
) -> ResolvedAuthor:
    """Resolve one provider account in its own read transaction."""
    async with database.connection() as conn:
        return await resolve_author_on_connection(
            conn,
            client_id=client_id,
            provider=provider,
            installation_ref=installation_ref,
            provider_user_id=provider_user_id,
        )


async def provider_identity_for_client_user(
    conn: AsyncConnection[DictRow], *, client_user_id: UUID, provider: str
) -> str:
    """Return the provider account bound to one client-scoped user.

    Private outreach is delivered to this account, so a missing binding refuses
    rather than falling back to any other contact route.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select provider_resource_id from public.provider_refs
            where owner_kind = %s and owner_id = %s
              and provider = %s and resource_type = %s
            """,
            (_OWNER_CLIENT_USER, client_user_id, provider, RESOURCE_USER),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="provider_refs", row_id=client_user_id)
    return as_str(row, "provider_resource_id")


async def sign_in_binding(
    conn: AsyncConnection[DictRow], *, client_user_id: UUID
) -> tuple[str, str]:
    """Return the authentication provider and subject one invitation binds to.

    The binding is provider-generic on purpose: Discord sign-in is the pilot's
    participant provider, and any other configured provider resolves the same
    way, so the claim path never hard-codes one identity provider.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select provider, provider_resource_id from public.provider_refs
            where owner_kind = %s and owner_id = %s and resource_type = %s
            """,
            (_OWNER_CLIENT_USER, client_user_id, RESOURCE_SIGN_IN_IDENTITY),
        )
        rows = await cur.fetchall()
    if not rows:
        raise AggregateNotFoundError(table="provider_refs", row_id=client_user_id)
    if len(rows) > 1:
        raise AuthorizationError(
            subject=f"client user {client_user_id}",
            reason="more than one sign-in identity is bound to this participant",
        )
    return as_str(rows[0], "provider"), as_str(rows[0], "provider_resource_id")
