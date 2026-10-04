"""What the live interview reads about the person's recent preview activity."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from utrace_contracts.domain.lifecycle import InterviewPhase, ProductSignalState

from app.db.pool import Database
from app.ids import uuid7
from app.interview.observations import describe_activity, recent_activity
from app.interview.ports import RegisteredTarget
from tests.factories import create_case
from tests.interview.fixtures import (
    create_live_session,
    create_preview_allocation,
    observe_document,
    record_observation,
    record_registry_change,
)
from tests.supabase_auth import SignedInUser


async def test_the_latest_actions_win_over_runtime_bookkeeping(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A bounded read returns the person's newest actions, not the oldest records."""
    case = await create_case(
        database,
        name="observed-activity",
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    allocation_id = await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )
    channel = RegisteredTarget(
        target_id="channel-filter",
        target_revision=1,
        description="TikTok",
        placement="top left of the screen",
        role="button",
        visible=True,
    )
    document = await observe_document(
        database,
        case=case,
        interview_session_id=session_id,
        preview_runtime_allocation_id=allocation_id,
        document_attachment_id=uuid7(),
        targets=(channel,),
    )
    for sequence in range(2, 12):
        await record_registry_change(
            database, document=document, export_sequence=sequence, change="updated", target=channel
        )
    await record_observation(
        database,
        document=document,
        export_sequence=12,
        observation_type="interaction.activation",
        target=channel,
        payload={"role": "button", "actor": "user", "activation_kind": "pointer"},
    )
    await record_observation(
        database,
        document=document,
        export_sequence=13,
        observation_type="product.milestone",
        target=None,
        payload={
            "milestone_id": "conversion_filters_applied",
            "attributes": [{"name": "channels", "value": {"value": "TikTok"}}],
        },
    )

    activity = await recent_activity(
        database,
        client_id=case.client_id,
        interview_session_id=session_id,
        since=datetime.now(tz=UTC) - timedelta(minutes=1),
        limit=2,
    )

    assert [describe_activity(item) for item in activity] == [
        "clicked TikTok",
        "conversion filters applied (channels TikTok)",
    ]
