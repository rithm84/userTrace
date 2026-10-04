"""Sending one private invitation.

Three phases, in this order, because a provider send cannot be undone:

1. One transaction locks the Product Signal, re-reads the case's outreach under
   that lock, reserves the offer against the client's allowance, creates the
   Outreach Attempt and its opaque invitation, and commits the send Command.
2. The direct message is sent, outside any transaction. It is never retried:
   Discord offers no idempotency key for a message send, so a repeat would
   deliver a second invitation.
3. One transaction records the durable delivery Result and moves the attempt to
   `awaiting_response`, or to `unreached` with its reservation released and the
   case canceled.

A delivered invitation is then acknowledged with a short public reply to the
reporter's message, so the channel shows their report was picked up. The reply
says nothing about the offer or the case. Only the run that delivered the
invitation sends it, and its failure leaves the invitation untouched.

Holding the Product Signal row lock across the first phase is what makes one
initial invitation per case true under concurrency: a second dispatch waits
there and then finds the first one's attempt.

Contract: docs/arch-components/signal-and-outreach.md#originating-user-and-invitation-lifecycle
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from utrace_contracts.domain.lifecycle import OutreachAttemptState, OutreachPurpose

from app.clients.configuration import ActiveRelease, load_active_release
from app.connectors.failures import ConnectorFailure
from app.connectors.ports import CustomerSurfacePort
from app.db.kernel import apply_transition_on_connection
from app.db.pool import Database
from app.db.rows import as_str, as_uuid
from app.errors import GuardRejectedError
from app.ids import uuid7
from app.intake.jobs import expire_initial_invitation_job
from app.intake.settings import IntakeOutreachSettings
from app.lifecycle.outreach import OutreachAttemptFacts
from app.logging import get_logger
from app.outreach.attempt_machine import attempt_transition
from app.outreach.budget import contact_allowance
from app.outreach.cancellation import cancel_case
from app.outreach.delivery import (
    DispatchOutcome,
    Prepared,
    UncertainDeliveryError,
    conclude,
    dispatched_command,
    open_attempt,
    send_command,
)
from app.outreach.invitations import (
    create_invitation,
)
from app.outreach.message import OfferTerms, invitation_text
from app.outreach.offers import reserve_offer
from app.outreach.reasons import CONTACT_BUDGET_EXHAUSTED, DO_NOT_CONTACT
from app.outreach.recipient import (
    Recipient,
    billing_customer_ref,
    client_name,
    concern_excerpt,
    load_recipient,
    product_name,
)

__all__ = ["send_initial_invitation"]

_log = get_logger(__name__)

_SECONDS_PER_HOUR: Final[int] = 3600
_CHANNEL_ACKNOWLEDGEMENT: Final[str] = "Thanks for sharing this! I've sent you a DM."


async def _prepare(
    database: Database,
    settings: IntakeOutreachSettings,
    *,
    client_id: UUID,
    product_signal_id: UUID,
    release: ActiveRelease,
) -> Prepared | DispatchOutcome:
    """Commit the reservation, the attempt, the invitation, and the send Command."""
    policy = release.policy
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select state, cancellation_scope_id from public.product_signals
                where id = %s for update
                """,
                (product_signal_id,),
            )
            signal = await cur.fetchone()
        if signal is None:
            raise GuardRejectedError(
                machine="outreach_attempt",
                trigger="dispatch",
                reason=f"Product Signal {product_signal_id} does not exist",
            )
        if as_str(signal, "state") != "awaiting_interview":
            return DispatchOutcome(
                outreach_attempt_id=None,
                invitation_id=None,
                state=as_str(signal, "state"),
                delivered=False,
                reason="the case is no longer awaiting its initial invitation",
            )

        # Read the case's existing outreach under the same row lock. That lock is
        # what makes one initial invitation per case true under concurrency: a
        # second dispatch waits here and then finds the first one's attempt.
        existing = await open_attempt(
            conn,
            product_signal_id=product_signal_id,
            purpose=OutreachPurpose.INITIAL_INVITATION,
        )
        if existing is not None:
            existing_id, existing_state, delivery_command = existing
            if delivery_command is not None or existing_state != "dispatching":
                return DispatchOutcome(
                    outreach_attempt_id=existing_id,
                    invitation_id=None,
                    state=existing_state,
                    delivered=delivery_command is not None,
                    reason="this case already dispatched its initial invitation",
                )
            command_id = await dispatched_command(
                conn, outreach_attempt_id=existing_id
            )
            raise UncertainDeliveryError(
                outreach_attempt_id=existing_id,
                command_id=command_id if command_id is not None else existing_id,
            )

        recipient: Recipient = await load_recipient(
        conn, product_signal_id=product_signal_id
    )
        if recipient.do_not_contact:
            await cancel_case(
                conn,
                client_id=client_id,
                product_signal_id=product_signal_id,
                reason=DO_NOT_CONTACT,
                outreach_attempt_id=None,
                attempt_trigger=None,
            )
            return DispatchOutcome(
                outreach_attempt_id=None,
                invitation_id=None,
                state="canceled",
                delivered=False,
                reason=DO_NOT_CONTACT,
            )

        allowance = await contact_allowance(
            conn,
            client_id=client_id,
            client_user_id=recipient.client_user_id,
            budget=policy.contact_budget,
        )
        if allowance.exhausted():
            await cancel_case(
                conn,
                client_id=client_id,
                product_signal_id=product_signal_id,
                reason=CONTACT_BUDGET_EXHAUSTED,
                outreach_attempt_id=None,
                attempt_trigger=None,
            )
            return DispatchOutcome(
                outreach_attempt_id=None,
                invitation_id=None,
                state="canceled",
                delivered=False,
                reason=CONTACT_BUDGET_EXHAUSTED,
            )

        terms: OfferTerms | None = None
        offer_id: UUID | None = None
        if policy.outreach_offers.enabled:
            reserved = await reserve_offer(
                conn,
                client_id=client_id,
                product_signal_id=product_signal_id,
                customer_account_id=recipient.customer_account_id,
                policy=policy.outreach_offers,
                billing_customer_ref=await billing_customer_ref(
                    conn,
                    client_id=client_id,
                    customer_account_id=recipient.customer_account_id,
                ),
                billing_mapping_revision=release.release_number,
            )
            offer_id = reserved.outreach_offer_id
            terms = OfferTerms(
                currency=reserved.currency,
                amount_minor_units=reserved.amount_minor_units,
                organization_name=recipient.customer_account_name,
                billing_use=policy.outreach_offers.billing_use,
                conditions=policy.outreach_offers.completion_terms,
            )

        now = datetime.now(tz=UTC)
        ttl = policy.contact_budget.invitation_ttl_seconds
        attempt_id = uuid7()
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.outreach_attempts (
                    id, client_id, product_signal_id, client_user_id, purpose, state,
                    outreach_offer_id, expires_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    attempt_id,
                    client_id,
                    product_signal_id,
                    recipient.client_user_id,
                    OutreachPurpose.INITIAL_INVITATION.value,
                    OutreachAttemptState.SCHEDULED.value,
                    offer_id,
                    now + timedelta(seconds=ttl),
                ),
            )
        invitation = await create_invitation(
            conn,
            client_id=client_id,
            outreach_attempt_id=attempt_id,
            product_signal_id=product_signal_id,
            client_user_id=recipient.client_user_id,
            bound_provider=recipient.sign_in_provider,
            bound_provider_identity=recipient.sign_in_identity,
            issued_at=now,
            ttl_seconds=ttl,
        )
        content = invitation_text(
            client_name=await client_name(conn, client_id=client_id),
            product_name=await product_name(conn, client_id=client_id),
            concern_excerpt=await concern_excerpt(
                conn, product_signal_id=product_signal_id
            ),
            session_link=settings.session_link(invitation.token),
            terms=terms,
            expires_in_hours=ttl // _SECONDS_PER_HOUR,
        )
        command = send_command(
            outreach_attempt_id=attempt_id,
            recipient_provider_identity=recipient.provider_user_id,
            cancellation_scope_id=as_uuid(signal, "cancellation_scope_id"),
        )
        await apply_transition_on_connection(
            conn,
            attempt_transition(
                client_id=client_id,
                product_signal_id=product_signal_id,
                outreach_attempt_id=attempt_id,
                trigger="dispatch",
                current_state=OutreachAttemptState.SCHEDULED.value,
                expected_revision=0,
                facts=OutreachAttemptFacts(
                    bound_identity_matches=True,
                    expired=False,
                    contact_budget_remaining=allowance.remaining,
                ),
                mutation={"dispatched_at": now},
                event_type="outreach_attempt_dispatching",
                payload={
                    "invitation_id": str(invitation.invitation_id),
                    "purpose": OutreachPurpose.INITIAL_INVITATION.value,
                    "outreach_offer_id": str(offer_id) if offer_id else None,
                },
                reason_code=None,
                pinned_configuration_release_id=release.id,
                jobs=(
                    expire_initial_invitation_job(
                        client_id=client_id,
                        product_signal_id=product_signal_id,
                        outreach_attempt_id=attempt_id,
                        expires_at=now + timedelta(seconds=ttl),
                        cancellation_scope_id=as_uuid(
                            signal, "cancellation_scope_id"
                        ),
                    ),
                ),
                commands=(command,),
            ),
        )
    return Prepared(
        outreach_attempt_id=attempt_id,
        command_id=command.command_id,
        invitation_id=invitation.invitation_id,
        recipient_provider_identity=recipient.provider_user_id,
        content=content,
    )


async def send_initial_invitation(
    database: Database,
    surface: CustomerSurfacePort,
    settings: IntakeOutreachSettings,
    *,
    client_id: UUID,
    product_signal_id: UUID,
) -> DispatchOutcome:
    """Reserve the offer and deliver one private initial invitation."""
    release = await load_active_release(database, client_id=client_id)
    prepared = await _prepare(
        database,
        settings,
        client_id=client_id,
        product_signal_id=product_signal_id,
        release=release,
    )
    if isinstance(prepared, DispatchOutcome):
        return prepared

    failure: ConnectorFailure | None = None
    provider_ref: str | None = None
    try:
        receipt = await surface.send_direct_message(
            user_id=prepared.recipient_provider_identity, content=prepared.content
        )
        provider_ref = receipt.message_id
    except ConnectorFailure as error:
        failure = error

    outcome = await conclude(
        database,
        client_id=client_id,
        product_signal_id=product_signal_id,
        prepared=prepared,
        release=release,
        provider_message_ref=provider_ref,
        failure=failure,
    )
    _log.info(
        "initial_invitation_dispatched",
        client_id=str(client_id),
        product_signal_id=str(product_signal_id),
        outreach_attempt_id=str(outcome.outreach_attempt_id),
        delivered=outcome.delivered,
        state=outcome.state,
    )
    if outcome.delivered:
        await _acknowledge_in_channel(
            database, surface, client_id=client_id, product_signal_id=product_signal_id
        )
    return outcome


async def _acknowledge_in_channel(
    database: Database,
    surface: CustomerSurfacePort,
    *,
    client_id: UUID,
    product_signal_id: UUID,
) -> None:
    """Reply to the reporter's latest message in the request that opened the case."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select messages.channel_ref, messages.provider_message_id
            from public.product_signals as signals
            join public.intake_batches as batches
              on batches.intake_item_id = signals.intake_item_id
            join public.intake_messages as messages
              on messages.intake_batch_id = batches.id
            where signals.id = %s
            order by messages.ordinal desc
            limit 1
            """,
            (product_signal_id,),
        )
        source = await cur.fetchone()
    if source is None:
        _log.info(
            "initial_invitation_not_acknowledged",
            client_id=str(client_id),
            product_signal_id=str(product_signal_id),
            reason="the case did not start from a channel message",
        )
        return
    try:
        await surface.reply_in_channel(
            channel_id=as_str(source, "channel_ref"),
            message_id=as_str(source, "provider_message_id"),
            content=_CHANNEL_ACKNOWLEDGEMENT,
        )
    except ConnectorFailure as failure:
        _log.warning(
            "initial_invitation_acknowledgement_failed",
            client_id=str(client_id),
            product_signal_id=str(product_signal_id),
            category=failure.category.value,
            detail=str(failure),
        )
