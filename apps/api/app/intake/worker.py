"""The intake and outreach Job handlers, and the watched-channel consumer.

Handlers open their own provider clients for the duration of one Job. A Job is
the unit of durability here: the gateway consumer only records messages, and
every decision and every external effect happens inside a claimed Job with a
frozen budget.

Contract: docs/arch-components/control-plane.md#jobs-commands-and-scheduling
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from uuid import UUID

from app.clients.configuration import ActiveRelease, load_active_release
from app.connectors.discord_connector import DiscordConnector
from app.connectors.openai_roles import OpenAIRole, openai_gateway_for
from app.connectors.ports import MessageReceipt, TypingReceipt
from app.connectors.typesafe_jev import JevClient
from app.db.pool import Database
from app.errors import AuthorizationError
from app.execution.jobs import ClaimedJob
from app.intake.errors import UnattributedAuthorError, UnwatchedSurfaceError
from app.intake.jobs import (
    EXPIRE_INITIAL_INVITATION,
    ROUTE_INTAKE_ITEM,
    SEAL_INTAKE_BATCH,
    SEND_INITIAL_INVITATION,
    SEND_STOP_NOTICE,
)
from app.intake.messages import DebounceWindow, extend_for_typing, record_message, seal_batch
from app.intake.routing import RoutingDependencies, route_intake_item
from app.intake.settings import IntakeOutreachSettings, get_intake_settings
from app.logging import get_logger
from app.outreach.dispatch import send_initial_invitation
from app.outreach.expiry import expire_initial_invitation
from app.outreach.stop_notice import send_stop_notice

__all__ = [
    "connector_for_release",
    "consume_watched_channels",
    "discord_connector_for",
    "expire_invitation_job",
    "intake_handlers",
    "route_job",
    "seal_job",
    "send_invitation_job",
    "send_stop_notice_job",
]

_log = get_logger(__name__)

type JobHandler = Callable[[Database, ClaimedJob], Awaitable[None]]


def connector_for_release(
    settings: IntakeOutreachSettings, *, release: ActiveRelease
) -> DiscordConnector:
    """Build the connector one pinned configuration release describes.

    Watched channels come from the release, so the set of intake surfaces is
    configuration rather than a runtime decision.
    """
    surface = release.policy.customer_surface
    return DiscordConnector(
        bot_token=settings.discord_bot_token,
        guild_id=surface.installation_ref,
        watched_channel_ids=[channel.channel_ref for channel in surface.watched_channels],
        direct_message_allowlist=None,
    )


async def discord_connector_for(
    database: Database, settings: IntakeOutreachSettings, *, client_id: UUID
) -> DiscordConnector:
    """Build the connector for one client's configured Customer Surface."""
    release = await load_active_release(database, client_id=client_id)
    return connector_for_release(settings, release=release)


@asynccontextmanager
async def _routing_dependencies(
    database: Database, settings: IntakeOutreachSettings, *, client_id: UUID
) -> AsyncIterator[RoutingDependencies]:
    """Open the providers routing may call, for the duration of one Job."""
    connector = await discord_connector_for(database, settings, client_id=client_id)
    await connector.open()
    async with (
        JevClient(
            api_key=settings.jev_api_key,
            model_id=settings.jev_model_id,
            timeout_seconds=settings.jev_timeout_seconds,
        ) as jev,
        openai_gateway_for(OpenAIRole.INTAKE_ASSISTANCE) as reasoning,
    ):
        try:
            yield RoutingDependencies(jev=jev, surface=connector, reasoning=reasoning)
        finally:
            await connector.close()


async def seal_job(database: Database, claimed: ClaimedJob) -> None:
    """Seal one debounced batch into its Intake Item."""
    batch_id = UUID(str(claimed.input["intake_batch_id"]))
    sealed = await seal_batch(database, intake_batch_id=batch_id)
    _log.info(
        "intake_seal_job_completed",
        job_id=str(claimed.job_id),
        intake_item_id=str(sealed.intake_item_id),
        message_count=sealed.message_count,
    )


async def route_job(database: Database, claimed: ClaimedJob) -> None:
    """Route one sealed Intake Item to exactly one committed outcome."""
    settings = get_intake_settings()
    client_id = UUID(str(claimed.input["client_id"]))
    item_id = UUID(str(claimed.input["intake_item_id"]))
    async with _routing_dependencies(
        database, settings, client_id=client_id
    ) as dependencies:
        committed = await route_intake_item(
            database, dependencies, intake_item_id=item_id
        )
    _log.info(
        "intake_route_job_completed",
        job_id=str(claimed.job_id),
        intake_item_id=str(item_id),
        outcome=committed.outcome.value if committed.outcome is not None else None,
        blocked_reason=committed.blocked_reason,
    )


async def send_invitation_job(database: Database, claimed: ClaimedJob) -> None:
    """Reserve the offer and deliver one private initial invitation."""
    settings = get_intake_settings()
    client_id = UUID(str(claimed.input["client_id"]))
    product_signal_id = UUID(str(claimed.input["product_signal_id"]))
    connector = await discord_connector_for(database, settings, client_id=client_id)
    await connector.open()
    try:
        outcome = await send_initial_invitation(
            database,
            connector,
            settings,
            client_id=client_id,
            product_signal_id=product_signal_id,
        )
    finally:
        await connector.close()
    _log.info(
        "outreach_invitation_job_completed",
        job_id=str(claimed.job_id),
        product_signal_id=str(product_signal_id),
        state=outcome.state,
        delivered=outcome.delivered,
    )


async def send_stop_notice_job(database: Database, claimed: ClaimedJob) -> None:
    """Tell the originating user, privately, that the client stopped their case."""
    settings = get_intake_settings()
    client_id = UUID(str(claimed.input["client_id"]))
    product_signal_id = UUID(str(claimed.input["product_signal_id"]))
    connector = await discord_connector_for(database, settings, client_id=client_id)
    await connector.open()
    try:
        outcome = await send_stop_notice(
            database,
            connector,
            client_id=client_id,
            product_signal_id=product_signal_id,
        )
    finally:
        await connector.close()
    _log.info(
        "outreach_stop_notice_job_completed",
        job_id=str(claimed.job_id),
        product_signal_id=str(product_signal_id),
        sent=outcome.sent,
        reason=outcome.reason,
    )


async def expire_invitation_job(database: Database, claimed: ClaimedJob) -> None:
    """Expire one unanswered initial invitation and cancel its case."""
    attempt_id = UUID(str(claimed.input["outreach_attempt_id"]))
    outcome = await expire_initial_invitation(
        database, outreach_attempt_id=attempt_id
    )
    _log.info(
        "outreach_expiry_job_completed",
        job_id=str(claimed.job_id),
        outreach_attempt_id=str(attempt_id),
        expired=outcome.expired,
        reason=outcome.reason,
    )


def intake_handlers() -> Mapping[str, JobHandler]:
    """Return the Job handlers this workstream owns."""
    return {
        SEAL_INTAKE_BATCH: seal_job,
        ROUTE_INTAKE_ITEM: route_job,
        SEND_INITIAL_INVITATION: send_invitation_job,
        SEND_STOP_NOTICE: send_stop_notice_job,
        EXPIRE_INITIAL_INVITATION: expire_invitation_job,
    }


async def consume_watched_channels(
    database: Database, settings: IntakeOutreachSettings, *, client_id: UUID
) -> None:
    """Consume one client's watched channels until cancelled or the gateway ends.

    The consumer records; it never decides. Each recorded message schedules or
    extends its batch's seal Job, and routing happens inside that Job. The author
    typing again extends their open batch until the typing indicator lapses. The
    watched-channel set is read once from the client's pinned active release, so
    a later release reaches intake when the process restarts.

    Three typed refusals are boundary outcomes rather than consumer failures: a
    channel the release does not watch, a bot author with no reporter
    attribution, and an author who holds no client-scoped identity and customer
    membership. None of them may become a customer report, and anyone can post
    in a watched channel, so each is logged with its reason and the next receipt
    is read. Every other error leaves this function, including the gateway
    ending, which `DiscordConnector.consume` raises.

    Returns only when the caller cancels it.
    """
    window = DebounceWindow(
        quiet_seconds=settings.debounce_quiet_seconds,
        maximum_seconds=settings.debounce_maximum_seconds,
    )
    release = await load_active_release(database, client_id=client_id)
    surface = release.policy.customer_surface
    connector = connector_for_release(settings, release=release)
    await connector.open()
    _log.info(
        "intake_consumer_started",
        client_id=str(client_id),
        guild_id=surface.installation_ref,
        release_number=release.release_number,
    )
    try:
        async with connector.consume() as receipts:
            _log.info(
                "intake_consumer_subscribed",
                client_id=str(client_id),
                channel_refs=[channel.channel_ref for channel in surface.watched_channels],
            )
            async for receipt in receipts:
                if isinstance(receipt, TypingReceipt):
                    await _hold_for_typing(database, client_id=client_id, receipt=receipt)
                    continue
                await _record_queued_receipt(
                    database,
                    client_id=client_id,
                    release=release,
                    receipt=receipt,
                    window=window,
                )
    finally:
        await connector.close()
        _log.info("intake_consumer_stopped", client_id=str(client_id))


async def _hold_for_typing(database: Database, *, client_id: UUID, receipt: TypingReceipt) -> None:
    """Hold the typing author's open batch until their indicator lapses."""
    if await extend_for_typing(database, client_id=client_id, receipt=receipt):
        _log.info(
            "intake_consumer_batch_held_for_typing",
            client_id=str(client_id),
            channel_ref=receipt.channel_id,
            author_provider_identity=receipt.author_id,
            active_until=receipt.active_until.isoformat(),
        )


async def _record_queued_receipt(
    database: Database,
    *,
    client_id: UUID,
    release: ActiveRelease,
    receipt: MessageReceipt,
    window: DebounceWindow,
) -> None:
    """Record one receipt the gateway queued, or log why it cannot become one."""
    _log.info(
        "intake_consumer_receipt_queued",
        client_id=str(client_id),
        channel_ref=receipt.channel_id,
        provider_message_id=receipt.message_id,
        author_provider_identity=receipt.author_id,
    )
    try:
        recorded = await record_message(
            database,
            client_id=client_id,
            release=release,
            receipt=receipt,
            window=window,
        )
    except (AuthorizationError, UnattributedAuthorError, UnwatchedSurfaceError) as refusal:
        _log.warning(
            "intake_consumer_receipt_refused",
            client_id=str(client_id),
            provider_message_id=receipt.message_id,
            refusal=type(refusal).__name__,
            detail=str(refusal),
        )
        return
    _log.info(
        "intake_consumer_receipt_recorded",
        client_id=str(client_id),
        provider_message_id=receipt.message_id,
        intake_batch_id=str(recorded.intake_batch_id),
        ordinal=recorded.ordinal,
        duplicate=recorded.duplicate,
    )
