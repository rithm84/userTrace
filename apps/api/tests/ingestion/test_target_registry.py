"""What the target registry says a document currently shows."""

from __future__ import annotations

from utrace_contracts.domain.lifecycle import InterviewPhase, ProductSignalState

from app.db.pool import Database
from app.ids import uuid7
from app.ingestion.target_registry import load_registered_targets
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


def _target(target_id: str, revision: int, description: str) -> RegisteredTarget:
    return RegisteredTarget(
        target_id=target_id,
        target_revision=revision,
        description=description,
        placement="top left of the screen",
        role="button",
        visible=True,
    )


async def test_each_target_is_what_its_newest_change_says(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Later changes replace earlier ones, an unmount removes, and a rejection changes nothing."""
    case = await create_case(
        database,
        name="target-registry",
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    allocation_id = await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )
    document = await observe_document(
        database,
        case=case,
        interview_session_id=session_id,
        preview_runtime_allocation_id=allocation_id,
        document_attachment_id=uuid7(),
        targets=(_target("channel", 1, "All"), _target("segment", 1, "All")),
    )
    for sequence, label in enumerate(("TikTok", "Instagram", "TikTok"), start=2):
        await record_registry_change(
            database,
            document=document,
            export_sequence=sequence,
            change="updated",
            target=_target("channel", sequence, label),
        )
    await record_registry_change(
        database,
        document=document,
        export_sequence=5,
        change="unmounted",
        target=_target("segment", 1, "All"),
    )
    await record_observation(
        database,
        document=document,
        export_sequence=6,
        observation_type="target.registry_change",
        target=None,
        payload={"change": "rejected", "registry_revision": 6},
    )

    targets = await load_registered_targets(
        database,
        client_id=case.client_id,
        interview_session_id=session_id,
        document_attachment_id=document.document_attachment_id,
    )

    assert [(t.target_id, t.target_revision, t.description) for t in targets] == [
        ("channel", 4, "TikTok")
    ]
