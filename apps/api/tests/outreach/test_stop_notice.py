"""The private notice a client stop owes the originating user.

The Command, its Result, and the recipient checks run against the real
database. The Customer Surface is a recording port so the test can count sends
without messaging a real person.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    CommandDisposition,
    EffectState,
    OutreachAttemptState,
    ProductSignalState,
)

from app.clients.provider_map import (
    PROVIDER_DISCORD,
    RESOURCE_SIGN_IN_IDENTITY,
    RESOURCE_USER,
    upsert_provider_ref,
)
from app.connectors.ports import ChannelMessageReceipt, DirectMessageReceipt, MessageReceipt
from app.db.pool import Database
from app.db.rows import as_str
from app.ids import uuid7
from app.outreach.delivery import SEND_COMMAND, record_delivery
from app.outreach.reasons import DO_NOT_CONTACT
from app.outreach.stop_notice import STOP_NOTICE_COMMAND, send_stop_notice
from tests.factories import CaseFixture, create_case, create_outreach_attempt
from tests.supabase_auth import SignedInUser

GUILD_ID = "stop-notice-guild"


class RecordingSurface:
    """`CustomerSurfacePort` that records each direct message instead of sending it."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def fetch_message(self, *, channel_id: str, message_id: str) -> MessageReceipt:
        raise AssertionError("the stop notice never reads a channel message")

    async def send_direct_message(self, *, user_id: str, content: str) -> DirectMessageReceipt:
        self.sent.append((user_id, content))
        return DirectMessageReceipt(
            provider="discord",
            message_id=f"dm-{len(self.sent)}",
            channel_id="dm-channel",
            recipient_id=user_id,
            sent_at=datetime.now(tz=UTC),
        )

    async def reply_in_channel(
        self, *, channel_id: str, message_id: str, content: str
    ) -> ChannelMessageReceipt:
        raise AssertionError("the stop notice never replies in a channel")


async def _reached_case(
    database: Database, auth_users: dict[str, SignedInUser], *, name: str
) -> tuple[CaseFixture, str]:
    """A case whose originating user received the initial invitation."""
    case = await create_case(
        database,
        name=name,
        state=ProductSignalState.AWAITING_INTERVIEW,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    discord_user_id = f"discord-{uuid7().hex}"
    attempt_id = await create_outreach_attempt(
        database, case=case, state=OutreachAttemptState.AWAITING_RESPONSE, expires_in_seconds=3600
    )
    command_id = uuid7()
    async with database.transaction() as conn:
        for resource_type, resource_id in (
            (RESOURCE_USER, discord_user_id),
            (RESOURCE_SIGN_IN_IDENTITY, discord_user_id),
        ):
            await upsert_provider_ref(
                conn,
                ref_id=uuid7(),
                client_id=case.client_id,
                provider=PROVIDER_DISCORD,
                installation_ref=GUILD_ID,
                resource_type=resource_type,
                provider_resource_id=resource_id,
                owner_kind="client_user",
                owner_id=case.client_user_id,
            )
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.commands (
                    id, client_id, command_type, state, target_kind, target_id,
                    payload, declared_revisions, queued_at
                )
                values (%s, %s, %s, 'queued', 'outreach_attempt', %s, %s, %s, now())
                """,
                (command_id, case.client_id, SEND_COMMAND, attempt_id, Jsonb({}), Jsonb({})),
            )
        await record_delivery(
            conn,
            client_id=case.client_id,
            outreach_attempt_id=attempt_id,
            command_id=command_id,
            recipient_provider_identity=discord_user_id,
            provider_message_ref="invitation-dm",
            effect_state=EffectState.COMPLETED,
            disposition=CommandDisposition.SUCCEEDED,
            failure_category=None,
            detail={},
        )
    return case, discord_user_id


async def _notice_dispositions(database: Database, *, product_signal_id: UUID) -> list[str]:
    """Return the terminal Result disposition of every notice Command for one case."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select results.disposition from public.results as results
            join public.commands as commands on commands.id = results.command_id
            where commands.command_type = %s and commands.target_id = %s
            """,
            (STOP_NOTICE_COMMAND, product_signal_id),
        )
        return [as_str(row, "disposition") for row in await cur.fetchall()]


async def test_stop_notice_is_sent_once_and_never_to_a_do_not_contact_user(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case, discord_user_id = await _reached_case(database, auth_users, name="stop-notice")
    surface = RecordingSurface()

    first = await send_stop_notice(
        database, surface, client_id=case.client_id, product_signal_id=case.product_signal_id
    )
    assert (first.sent, first.reason) == (True, "delivered")
    assert surface.sent == [
        (
            discord_user_id,
            (
                "The stop-notice team stopped the case about your stop-notice product "
                "request. Nothing further will run, and anything you had already "
                "completed still counts."
            ),
        )
    ]

    again = await send_stop_notice(
        database, surface, client_id=case.client_id, product_signal_id=case.product_signal_id
    )
    assert (again.sent, again.reason, again.command_id) == (
        False,
        "already_concluded",
        first.command_id,
    )
    assert len(surface.sent) == 1
    assert await _notice_dispositions(
        database, product_signal_id=case.product_signal_id
    ) == [CommandDisposition.SUCCEEDED.value]

    opted_out, _ = await _reached_case(database, auth_users, name="stop-notice-dnc")
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.users set do_not_contact = true where id = %s",
            (opted_out.utrace_user_id,),
        )
    quiet = RecordingSurface()
    skipped = await send_stop_notice(
        database,
        quiet,
        client_id=opted_out.client_id,
        product_signal_id=opted_out.product_signal_id,
    )
    assert (skipped.sent, skipped.reason) == (False, DO_NOT_CONTACT)
    assert quiet.sent == []
    assert await _notice_dispositions(
        database, product_signal_id=opted_out.product_signal_id
    ) == [CommandDisposition.CANCELED.value]
