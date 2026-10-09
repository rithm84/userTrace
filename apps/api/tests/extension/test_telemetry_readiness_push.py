"""Telemetry readiness reaches the combined record, the session tab, and the extension.

Ingestion publishes browser, server, and correlation readiness from its own
Jobs. The combined record every reader consults is recombined from those
projections by the same Job, which republishes the session projection, and the
record's commit wakes the extension's control socket. The running application
reads readiness through its real ingestion adapter here, not a stand-in.
"""


from fastapi import FastAPI

from app.db.pool import Database
from app.ids import uuid7
from app.ingestion.jobs import refresh_session_readiness
from app.ingestion.signing import Signer
from app.interview.ports import RegisteredTarget
from app.interview.readiness import load_readiness
from tests.extension.harness import (
    await_disconnect,
    bind_document,
    connect,
    last_of,
    prepare_session,
)
from tests.interview.fixtures import observe_document, record_ready_page
from tests.supabase_auth import SignedInUser


async def test_telemetry_readiness_reaches_the_record_the_tab_and_the_extension(
    database: Database,
    running_api: FastAPI,
    page_proof_signer: Signer,
    auth_users: dict[str, SignedInUser],
) -> None:
    prepared = await prepare_session(database, auth_users, "telemetry-readiness-push")
    attachment_id = uuid7()
    document = await observe_document(
        database,
        case=prepared.case,
        interview_session_id=prepared.session_id,
        preview_runtime_allocation_id=prepared.allocation_id,
        document_attachment_id=attachment_id,
        targets=(
            RegisteredTarget(
                target_id="save-view-button",
                target_revision=1,
                description="Save this view",
                placement="top left of the screen",
                role="button",
                visible=True,
            ),
        ),
    )

    async with connect(
        device=prepared.device, installation_id=prepared.installation_id
    ) as client:
        bound = await bind_document(
            client,
            database=database,
            prepared=prepared,
            signer=page_proof_signer,
            document_attachment_id=attachment_id,
        )
        blocked = last_of(bound, "readiness")
        assert blocked["payload"]["combined_readiness"] == "blocked"
        assert "browser_sdk_unavailable" in blocked["payload"]["blockers"]

        await record_ready_page(database, case=prepared.case, document=document)
        await refresh_session_readiness(
            database,
            client_id=prepared.case.client_id,
            activation_id=document.activation_id,
        )
        frames = await client.until(
            lambda frames: any(
                frame["kind"] == "readiness"
                and frame["payload"]["combined_readiness"] == "ready"
                for frame in frames
            )
        )

    pushed = last_of(frames, "readiness")
    assert pushed["payload"]["blockers"] == []
    assert pushed["payload"]["extension_readiness"] == "ready"
    record = await load_readiness(database, interview_session_id=prepared.session_id)
    assert record.observation is True
    assert record.readiness_revision == pushed["payload"]["readiness_revision"]
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select projection from public.session_projections where session_id = %s",
            (prepared.session_id,),
        )
        projection = await cur.fetchone()
    assert projection is not None
    assert projection["projection"]["readiness"]["observation"] is True

    # A lost control connection withdrew the extension half; a healthy
    # telemetry path afterwards cannot restore it.
    await await_disconnect(database, installation_id=prepared.installation_id)
    await record_ready_page(database, case=prepared.case, document=document)
    await refresh_session_readiness(
        database,
        client_id=prepared.case.client_id,
        activation_id=document.activation_id,
    )
    after_loss = await load_readiness(database, interview_session_id=prepared.session_id)
    assert after_loss.observation is False
    assert after_loss.components["browser_sdk_ready"] is True
    assert "extension_readiness_withdrawn" in after_loss.blockers
