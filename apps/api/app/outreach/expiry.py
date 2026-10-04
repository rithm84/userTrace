"""Terminal expiry of an initial invitation.

An initial invitation that reaches its pinned response deadline without a claim
follows the cancellation path: the attempt expires, the Product Signal is
canceled, outstanding invitations are revoked, and the unearned reservation
returns its allowance. No Case File is produced.

A claimed, declined, or already terminal attempt is left alone, so a late
expiry Job cannot undo a real response.

Contract: docs/arch-components/signal-and-outreach.md#originating-user-and-invitation-lifecycle
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.db.pool import Database
from app.db.rows import as_datetime, as_str, as_uuid
from app.errors import AggregateNotFoundError
from app.logging import get_logger
from app.outreach.cancellation import cancel_case
from app.outreach.reasons import INVITATION_EXPIRED

__all__ = ["ExpiryOutcome", "expire_initial_invitation"]

_log = get_logger(__name__)

_OPEN_STATES = frozenset({"scheduled", "dispatching", "awaiting_response"})


@dataclass(frozen=True, slots=True)
class ExpiryOutcome:
    """What the expiry Job decided for one Outreach Attempt."""

    outreach_attempt_id: UUID
    expired: bool
    reason: str
    released_minor_units: int


async def expire_initial_invitation(
    database: Database, *, outreach_attempt_id: UUID
) -> ExpiryOutcome:
    """Expire one unanswered initial invitation and cancel its case."""
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select client_id, product_signal_id, state, expires_at
                from public.outreach_attempts where id = %s for update
                """,
                (outreach_attempt_id,),
            )
            attempt = await cur.fetchone()
        if attempt is None:
            raise AggregateNotFoundError(
                table="outreach_attempts", row_id=outreach_attempt_id
            )
        state = as_str(attempt, "state")
        if state not in _OPEN_STATES:
            return ExpiryOutcome(
                outreach_attempt_id=outreach_attempt_id,
                expired=False,
                reason=f"the attempt is already '{state}'",
                released_minor_units=0,
            )
        if as_datetime(attempt, "expires_at") > datetime.now(tz=UTC):
            return ExpiryOutcome(
                outreach_attempt_id=outreach_attempt_id,
                expired=False,
                reason="the response deadline has not passed",
                released_minor_units=0,
            )
        canceled = await cancel_case(
            conn,
            client_id=as_uuid(attempt, "client_id"),
            product_signal_id=as_uuid(attempt, "product_signal_id"),
            reason=INVITATION_EXPIRED,
            outreach_attempt_id=outreach_attempt_id,
            attempt_trigger="expire",
        )
    _log.info(
        "initial_invitation_expired",
        outreach_attempt_id=str(outreach_attempt_id),
        product_signal_id=str(canceled.product_signal_id),
        released_minor_units=canceled.released_minor_units,
    )
    return ExpiryOutcome(
        outreach_attempt_id=outreach_attempt_id,
        expired=True,
        reason=INVITATION_EXPIRED,
        released_minor_units=canceled.released_minor_units,
    )
