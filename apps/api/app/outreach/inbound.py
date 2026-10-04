"""Routing an inbound direct message.

An inbound bot DM routes only to an invitation, an active session, a re-contact,
or an explicit preference action. Nothing else acts on it: a direct message is
not an intake surface, so it can never become a customer report or a Product
Signal.

Preference actions are matched deterministically against a closed vocabulary.
A message that is not one of them is recorded and handed to the session that is
already open, or recorded as unroutable.

Contract: docs/arch-components/signal-and-outreach.md#discord-ingestion-and-identity
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.clients.provider_map import PROVIDER_DISCORD
from app.db.pool import Database
from app.db.rows import as_uuid
from app.ids import uuid7
from app.logging import get_logger
from app.outreach.cancellation import cancel_case
from app.outreach.reasons import DECLINED, DO_NOT_CONTACT

__all__ = ["InboundRoute", "RouteKind", "route_direct_message"]

_log = get_logger(__name__)

_STOP_PHRASES: Final[frozenset[str]] = frozenset(
    {"stop", "unsubscribe", "do not contact me", "do not contact", "opt out"}
)
_DECLINE_PHRASES: Final[frozenset[str]] = frozenset(
    {"no", "no thanks", "no thank you", "not interested", "decline", "pass"}
)


class RouteKind:
    """The four destinations an inbound direct message may reach."""

    PREFERENCE_ACTION: Final[str] = "preference_action"
    INVITATION_RESPONSE: Final[str] = "invitation_response"
    ACTIVE_SESSION: Final[str] = "active_session"
    RECONTACT: Final[str] = "recontact"
    UNROUTABLE: Final[str] = "unroutable"


@dataclass(frozen=True, slots=True)
class InboundRoute:
    """Where one inbound direct message went, and what it changed."""

    route_kind: str
    client_id: UUID | None
    outreach_attempt_id: UUID | None
    interview_session_id: UUID | None
    duplicate: bool


def _normalize(content: str) -> str:
    """Return the message reduced to the form preference matching compares."""
    return content.strip().strip(".!").casefold()


async def _open_outreach(
    conn: AsyncConnection[DictRow], *, sender_provider_identity: str
) -> DictRow | None:
    """Find the outstanding private outreach this sender is responding to."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select attempts.id as outreach_attempt_id, attempts.client_id,
                   attempts.product_signal_id, attempts.purpose,
                   sessions.id as interview_session_id
            from public.outreach_attempts as attempts
            join public.provider_refs as refs
              on refs.owner_kind = 'client_user'
             and refs.owner_id = attempts.client_user_id
             and refs.provider = %s
             and refs.resource_type = 'user'
             and refs.provider_resource_id = %s
            left join public.interview_sessions as sessions
              on sessions.product_signal_id = attempts.product_signal_id
             and sessions.state <> 'closed'
            where attempts.state in ('dispatching', 'awaiting_response', 'claimed')
            order by attempts.created_at desc
            limit 1
            """,
            (PROVIDER_DISCORD, sender_provider_identity),
        )
        return await cur.fetchone()


async def _record(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID | None,
    provider_message_id: str,
    sender_provider_identity: str,
    content: str,
    route_kind: str,
    outreach_attempt_id: UUID | None,
    interview_session_id: UUID | None,
    occurred_at: datetime,
) -> bool:
    """Record the inbound message. Returns whether it was a duplicate delivery."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.inbound_direct_messages (
                id, client_id, provider, provider_message_id,
                sender_provider_identity, content, route_kind, outreach_attempt_id,
                interview_session_id, occurred_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (provider, provider_message_id) do nothing
            returning id
            """,
            (
                uuid7(),
                client_id,
                PROVIDER_DISCORD,
                provider_message_id,
                sender_provider_identity,
                content,
                route_kind,
                outreach_attempt_id,
                interview_session_id,
                occurred_at,
            ),
        )
        return await cur.fetchone() is None


async def route_direct_message(
    database: Database,
    *,
    provider_message_id: str,
    sender_provider_identity: str,
    content: str,
    occurred_at: datetime,
) -> InboundRoute:
    """Route one inbound direct message to its only permitted destination."""
    normalized = _normalize(content)
    async with database.transaction() as conn:
        outreach = await _open_outreach(
            conn, sender_provider_identity=sender_provider_identity
        )
        if outreach is None:
            duplicate = await _record(
                conn,
                client_id=None,
                provider_message_id=provider_message_id,
                sender_provider_identity=sender_provider_identity,
                content=content,
                route_kind=RouteKind.UNROUTABLE,
                outreach_attempt_id=None,
                interview_session_id=None,
                occurred_at=occurred_at,
            )
            return InboundRoute(
                route_kind=RouteKind.UNROUTABLE,
                client_id=None,
                outreach_attempt_id=None,
                interview_session_id=None,
                duplicate=duplicate,
            )

        client_id = as_uuid(outreach, "client_id")
        attempt_id = as_uuid(outreach, "outreach_attempt_id")
        product_signal_id = as_uuid(outreach, "product_signal_id")
        session_raw = outreach["interview_session_id"]
        session_id = UUID(str(session_raw)) if session_raw is not None else None

        if normalized in _STOP_PHRASES:
            route_kind = RouteKind.PREFERENCE_ACTION
            reason = DO_NOT_CONTACT
        elif normalized in _DECLINE_PHRASES:
            route_kind = RouteKind.INVITATION_RESPONSE
            reason = DECLINED
        elif session_id is not None:
            route_kind = RouteKind.ACTIVE_SESSION
            reason = ""
        else:
            route_kind = RouteKind.RECONTACT
            reason = ""

        duplicate = await _record(
            conn,
            client_id=client_id,
            provider_message_id=provider_message_id,
            sender_provider_identity=sender_provider_identity,
            content=content,
            route_kind=route_kind,
            outreach_attempt_id=attempt_id,
            interview_session_id=session_id,
            occurred_at=occurred_at,
        )
        if not duplicate and reason:
            if reason == DO_NOT_CONTACT:
                await _set_do_not_contact(conn, outreach_attempt_id=attempt_id)
            await cancel_case(
                conn,
                client_id=client_id,
                product_signal_id=product_signal_id,
                reason=reason,
                outreach_attempt_id=attempt_id,
                attempt_trigger="decline",
            )
    _log.info(
        "inbound_direct_message_routed",
        route_kind=route_kind,
        client_id=str(client_id),
        outreach_attempt_id=str(attempt_id),
        duplicate=duplicate,
    )
    return InboundRoute(
        route_kind=route_kind,
        client_id=client_id,
        outreach_attempt_id=attempt_id,
        interview_session_id=session_id,
        duplicate=duplicate,
    )


async def _set_do_not_contact(
    conn: AsyncConnection[DictRow], *, outreach_attempt_id: UUID
) -> None:
    """Record the global do-not-contact preference for the outreach recipient."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.users
            set do_not_contact = true, revision = revision + 1, updated_at = now()
            where id = (
                select client_users.utrace_user_id
                from public.outreach_attempts as attempts
                join public.client_users as client_users
                  on client_users.id = attempts.client_user_id
                where attempts.id = %s
            )
            """,
            (outreach_attempt_id,),
        )
