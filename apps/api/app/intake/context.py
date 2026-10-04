"""The bounded routing context one Intake Item is judged on.

The base context holds the sealed messages, direct reply and thread references,
the authenticated scope, the routing configuration, and a source-linked Product
Overview revision. Nothing else reaches the decision model: there is no ambient
production telemetry and no repository search in intake.

Assembly requires no generative model call. Conditional assistance adds nearby
Discord context, retrieved product documentation, or GPT-6.1 Sol's interpretation to
this same structure and records why it was added.

Contract: docs/arch-components/context-assembly.md#stage-boundaries
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.clients.configuration import ActiveRelease
from app.clients.overview import ActiveProductOverview, render_overview
from app.db.pool import Database
from app.db.rows import as_datetime, as_str, as_uuid
from app.envelope import JsonValue
from app.errors import AggregateNotFoundError

__all__ = [
    "IntakeScope",
    "RoutingContext",
    "SealedMessage",
    "assemble_context",
    "with_assistance",
    "with_interpretation",
]
_OVERVIEW_SECTIONS = ("purpose", "capabilities", "workflows", "terminology")
"""The overview sections every routing judgement reads; the rest join only when
a judgement reports a fact it could not resolve."""


@dataclass(frozen=True, slots=True)
class SealedMessage:
    """One immutable message inside the sealed batch, in author order."""

    ordinal: int
    provider_message_id: str
    content: str
    reply_to_provider_message_id: str | None
    thread_ref: str | None
    occurred_at: str


@dataclass(frozen=True, slots=True)
class IntakeScope:
    """The authenticated scope the batch was observed in."""

    client_id: UUID
    intake_item_id: UUID
    channel_ref: str
    author_provider_identity: str
    client_user_id: UUID
    customer_account_id: UUID
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RoutingContext:
    """Everything routing supplies, with the revisions it pinned."""

    scope: IntakeScope
    messages: tuple[SealedMessage, ...]
    release: ActiveRelease
    overview: ActiveProductOverview
    overview_excerpt: str
    nearby_messages: tuple[str, ...]
    product_documentation: str | None
    interpretation: str | None
    assistance_reason: str | None

    def batch_text(self) -> str:
        """Return the sealed batch as the ordered text the decision model reads.

        Each message is prefixed with its `[n]` ordinal for the model; text that
        people read quotes `wording` instead.
        """
        return "\n".join(
            f"[{message.ordinal}] {message.content}" for message in self.messages
        )

    def wording(self) -> str:
        """Return the originating user's own words, in order, without markers."""
        return "\n".join(message.content for message in self.messages)

    def state(self) -> Mapping[str, JsonValue]:
        """Return the supplied state for one Jev request.

        Only facts appear here. Identity, permissions, counts, and dates stay in
        application code, which is what resolves them.
        """
        body: dict[str, JsonValue] = {
            "surface": "discord_watched_channel",
            "channel": self.scope.channel_ref,
            "report": self.batch_text(),
            "product_overview": self.overview_excerpt,
        }
        if self.nearby_messages:
            body["nearby_conversation"] = list(self.nearby_messages)
        if self.product_documentation is not None:
            body["product_documentation"] = self.product_documentation
        if self.interpretation is not None:
            body["assisted_interpretation"] = self.interpretation
        return body


def with_assistance(
    context: RoutingContext,
    *,
    reason: str,
    nearby_messages: Sequence[str],
) -> RoutingContext:
    """Return the context widened for a judgement that could not resolve a fact.

    It adds the overview sections the excerpt left out and any nearby messages
    fetched for the batch, and records why.
    """
    return replace(
        context,
        nearby_messages=context.nearby_messages + tuple(nearby_messages),
        product_documentation=render_overview(
            context.overview.overview,
            sections=tuple(
                section.key
                for section in context.overview.overview.sections()
                if section.key not in _OVERVIEW_SECTIONS
            ),
        ),
        assistance_reason=reason,
    )


def with_interpretation(
    context: RoutingContext, *, reason: str, interpretation: str
) -> RoutingContext:
    """Return the context carrying GPT-6.1 Sol's labelled interpretation of the batch."""
    return replace(context, interpretation=interpretation, assistance_reason=reason)


async def assemble_context(
    database: Database,
    *,
    intake_item_id: UUID,
    release: ActiveRelease,
    overview: ActiveProductOverview,
    scope: IntakeScope,
) -> RoutingContext:
    """Assemble the base routing context for one sealed Intake Item."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select payload from public.intake_items where id = %s", (intake_item_id,)
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="intake_items", row_id=intake_item_id)
    messages = _decode_messages(row["payload"])
    return RoutingContext(
        scope=scope,
        messages=messages,
        release=release,
        overview=overview,
        overview_excerpt=render_overview(overview.overview, sections=_OVERVIEW_SECTIONS),
        nearby_messages=(),
        product_documentation=None,
        interpretation=None,
        assistance_reason=None,
    )


def _decode_messages(payload: object) -> tuple[SealedMessage, ...]:
    """Decode the sealed message list stored on the Intake Item."""
    if not isinstance(payload, dict):
        raise TypeError("intake item payload is not an object")
    raw = payload.get("messages")
    if not isinstance(raw, list) or not raw:
        raise ValueError("intake item payload carries no sealed messages")
    decoded: list[SealedMessage] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("a sealed message is not an object")
        decoded.append(
            SealedMessage(
                ordinal=int(str(item["ordinal"])),
                provider_message_id=str(item["provider_message_id"]),
                content=str(item["content"]),
                reply_to_provider_message_id=_optional_str(
                    item.get("reply_to_provider_message_id")
                ),
                thread_ref=_optional_str(item.get("thread_ref")),
                occurred_at=str(item["occurred_at"]),
            )
        )
    decoded.sort(key=lambda message: message.ordinal)
    return tuple(decoded)


def _optional_str(value: object) -> str | None:
    """Return a nullable provider reference as text."""
    if value is None:
        return None
    return str(value)


async def load_scope(
    database: Database, *, intake_item_id: UUID
) -> IntakeScope:
    """Return the authenticated scope recorded on one sealed Intake Item."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select items.id, items.client_id, items.source_channel_ref,
                   items.author_provider_identity, items.client_user_id,
                   items.customer_account_id, items.created_at
            from public.intake_items as items
            where items.id = %s
            """,
            (intake_item_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="intake_items", row_id=intake_item_id)
    return IntakeScope(
        client_id=as_uuid(row, "client_id"),
        intake_item_id=as_uuid(row, "id"),
        channel_ref=as_str(row, "source_channel_ref"),
        author_provider_identity=as_str(row, "author_provider_identity"),
        client_user_id=as_uuid(row, "client_user_id"),
        customer_account_id=as_uuid(row, "customer_account_id"),
        occurred_at=as_datetime(row, "created_at"),
    )
