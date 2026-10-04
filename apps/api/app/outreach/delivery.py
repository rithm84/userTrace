"""The send Command, its durable delivery Result, and the attempt's conclusion.

A provider send cannot be undone and Discord exposes no idempotency key for one,
so the Command id is the idempotency boundary and an uncertain send is
reconciled rather than repeated. A delivery is a durable result, never Product
Signal state, and never proof that the person read the message.

Contract: docs/arch-components/signal-and-outreach.md#originating-user-and-invitation-lifecycle
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    AggregateKind,
    CommandDisposition,
    EffectState,
    OutreachAttemptState,
    OutreachPurpose,
)

from app.clients.configuration import ActiveRelease
from app.clients.provider_map import PROVIDER_DISCORD
from app.connectors.failures import ConnectorFailure
from app.db.kernel import apply_transition_on_connection
from app.db.pool import Database
from app.db.rows import as_str, as_uuid
from app.envelope import CommandIntent, JsonValue, StreamRef
from app.errors import GuardRejectedError
from app.ids import uuid7
from app.lifecycle.outreach import OutreachAttemptFacts
from app.outreach.attempt_machine import attempt_transition, load_attempt_state
from app.outreach.cancellation import cancel_case
from app.outreach.reasons import UNREACHABLE

__all__ = [
    "SEND_COMMAND",
    "DispatchOutcome",
    "Prepared",
    "UncertainDeliveryError",
    "conclude",
    "dispatched_command",
    "open_attempt",
    "record_delivery",
    "record_send_result",
    "send_command",
]

SEND_COMMAND: Final[str] = "outreach.send_direct_message"


class UncertainDeliveryError(GuardRejectedError):
    """A send was dispatched but no delivery was recorded; resending is refused.

    Discord exposes no idempotency key for a message send, so the attempt is
    reconciled by reading the direct-message channel before any resend rather
    than delivering a second invitation.
    """

    def __init__(self, outreach_attempt_id: UUID, command_id: UUID) -> None:
        super().__init__(
            machine="outreach_attempt",
            trigger="dispatch",
            reason=(
                f"attempt {outreach_attempt_id} dispatched command {command_id} with "
                "no recorded delivery; reconcile the direct-message channel before "
                "any resend"
            ),
        )
        self.outreach_attempt_id = outreach_attempt_id
        self.command_id = command_id


@dataclass(frozen=True, slots=True)
class Prepared:
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


def send_command(
    *,
    outreach_attempt_id: UUID,
    recipient_provider_identity: str,
    cancellation_scope_id: UUID,
) -> CommandIntent:
    """Build the single-purpose send Command whose id is its idempotency key."""
    return CommandIntent(
        command_id=uuid7(),
        command_type=SEND_COMMAND,
        target=StreamRef(
            kind=AggregateKind.OUTREACH_ATTEMPT, id=outreach_attempt_id
        ),
        payload={"recipient_provider_identity": recipient_provider_identity},
        declared_revisions={},
        cancellation_scope_id=cancellation_scope_id,
    )


async def record_send_result(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    command_id: UUID,
    provider_message_ref: str | None,
    effect_state: EffectState,
    disposition: CommandDisposition,
    failure_category: str | None,
    detail: JsonValue,
) -> None:
    """Record the terminal Result of one send Command and close the Command."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.results (
                id, client_id, command_id, kind, disposition, effect_state,
                technical_category, payload
            )
            values (%s, %s, %s, 'terminal', %s, %s, %s, %s)
            """,
            (
                uuid7(),
                client_id,
                command_id,
                disposition.value,
                effect_state.value,
                failure_category,
                Jsonb(detail),
            ),
        )
        await cur.execute(
            """
            update public.commands
            set state = 'terminal', terminal_at = now(), provider_ref = %s,
                revision = revision + 1, updated_at = now()
            where id = %s
            """,
            (provider_message_ref, command_id),
        )


async def record_delivery(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    outreach_attempt_id: UUID,
    command_id: UUID,
    recipient_provider_identity: str,
    provider_message_ref: str | None,
    effect_state: EffectState,
    disposition: CommandDisposition,
    failure_category: str | None,
    detail: JsonValue,
) -> None:
    """Record the durable delivery Result for one send Command.

    Provider sends are delivery results, never Product Signal state, and never
    proof that the person read the message.
    """
    delivered_at = (
        datetime.now(tz=UTC) if effect_state is EffectState.COMPLETED else None
    )
    await record_send_result(
        conn,
        client_id=client_id,
        command_id=command_id,
        provider_message_ref=provider_message_ref,
        effect_state=effect_state,
        disposition=disposition,
        failure_category=failure_category,
        detail=detail,
    )
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.outreach_deliveries (
                id, client_id, outreach_attempt_id, command_id, provider,
                recipient_provider_identity, provider_message_ref, effect_state,
                disposition, failure_category, detail, delivered_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                uuid7(),
                client_id,
                outreach_attempt_id,
                command_id,
                PROVIDER_DISCORD,
                recipient_provider_identity,
                provider_message_ref,
                effect_state.value,
                disposition.value,
                failure_category,
                Jsonb(detail),
                delivered_at,
            ),
        )


async def open_attempt(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID, purpose: OutreachPurpose
) -> tuple[UUID, str, UUID | None] | None:
    """Return an existing attempt of one purpose, with its state and command."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select attempts.id, attempts.state, deliveries.command_id
            from public.outreach_attempts as attempts
            left join public.outreach_deliveries as deliveries
              on deliveries.outreach_attempt_id = attempts.id
            where attempts.product_signal_id = %s and attempts.purpose = %s
            order by attempts.created_at desc limit 1
            """,
            (product_signal_id, purpose.value),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    command_id = row["command_id"]
    return (
        as_uuid(row, "id"),
        as_str(row, "state"),
        UUID(str(command_id)) if command_id is not None else None,
    )


async def dispatched_command(
    conn: AsyncConnection[DictRow], *, outreach_attempt_id: UUID
) -> UUID | None:
    """Return the send Command already committed for one attempt."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.commands
            where command_type = %s and target_kind = 'outreach_attempt'
              and target_id = %s
            order by created_at desc limit 1
            """,
            (SEND_COMMAND, outreach_attempt_id),
        )
        row = await cur.fetchone()
    return as_uuid(row, "id") if row is not None else None



async def conclude(
    database: Database,
    *,
    client_id: UUID,
    product_signal_id: UUID,
    prepared: Prepared,
    release: ActiveRelease,
    provider_message_ref: str | None,
    failure: ConnectorFailure | None,
) -> DispatchOutcome:
    """Record the delivery Result and move the attempt to its next state."""
    async with database.transaction() as conn:
        if failure is None:
            await record_delivery(
                conn,
                client_id=client_id,
                outreach_attempt_id=prepared.outreach_attempt_id,
                command_id=prepared.command_id,
                recipient_provider_identity=prepared.recipient_provider_identity,
                provider_message_ref=provider_message_ref,
                effect_state=EffectState.COMPLETED,
                disposition=CommandDisposition.SUCCEEDED,
                failure_category=None,
                detail={"provider_message_ref": provider_message_ref},
            )
            state, revision = await load_attempt_state(
                conn, outreach_attempt_id=prepared.outreach_attempt_id
            )
            await apply_transition_on_connection(
                conn,
                attempt_transition(
                    client_id=client_id,
                    product_signal_id=product_signal_id,
                    outreach_attempt_id=prepared.outreach_attempt_id,
                    trigger="delivery_confirmed",
                    current_state=state,
                    expected_revision=revision,
                    facts=OutreachAttemptFacts(
                        bound_identity_matches=True,
                        expired=False,
                        contact_budget_remaining=0,
                    ),
                    mutation={"delivery_provider_ref": provider_message_ref},
                    event_type="outreach_attempt_delivered",
                    payload={"provider_message_ref": provider_message_ref},
                    reason_code=None,
                    pinned_configuration_release_id=release.id,
                    jobs=(),
                    commands=(),
                ),
            )
            return DispatchOutcome(
                outreach_attempt_id=prepared.outreach_attempt_id,
                invitation_id=prepared.invitation_id,
                state=OutreachAttemptState.AWAITING_RESPONSE.value,
                delivered=True,
                reason="delivered",
            )

        await record_delivery(
            conn,
            client_id=client_id,
            outreach_attempt_id=prepared.outreach_attempt_id,
            command_id=prepared.command_id,
            recipient_provider_identity=prepared.recipient_provider_identity,
            provider_message_ref=None,
            effect_state=EffectState.ABSENT,
            disposition=CommandDisposition.FAILED,
            failure_category=failure.category.value,
            detail={"message": failure.detail.message},
        )
        await cancel_case(
            conn,
            client_id=client_id,
            product_signal_id=product_signal_id,
            reason=UNREACHABLE,
            outreach_attempt_id=prepared.outreach_attempt_id,
            attempt_trigger="delivery_failed",
        )
    return DispatchOutcome(
        outreach_attempt_id=prepared.outreach_attempt_id,
        invitation_id=prepared.invitation_id,
        state=OutreachAttemptState.UNREACHED.value,
        delivered=False,
        reason=UNREACHABLE,
    )


