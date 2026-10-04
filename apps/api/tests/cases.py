"""Case fixtures for the outreach and claim tests.

These write exactly what the routing commit path writes, so an outreach test
starts from a real routed case without paying for another routing run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    FlowType,
    IntakeItemState,
    OutreachAttemptState,
    OutreachPurpose,
    ProductSignalState,
)

from app.db.pool import Database
from app.ids import uuid7
from app.intake.concerns import insert_concern
from app.outreach.invitations import create_invitation
from tests.workstream import Participant, WorkstreamFixture

__all__ = ["seed_awaiting_invitation", "seed_product_signal"]


async def seed_product_signal(
    database: Database,
    fixture: WorkstreamFixture,
    *,
    reporter: Participant,
    excerpt: str,
) -> UUID:
    """Insert one routed Product Signal with its originating report and concern.

    Outreach tests need a case, not another routing run, so this writes exactly
    what the commit path writes: the signal, the originating source report, and
    the routing concern revision.
    """
    intake_id = uuid7()
    signal_id = uuid7()
    now = datetime.now(tz=UTC)
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.intake_items (
                    id, client_id, state, source_provider, source_channel_ref,
                    source_batch_digest, author_provider_identity, client_user_id,
                    customer_account_id, payload, routing_outcome, product_signal_id
                )
                values (%s, %s, %s, 'discord', %s, %s, %s, %s, %s, %s,
                        'create_product_signal', null)
                """,
                (
                    intake_id,
                    fixture.client_id,
                    IntakeItemState.ROUTED.value,
                    fixture.channel_id,
                    f"sha256:{uuid7().hex}",
                    reporter.discord_user_id,
                    reporter.client_user_id,
                    reporter.customer_account_id,
                    Jsonb({"messages": [{"ordinal": 1, "content": excerpt}]}),
                ),
            )
            await cur.execute(
                """
                insert into public.product_signals (
                    id, client_id, state, intake_item_id,
                    pinned_configuration_release_id, originating_client_user_id,
                    originating_customer_account_id, initial_flow_type,
                    cancellation_scope_id
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    signal_id,
                    fixture.client_id,
                    ProductSignalState.AWAITING_INTERVIEW.value,
                    intake_id,
                    fixture.release.id,
                    reporter.client_user_id,
                    reporter.customer_account_id,
                    FlowType.FEATURE_REQUEST_FLOW.value,
                    uuid7(),
                ),
            )
            await cur.execute(
                "update public.intake_items set product_signal_id = %s where id = %s",
                (signal_id, intake_id),
            )
            await cur.execute(
                """
                insert into public.source_reports (
                    id, client_id, product_signal_id, intake_item_id,
                    reporting_client_user_id, reporting_customer_account_id,
                    source_excerpt, occurred_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid7(),
                    fixture.client_id,
                    signal_id,
                    intake_id,
                    reporter.client_user_id,
                    reporter.customer_account_id,
                    excerpt,
                    now,
                ),
            )
        await insert_concern(
            conn,
            client_id=fixture.client_id,
            product_signal_id=signal_id,
            concern_revision=0,
            origin="routing_inference",
            pain_point=excerpt,
            validation_criterion=f"the reporter can accomplish: {excerpt}",
            user_wording={"messages": [excerpt]},
            unknowns={"impact": "unknown"},
        )
    return signal_id


async def seed_awaiting_invitation(
    database: Database,
    fixture: WorkstreamFixture,
    *,
    product_signal_id: UUID,
    participant: Participant,
    bound_provider: str,
    bound_identity: str,
) -> tuple[UUID, str]:
    """Insert one delivered Outreach Attempt and its live invitation token."""
    attempt_id = uuid7()
    now = datetime.now(tz=UTC)
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.outreach_attempts (
                    id, client_id, product_signal_id, client_user_id, purpose, state,
                    dispatched_at, expires_at
                )
                values (%s, %s, %s, %s, %s, %s, now(), now() + interval '72 hours')
                """,
                (
                    attempt_id,
                    fixture.client_id,
                    product_signal_id,
                    participant.client_user_id,
                    OutreachPurpose.INITIAL_INVITATION.value,
                    OutreachAttemptState.AWAITING_RESPONSE.value,
                ),
            )
        invitation = await create_invitation(
            conn,
            client_id=fixture.client_id,
            outreach_attempt_id=attempt_id,
            product_signal_id=product_signal_id,
            client_user_id=participant.client_user_id,
            bound_provider=bound_provider,
            bound_provider_identity=bound_identity,
            issued_at=now,
            ttl_seconds=72 * 3600,
        )
    return attempt_id, invitation.token
