"""What a committed Product Signal says, as people read it.

The commit runs against the real database from a sealed Intake Item. The
decision model's judgement is not what this proves, so the test commits the
create outcome directly, as routing does once the model's answers pass their
checks.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.connectors.ports import MessageReceipt
from app.db.pool import Database
from app.db.rows import as_str
from app.ids import uuid7
from app.intake.commit import commit_product_signal
from app.intake.context import assemble_context, load_scope
from tests.supabase_auth import SignedInUser
from tests.workstream import create_workstream_client, ingest_receipt

CANONICAL_REQUEST = (
    "I keep rebuilding the same conversion filters every Monday. Can Shopalytics let "
    "me save a view for TikTok traffic from women 25-34 on products with negative "
    "sizing reviews, then reopen it later?"
)


async def test_the_concern_quotes_the_reporter_in_the_named_channel(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The model reads `[n]` message markers; the stored wording carries none."""
    participant = auth_users["participant"]
    fixture = await create_workstream_client(
        database,
        owner_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_sign_in=(
            participant.identity_provider,
            participant.identity_provider_id,
        ),
        offers_enabled=False,
    )
    item_id = await ingest_receipt(
        database,
        fixture,
        receipt=MessageReceipt(
            provider="discord",
            message_id=str(uuid7().int),
            channel_id=fixture.channel_id,
            guild_id=fixture.guild_id,
            author_id=fixture.maya.discord_user_id,
            author_is_bot=False,
            content=CANONICAL_REQUEST,
            created_at=datetime.now(tz=UTC),
            edited_at=None,
            reply_to_message_id=None,
            thread_id=None,
        ),
    )
    context = await assemble_context(
        database,
        intake_item_id=item_id,
        release=fixture.release,
        overview=fixture.overview,
        scope=await load_scope(database, intake_item_id=item_id),
    )
    assert context.batch_text() == f"[1] {CANONICAL_REQUEST}"

    async with database.transaction() as conn:
        committed = await commit_product_signal(
            conn,
            context=context,
            attempt_index=1,
            decision_model_id="commit-test",
            questions={},
            answers={},
            acceptance={},
        )
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select concerns.pain_point, concerns.validation_criterion,
                   reports.source_excerpt
            from public.product_signal_concerns as concerns
            join public.source_reports as reports
              on reports.product_signal_id = concerns.product_signal_id
            where concerns.product_signal_id = %s
            """,
            (committed.product_signal_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert as_str(row, "pain_point") == (
        f"Reported in #feature-requests by the originating user: {CANONICAL_REQUEST}"
    )
    assert as_str(row, "validation_criterion").endswith(f": {CANONICAL_REQUEST}")
    assert as_str(row, "source_excerpt") == CANONICAL_REQUEST
