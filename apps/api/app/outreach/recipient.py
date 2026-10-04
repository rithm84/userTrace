"""Reading the recipient and the text one private invitation needs.

Every value here comes from the client's own configuration or from what the
originating user wrote. The Stripe Customer comes from the client's verified
provider reference, never from an email match, a model guess, or a
participant-supplied identifier.

Contract: docs/arch-components/identity-and-access.md#outreach-offers-recipient-mapping
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.clients.provider_map import (
    PROVIDER_DISCORD,
    provider_identity_for_client_user,
    sign_in_binding,
)
from app.db.rows import as_bool, as_str, as_uuid
from app.errors import GuardRejectedError

__all__ = [
    "Recipient",
    "billing_customer_ref",
    "client_name",
    "concern_excerpt",
    "load_recipient",
    "product_name",
]


@dataclass(frozen=True, slots=True)
class Recipient:
    """The originating user's client-scoped identity and delivery references."""

    client_user_id: UUID
    customer_account_id: UUID
    customer_account_name: str
    do_not_contact: bool
    provider_user_id: str
    sign_in_provider: str
    sign_in_identity: str


@dataclass(frozen=True, slots=True)
class _Prepared:
    """What the first transaction committed, carried into the provider send."""

    outreach_attempt_id: UUID
    command_id: UUID
    invitation_id: UUID
    recipient_provider_identity: str
    content: str


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """The committed result of one private outreach dispatch."""

    outreach_attempt_id: UUID | None
    invitation_id: UUID | None
    state: str
    delivered: bool
    reason: str


async def load_recipient(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID
) -> Recipient:
    """Resolve the originating user and how private outreach reaches them."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select signals.originating_client_user_id as client_user_id,
                   accounts.id as customer_account_id,
                   accounts.name as customer_account_name,
                   users.do_not_contact
            from public.product_signals as signals
            join public.customer_accounts as accounts
              on accounts.id = signals.originating_customer_account_id
            join public.client_users as client_users
              on client_users.id = signals.originating_client_user_id
            join public.users as users on users.id = client_users.utrace_user_id
            where signals.id = %s
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise GuardRejectedError(
            machine="outreach_attempt",
            trigger="dispatch",
            reason=f"Product Signal {product_signal_id} has no originating user",
        )
    client_user_id = as_uuid(row, "client_user_id")
    provider_user_id = await provider_identity_for_client_user(
        conn, client_user_id=client_user_id, provider=PROVIDER_DISCORD
    )
    provider, identity = await sign_in_binding(conn, client_user_id=client_user_id)
    return Recipient(
        client_user_id=client_user_id,
        customer_account_id=as_uuid(row, "customer_account_id"),
        customer_account_name=as_str(row, "customer_account_name"),
        do_not_contact=as_bool(row, "do_not_contact"),
        provider_user_id=provider_user_id,
        sign_in_provider=provider,
        sign_in_identity=identity,
    )


async def concern_excerpt(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID
) -> str:
    """Return the originating user's own wording, quoted back in the invitation."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select source_excerpt from public.source_reports
            where product_signal_id = %s
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise GuardRejectedError(
            machine="outreach_attempt",
            trigger="dispatch",
            reason=f"Product Signal {product_signal_id} has no originating report",
        )
    return as_str(row, "source_excerpt")


async def client_name(conn: AsyncConnection[DictRow], *, client_id: UUID) -> str:
    """Return the client organization's name for the invitation text."""
    async with conn.cursor() as cur:
        await cur.execute("select name from public.clients where id = %s", (client_id,))
        row = await cur.fetchone()
    if row is None:
        raise GuardRejectedError(
            machine="outreach_attempt", trigger="dispatch", reason="unknown client"
        )
    return as_str(row, "name")


async def product_name(conn: AsyncConnection[DictRow], *, client_id: UUID) -> str:
    """Return the client's product name for the invitation text."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select product_name from public.clients where id = %s", (client_id,)
        )
        row = await cur.fetchone()
    if row is None:
        raise GuardRejectedError(
            machine="outreach_attempt", trigger="dispatch", reason="unknown client"
        )
    return as_str(row, "product_name")


async def billing_customer_ref(
    conn: AsyncConnection[DictRow], *, client_id: UUID, customer_account_id: UUID
) -> str:
    """Return the verified Stripe Customer mapped to one customer organization.

    The mapping comes from the client's configured provider reference, never from
    an email match, a model guess, or a participant-supplied identifier.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select provider_resource_id from public.provider_refs
            where client_id = %s and provider = 'stripe'
              and resource_type = 'stripe_customer' and owner_id = %s
            """,
            (client_id, customer_account_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise GuardRejectedError(
            machine="outreach_offer",
            trigger="reserve",
            reason=(
                f"customer account {customer_account_id} has no verified Stripe "
                "Customer mapping for this client"
            ),
        )
    return as_str(row, "provider_resource_id")


