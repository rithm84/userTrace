"""Cancelling a case whose initial outreach cannot complete.

A decline, an opt-out, a do-not-contact request, an inability to reach the same
user, an expired invitation, and an exhausted contact budget all end the same
way for the initial invitation: the Product Signal is canceled, no Case File is
produced, outstanding invitations are revoked, and the unearned reservation
returns its allowance.

The recorded reason, not the lifecycle state, is what a later linked successor
Product Signal is judged against.

Contract: docs/arch-components/signal-and-outreach.md#originating-user-and-invitation-lifecycle
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import ActorKind, AggregateKind

from app.db.kernel import apply_transition_on_connection
from app.db.rows import as_int, as_str
from app.envelope import Actor, AuditIntent, EventAppend, StreamRef
from app.errors import AggregateNotFoundError
from app.ids import uuid7
from app.lifecycle.machine import plan_transition
from app.lifecycle.outreach import OutreachAttemptFacts
from app.lifecycle.product_signal import PRODUCT_SIGNAL_MACHINE, ProductSignalFacts
from app.logging import get_logger
from app.outreach.attempt_machine import attempt_transition, load_attempt_state
from app.outreach.invitations import revoke_invitations
from app.outreach.offers import release_reservation

__all__ = ["CanceledCase", "cancel_case"]

_log = get_logger(__name__)
_ACTOR = Actor(kind=ActorKind.SERVICE, ref="outreach.controller")


@dataclass(frozen=True, slots=True)
class CanceledCase:
    """What one cancellation released and revoked."""

    product_signal_id: UUID
    reason: str
    released_minor_units: int
    revoked_invitations: int
    attempt_state: str | None


async def _terminate_attempt(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    product_signal_id: UUID,
    outreach_attempt_id: UUID,
    trigger: str,
    reason: str,
    pinned_configuration_release_id: UUID | None,
) -> str | None:
    """Move one open Outreach Attempt to its terminal state, if it is still open."""
    state, revision = await load_attempt_state(
        conn, outreach_attempt_id=outreach_attempt_id
    )
    terminal = {
        "claimed",
        "declined",
        "expired",
        "unreached",
        "revoked",
    }
    if state in terminal:
        return state
    outcome = await apply_transition_on_connection(
        conn,
        attempt_transition(
            client_id=client_id,
            product_signal_id=product_signal_id,
            outreach_attempt_id=outreach_attempt_id,
            trigger=trigger,
            current_state=state,
            expected_revision=revision,
            facts=OutreachAttemptFacts(
                bound_identity_matches=False, expired=True, contact_budget_remaining=0
            ),
            mutation={"reason": reason, "responded_at": datetime.now(tz=UTC)},
            event_type=f"outreach_attempt_{trigger}",
            payload={"reason": reason},
            reason_code=reason,
            pinned_configuration_release_id=pinned_configuration_release_id,
            jobs=(),
            commands=(),
        ),
    )
    return outcome.to_state


async def cancel_case(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    product_signal_id: UUID,
    reason: str,
    outreach_attempt_id: UUID | None,
    attempt_trigger: str | None,
) -> CanceledCase:
    """Cancel one case whose initial outreach cannot complete.

    Runs inside the caller's transaction so the release, the revocation, and the
    terminal Product Signal state commit together.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select state, revision, pinned_configuration_release_id
            from public.product_signals where id = %s for update
            """,
            (product_signal_id,),
        )
        signal = await cur.fetchone()
    if signal is None:
        raise AggregateNotFoundError(table="product_signals", row_id=product_signal_id)

    release_id = signal["pinned_configuration_release_id"]
    attempt_state: str | None = None
    if outreach_attempt_id is not None and attempt_trigger is not None:
        attempt_state = await _terminate_attempt(
            conn,
            client_id=client_id,
            product_signal_id=product_signal_id,
            outreach_attempt_id=outreach_attempt_id,
            trigger=attempt_trigger,
            reason=reason,
            pinned_configuration_release_id=(
                UUID(str(release_id)) if release_id is not None else None
            ),
        )

    revoked = await revoke_invitations(
        conn, product_signal_id=product_signal_id, reason=reason
    )
    released = await release_reservation(
        conn, client_id=client_id, product_signal_id=product_signal_id, reason=reason
    )

    state = as_str(signal, "state")
    if state != "canceled":
        stream = StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=product_signal_id)
        await apply_transition_on_connection(
            conn,
            plan_transition(
                PRODUCT_SIGNAL_MACHINE,
                ProductSignalFacts(
                    participation_occurred=False,
                    case_file_published=False,
                ),
                trigger="cancel",
                current_state=state,
                client_id=client_id,
                aggregate_kind=AggregateKind.PRODUCT_SIGNAL,
                aggregate_id=product_signal_id,
                expected_revision=as_int(signal, "revision"),
                state_column="state",
                mutation={"closure_reason": reason},
                event=EventAppend(
                    message_id=uuid7(),
                    stream=stream,
                    event_type="product_signal_canceled",
                    payload={
                        "reason": reason,
                        "released_minor_units": released,
                        "revoked_invitations": revoked,
                    },
                    actor=_ACTOR,
                    causation_id=outreach_attempt_id,
                    correlation_id=product_signal_id,
                    occurred_at=datetime.now(tz=UTC),
                ),
                jobs=(),
                commands=(),
                audit=AuditIntent(
                    audit_id=uuid7(),
                    subject=stream,
                    action="cancel_case",
                    outcome="canceled",
                    reason_code=reason,
                    actor=_ACTOR,
                    pinned_configuration_release_id=(
                        UUID(str(release_id)) if release_id is not None else None
                    ),
                    detail={"released_minor_units": released},
                ),
                additional_locks=(),
            ),
        )
    _log.info(
        "case_canceled",
        client_id=str(client_id),
        product_signal_id=str(product_signal_id),
        reason=reason,
        released_minor_units=released,
        revoked_invitations=revoked,
    )
    return CanceledCase(
        product_signal_id=product_signal_id,
        reason=reason,
        released_minor_units=released,
        revoked_invitations=revoked,
        attempt_state=attempt_state,
    )
