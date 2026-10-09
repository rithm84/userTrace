"""The extension control socket, over a real WebSocket against the real API.

The production application runs on a port and the harness in `harness` drives
it as an installed extension would: authenticate, resume the control stream,
bind a document with a real signed page proof, and exchange commands, controls,
and results.
"""

import time
from uuid import UUID

import pytest
from fastapi import FastAPI
from websockets.exceptions import ConnectionClosed

from app.db.pool import Database
from app.extension.installations import ProofOfPossession, revoke_installation
from app.ids import uuid7
from app.ingestion.signing import Signer
from app.interview.ports import RegisteredTarget
from tests.extension.conftest import install_observation_readiness
from tests.extension.harness import (
    INSTALLATION_NOT_LINKED_CLOSE_CODE,
    ORIGIN,
    POLICY_VIOLATION_CLOSE_CODE,
    PROTOCOL_VERSION,
    SEQUENCE_GAP_CLOSE_CODE,
    await_disconnect,
    bind_document,
    binding_offer_payload,
    connect,
    kinds,
    last_of,
    now,
    page_proof,
    prepare_session,
    recovery_payload,
)
from tests.extension.keys import DeviceKey
from tests.extension.pushes import dispatch_point, has_command, post_participant_control
from tests.interview.fixtures import (
    RecordingObservationReadiness,
    complete_readiness,
    observe_document,
)
from tests.supabase_auth import SignedInUser

_SAVE_VIEW = RegisteredTarget(
    target_id="save-view-button",
    target_revision=3,
    description="Save this view",
    placement="top left of the screen",
    role="button",
    visible=True,
)
#: How soon a dispatched gesture must reach the bound tab.
_PROMPT_SECONDS = 2.0


async def test_the_socket_authenticates_resumes_and_binds_one_document(
    database: Database,
    running_api: FastAPI,
    page_proof_signer: Signer,
    auth_users: dict[str, SignedInUser],
) -> None:
    prepared = await prepare_session(database, auth_users, "socket-bind")
    attachment_id = uuid7()
    install_observation_readiness(
        running_api, RecordingObservationReadiness(readiness=complete_readiness())
    )

    async with connect(
        device=prepared.device, installation_id=prepared.installation_id
    ) as client:
        await client.send(
            "recovery_request",
            recovery_payload(installation_id=prepared.installation_id, resume_from=0),
        )
        recovery = await client.receive()
        await client.acknowledge()
        assert recovery["kind"] == "recovery_state"
        assert recovery["sequence"] == 1
        assert recovery["payload"]["disposition"] == "resumed"
        assert recovery["payload"]["next_sequence"] == 2
        assert recovery["payload"]["protocol_version"] == PROTOCOL_VERSION

        first_preferences = (await client.drain(1))[0]
        assert first_preferences["kind"] == "preferences_state"
        assert first_preferences["payload"]["visual_guidance_enabled"] is True

        await client.send(
            "binding_offer",
            await binding_offer_payload(
                database=database,
                prepared=prepared,
                signer=page_proof_signer,
                document_attachment_id=attachment_id,
            ),
        )
        frames = await client.until(
            lambda frames: "character_state" in kinds(frames)
            and "controls_state" in kinds(frames)
            and "readiness" in kinds(frames)
            and last_of(frames, "readiness")["payload"]["combined_readiness"] == "ready"
        )
        await client.synchronize()

    binding = last_of(frames, "binding_state")
    readiness = last_of(frames, "readiness")
    character = last_of(frames, "character_state")
    controls = last_of(frames, "controls_state")
    # The session's own controls, so nothing an earlier session left carries over.
    assert controls["payload"]["session_id"] == str(prepared.session_id)
    assert controls["payload"]["mute_state"] == "unmuted"
    assert controls["payload"]["pause_state"] == "running"
    assert kinds(frames).count("controls_state") == 1
    # Preferences did not change, so the projection does not send them again.
    assert "preferences_state" not in kinds(frames)
    assert binding["payload"]["status"] == "bound"
    assert binding["payload"]["session_id"] == str(prepared.session_id)
    assert binding["payload"]["preview_runtime_allocation_id"] == str(
        prepared.allocation_id
    )
    # The binding adopts the attachment the signed proof names, never a new one.
    assert binding["payload"]["document_attachment_id"] == str(attachment_id)
    assert readiness["payload"]["extension_readiness"] == "ready"
    assert readiness["payload"]["blockers"] == []
    assert character["payload"]["state"] == "listening"
    # Each revision of each state frame is sent once on one connection.
    for kind in ("binding_state", "readiness", "character_state"):
        revisions = [
            frame["payload"][f"{kind.removesuffix('_state')}_revision"]
            for frame in frames
            if frame["kind"] == kind
        ]
        assert revisions == sorted(set(revisions)), (kind, revisions)

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select status, document_origin, tab_id, sdk_protocol_version,
                   document_attachment_id, page_proof_digest, offer_token_id
            from public.extension_bindings where interview_session_id = %s
            """,
            (prepared.session_id,),
        )
        row = await cur.fetchone()
        await cur.execute(
            "select last_sequence, acknowledged_sequence "
            "from public.extension_control_streams where extension_installation_id = %s",
            (prepared.installation_id,),
        )
        stream = await cur.fetchone()
    assert row is not None
    assert row["status"] == "bound"
    assert row["document_origin"] == ORIGIN
    assert row["tab_id"] == 11
    assert row["document_attachment_id"] == attachment_id
    assert row["page_proof_digest"].startswith("sha256:")
    assert row["offer_token_id"] is not None
    assert stream is not None
    assert stream["last_sequence"] == client.acknowledged
    assert stream["acknowledged_sequence"] == client.acknowledged


async def test_a_page_proof_for_another_session_is_refused(
    database: Database,
    running_api: FastAPI,
    page_proof_signer: Signer,
    auth_users: dict[str, SignedInUser],
) -> None:
    prepared = await prepare_session(database, auth_users, "socket-proof-mismatch")
    install_observation_readiness(
        running_api, RecordingObservationReadiness(readiness=complete_readiness())
    )
    payload = await binding_offer_payload(
        database=database,
        prepared=prepared,
        signer=page_proof_signer,
        document_attachment_id=uuid7(),
    )
    payload["page_proof"] = page_proof(
        signer=page_proof_signer,
        client_id=prepared.case.client_id,
        session_id=uuid7(),
        preview_runtime_allocation_id=prepared.allocation_id,
        document_attachment_id=uuid7(),
        origin=ORIGIN,
        lifetime_seconds=300,
    )

    async with connect(
        device=prepared.device, installation_id=prepared.installation_id
    ) as client:
        await client.send(
            "recovery_request",
            recovery_payload(installation_id=prepared.installation_id, resume_from=0),
        )
        await client.drain(2)
        await client.send("binding_offer", payload)
        binding = (await client.drain(1))[0]

    assert binding["payload"]["status"] == "rejected"
    assert binding["payload"]["reason"] == "page_proof_session_mismatch"

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as bound from public.extension_bindings where status = 'bound'"
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["bound"] == 0


async def test_a_page_proof_this_control_plane_did_not_sign_closes_the_socket(
    database: Database,
    running_api: FastAPI,
    page_proof_signer: Signer,
    auth_users: dict[str, SignedInUser],
) -> None:
    prepared = await prepare_session(database, auth_users, "socket-proof-forged")
    install_observation_readiness(
        running_api, RecordingObservationReadiness(readiness=complete_readiness())
    )
    # A well-formed envelope naming the real key, signed with a key nobody holds.
    forged = Signer(seed=bytes(range(32)), key_id=page_proof_signer.key_id)
    payload = await binding_offer_payload(
        database=database,
        prepared=prepared,
        signer=forged,
        document_attachment_id=uuid7(),
    )

    async with connect(
        device=prepared.device, installation_id=prepared.installation_id
    ) as client:
        await client.send(
            "recovery_request",
            recovery_payload(installation_id=prepared.installation_id, resume_from=0),
        )
        await client.drain(2)
        await client.send("binding_offer", payload)
        with pytest.raises(ConnectionClosed) as closed:
            await client.receive()
    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == POLICY_VIOLATION_CLOSE_CODE
    assert closed.value.rcvd.reason == "invalid_page_proof"


async def test_a_command_another_process_dispatches_reaches_the_bound_tab(
    database: Database,
    running_api: FastAPI,
    page_proof_signer: Signer,
    auth_users: dict[str, SignedInUser],
) -> None:
    """The worker's gesture path: record in one process, route from the socket's.

    The dispatch runs as its own operating-system process with its own pool, so
    the only thing connecting it to the API process holding the socket is the
    committed command and the notification its commit sends.
    """
    prepared = await prepare_session(database, auth_users, "socket-cross-process")
    attachment_id = uuid7()
    install_observation_readiness(
        running_api, RecordingObservationReadiness(readiness=complete_readiness())
    )
    await observe_document(
        database,
        case=prepared.case,
        interview_session_id=prepared.session_id,
        preview_runtime_allocation_id=prepared.allocation_id,
        document_attachment_id=attachment_id,
        targets=(_SAVE_VIEW,),
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
        binding_id = last_of(bound, "binding_state")["payload"]["extension_binding_id"]

        outcome = await dispatch_point(
            client_id=prepared.case.client_id,
            session_id=prepared.session_id,
            target=_SAVE_VIEW,
        )
        assert outcome["dispatched"] is True, outcome
        command_id = str(outcome["command_id"])
        frames = await client.until(has_command(command_id))
        routed = last_of(frames, "visual_command")

        await client.send(
            "command_result",
            {
                "command_id": command_id,
                "outcome": "applied",
                "extension_binding_id": binding_id,
                "document_attachment_id": str(attachment_id),
                "observed_targets": [
                    {"target_id": "save-view-button", "target_revision": 3}
                ],
                "completed_at": now(),
            },
        )
        await client.synchronize()

        # A gesture for a revision the runtime has since replaced is refused.
        stale = await dispatch_point(
            client_id=prepared.case.client_id,
            session_id=prepared.session_id,
            target=RegisteredTarget(
                target_id="save-view-button",
                target_revision=2,
                description="Save this view",
                placement="top left of the screen",
                role="button",
                visible=True,
            ),
        )

    assert routed["payload"]["session_id"] == str(prepared.session_id)
    assert routed["payload"]["extension_binding_id"] == binding_id
    assert routed["payload"]["document_attachment_id"] == str(attachment_id)
    scene = routed["payload"]["command"]["scene"]
    assert routed["payload"]["command"]["type"] == "scene"
    assert [(step["step"], step["target_id"], step["target_revision"]) for step in scene["steps"]] == [
        ("point", "save-view-button", 3)
    ]
    assert stale == {"dispatched": False, "command_id": None, "reason": "stale_target"}

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select dispatches.outcome, dispatches.routed_at, commands.state,
                   commands.provider_ref
            from public.visual_command_dispatches as dispatches
            join public.commands on commands.id = dispatches.command_id
            where dispatches.command_id = %s
            """,
            (UUID(command_id),),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["outcome"] == "applied"
    assert row["routed_at"] is not None
    assert row["state"] == "terminal"
    assert row["provider_ref"] == binding_id


async def test_a_gesture_reaches_the_bound_tab_while_a_writer_holds_readiness(
    database: Database,
    running_api: FastAPI,
    page_proof_signer: Signer,
    auth_users: dict[str, SignedInUser],
) -> None:
    """A readiness write that stalls holding the row never holds back the cursor.

    In a live interview one readiness write held the session's readiness row for 13
    seconds, and every circle the agent spoke meanwhile reached the preview at once
    afterwards. The socket reads readiness without taking the row, so a gesture
    dispatched while another transaction holds it still arrives promptly.
    """
    prepared = await prepare_session(database, auth_users, "socket-readiness-held")
    attachment_id = uuid7()
    install_observation_readiness(
        running_api, RecordingObservationReadiness(readiness=complete_readiness())
    )
    await observe_document(
        database,
        case=prepared.case,
        interview_session_id=prepared.session_id,
        preview_runtime_allocation_id=prepared.allocation_id,
        document_attachment_id=attachment_id,
        targets=(_SAVE_VIEW,),
    )

    async with connect(
        device=prepared.device, installation_id=prepared.installation_id
    ) as client:
        await bind_document(
            client,
            database=database,
            prepared=prepared,
            signer=page_proof_signer,
            document_attachment_id=attachment_id,
        )
        async with database.transaction() as holder, holder.cursor() as cur:
            await cur.execute(
                "select 1 from public.session_readiness "
                "where interview_session_id = %s for update",
                (prepared.session_id,),
            )
            assert await cur.fetchone() is not None
            outcome = await dispatch_point(
                client_id=prepared.case.client_id,
                session_id=prepared.session_id,
                target=_SAVE_VIEW,
            )
            assert outcome["dispatched"] is True, outcome
            dispatched_at = time.monotonic()
            await client.until(has_command(str(outcome["command_id"])))
            delivered = time.monotonic() - dispatched_at

    assert delivered < _PROMPT_SECONDS, f"the gesture took {delivered:.1f}s to arrive"


async def test_ending_the_session_from_the_session_tab_revokes_the_extension(
    database: Database,
    running_api: FastAPI,
    page_proof_signer: Signer,
    auth_users: dict[str, SignedInUser],
) -> None:
    """End from the session tab reaches the extension over its own socket.

    The control is posted to the session API as the session tab posts it; the
    socket learns of it only from the committed state.
    """
    prepared = await prepare_session(database, auth_users, "socket-end-from-tab")
    attachment_id = uuid7()
    install_observation_readiness(
        running_api, RecordingObservationReadiness(readiness=complete_readiness())
    )
    await observe_document(
        database,
        case=prepared.case,
        interview_session_id=prepared.session_id,
        preview_runtime_allocation_id=prepared.allocation_id,
        document_attachment_id=attachment_id,
        targets=(_SAVE_VIEW,),
    )

    async with connect(
        device=prepared.device, installation_id=prepared.installation_id
    ) as client:
        await bind_document(
            client,
            database=database,
            prepared=prepared,
            signer=page_proof_signer,
            document_attachment_id=attachment_id,
        )
        pending = await dispatch_point(
            client_id=prepared.case.client_id,
            session_id=prepared.session_id,
            target=_SAVE_VIEW,
        )
        command_id = str(pending["command_id"])
        await client.until(has_command(command_id))

        await post_participant_control(
            access_token=auth_users["participant"].access_token,
            session_id=prepared.session_id,
            action="end_confirm",
        )
        frames = await client.until(
            lambda frames: "binding_state" in kinds(frames)
            and "cancel" in kinds(frames)
            and "character_state" in kinds(frames)
            and last_of(frames, "character_state")["payload"]["state"] == "completion"
        )

    binding = last_of(frames, "binding_state")
    assert binding["payload"]["status"] == "revoked"
    assert binding["payload"]["reason"] == "end_confirm"
    assert binding["payload"]["session_id"] == str(prepared.session_id)
    cancel = last_of(frames, "cancel")
    assert cancel["payload"] == {"command_id": command_id, "reason": "session_closed"}

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select status, reason from public.extension_bindings "
            "where interview_session_id = %s",
            (prepared.session_id,),
        )
        bindings = await cur.fetchall()
        await cur.execute(
            "select outcome, outcome_reason from public.visual_command_dispatches "
            "where command_id = %s",
            (UUID(command_id),),
        )
        dispatch = await cur.fetchone()
    assert [(row["status"], row["reason"]) for row in bindings] == [("revoked", "end_confirm")]
    assert dispatch is not None
    assert (dispatch["outcome"], dispatch["outcome_reason"]) == ("canceled", "session_closed")


async def test_pause_cancels_a_pending_command_and_a_result_is_recorded(
    database: Database,
    running_api: FastAPI,
    page_proof_signer: Signer,
    auth_users: dict[str, SignedInUser],
) -> None:
    prepared = await prepare_session(database, auth_users, "socket-pause")
    attachment_id = uuid7()
    install_observation_readiness(
        running_api, RecordingObservationReadiness(readiness=complete_readiness())
    )
    await observe_document(
        database,
        case=prepared.case,
        interview_session_id=prepared.session_id,
        preview_runtime_allocation_id=prepared.allocation_id,
        document_attachment_id=attachment_id,
        targets=(_SAVE_VIEW,),
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
        binding_id = last_of(bound, "binding_state")["payload"]["extension_binding_id"]

        first = await dispatch_point(
            client_id=prepared.case.client_id,
            session_id=prepared.session_id,
            target=_SAVE_VIEW,
        )
        await client.until(has_command(str(first["command_id"])))
        # The extension answers the routed command with its terminal outcome.
        await client.send(
            "command_result",
            {
                "command_id": str(first["command_id"]),
                "outcome": "applied",
                "extension_binding_id": binding_id,
                "document_attachment_id": str(attachment_id),
                "observed_targets": [
                    {"target_id": "save-view-button", "target_revision": 3}
                ],
                "completed_at": now(),
            },
        )
        second = await dispatch_point(
            client_id=prepared.case.client_id,
            session_id=prepared.session_id,
            target=_SAVE_VIEW,
        )
        await client.until(has_command(str(second["command_id"])))

        await client.send(
            "control_action",
            {
                "action_id": str(uuid7()),
                "session_id": str(prepared.session_id),
                "action": "pause",
                "expected_session_control_revision": 0,
                "requested_at": now(),
                "locally_confirmed": False,
            },
        )
        frames = await client.until(
            lambda frames: {"cancel", "control_action_result", "readiness"} <= set(kinds(frames))
            and "character_state" in kinds(frames)
            and last_of(frames, "character_state")["payload"]["state"] == "paused"
            and last_of(frames, "readiness")["payload"]["combined_readiness"] == "blocked"
        )

    cancel = last_of(frames, "cancel")
    assert cancel["payload"] == {
        "command_id": str(second["command_id"]),
        "reason": "paused_by_participant",
    }
    result = last_of(frames, "control_action_result")
    assert result["payload"]["disposition"] == "accepted"
    assert result["payload"]["pause_state"] == "paused"
    assert result["payload"]["mute_state"] == "unmuted"
    assert "paused_by_participant" in last_of(frames, "readiness")["payload"]["blockers"]
    # Pause keeps the binding: the orb stays with Resume, Return, and End.
    assert "binding_state" not in kinds(frames)

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select command_id, outcome, outcome_reason, observed_targets
            from public.visual_command_dispatches
            where interview_session_id = %s
            order by created_at
            """,
            (prepared.session_id,),
        )
        dispatches = [dict(row) for row in await cur.fetchall()]
        await cur.execute(
            "select command_id, disposition, effect_state, technical_category "
            "from public.results order by received_at"
        )
        results = [dict(row) for row in await cur.fetchall()]
    assert [row["outcome"] for row in dispatches] == ["applied", "canceled"]
    assert dispatches[0]["observed_targets"] == [
        {"target_id": "save-view-button", "target_revision": 3}
    ]
    assert dispatches[1]["outcome_reason"] == "paused_by_participant"
    assert [row["disposition"] for row in results] == ["succeeded", "canceled"]
    # The canceled command had been routed, so the page may have applied it.
    assert results[1]["effect_state"] == "indeterminate"


async def test_a_sequence_the_extension_could_not_have_reached_closes_the_socket(
    database: Database,
    running_api: FastAPI,
    auth_users: dict[str, SignedInUser],
) -> None:
    prepared = await prepare_session(database, auth_users, "socket-gap")
    install_observation_readiness(
        running_api, RecordingObservationReadiness(readiness=complete_readiness())
    )
    async with connect(
        device=prepared.device, installation_id=prepared.installation_id
    ) as client:
        await client.send(
            "recovery_request",
            recovery_payload(
                installation_id=prepared.installation_id, resume_from=99
            ),
        )
        with pytest.raises(ConnectionClosed) as closed:
            await client.receive()
    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == SEQUENCE_GAP_CLOSE_CODE
    assert closed.value.rcvd.reason == "sequence_gap"


async def test_a_reconnect_reprojects_state_and_settles_unknown_commands(
    database: Database,
    running_api: FastAPI,
    page_proof_signer: Signer,
    auth_users: dict[str, SignedInUser],
) -> None:
    prepared = await prepare_session(database, auth_users, "socket-resume")
    attachment_id = uuid7()
    install_observation_readiness(
        running_api, RecordingObservationReadiness(readiness=complete_readiness())
    )
    await observe_document(
        database,
        case=prepared.case,
        interview_session_id=prepared.session_id,
        preview_runtime_allocation_id=prepared.allocation_id,
        document_attachment_id=attachment_id,
        targets=(_SAVE_VIEW,),
    )

    async with connect(
        device=prepared.device, installation_id=prepared.installation_id
    ) as client:
        await client.send(
            "recovery_request",
            recovery_payload(installation_id=prepared.installation_id, resume_from=0),
        )
        await client.drain(2)
        acknowledged_before = client.acknowledged
        await client.send(
            "binding_offer",
            await binding_offer_payload(
                database=database,
                prepared=prepared,
                signer=page_proof_signer,
                document_attachment_id=attachment_id,
            ),
        )
        # Read without acknowledging, so the reconnect has undelivered work above
        # its resume point, including the routed command.
        received = []
        while not any(frame["kind"] == "character_state" for frame in received):
            received.append(await client.receive())
        pending = await dispatch_point(
            client_id=prepared.case.client_id,
            session_id=prepared.session_id,
            target=_SAVE_VIEW,
        )
        while not has_command(str(pending["command_id"]))(received):
            received.append(await client.receive())

    await await_disconnect(database, installation_id=prepared.installation_id)
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select last_sequence from public.extension_control_streams "
            "where extension_installation_id = %s",
            (prepared.installation_id,),
        )
        before = await cur.fetchone()
    assert before is not None

    async with connect(
        device=prepared.device, installation_id=prepared.installation_id
    ) as client:
        client.acknowledged = acknowledged_before
        await client.send(
            "recovery_request",
            recovery_payload(
                installation_id=prepared.installation_id,
                resume_from=acknowledged_before,
                pending=(UUID(str(pending["command_id"])),),
                session_id=prepared.session_id,
            ),
        )
        recovery, preferences, binding, readiness, character = await client.drain(5)

    assert recovery["payload"]["disposition"] == "restarted"
    assert recovery["sequence"] == acknowledged_before + 1
    assert recovery["payload"]["next_sequence"] == acknowledged_before + 2
    assert preferences["kind"] == "preferences_state"
    # Resumption re-projects the binding with everything else it re-projects.
    assert binding["kind"] == "binding_state"
    assert binding["payload"]["status"] == "bound"
    assert binding["payload"]["document_attachment_id"] == str(attachment_id)
    assert readiness["kind"] == "readiness"
    assert readiness["payload"]["session_id"] == str(prepared.session_id)
    assert "extension_control_connection_lost" in readiness["payload"]["blockers"]
    assert character["kind"] == "character_state"

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select outcome, outcome_reason from public.visual_command_dispatches "
            "where command_id = %s",
            (UUID(str(pending["command_id"])),),
        )
        dispatch = await cur.fetchone()
        await cur.execute(
            "select disposition, effect_state, technical_category from public.results "
            "where command_id = %s",
            (UUID(str(pending["command_id"])),),
        )
        result = await cur.fetchone()
        await cur.execute(
            """
            select count(*) as superseded from public.extension_control_frames
            where extension_installation_id = %s and superseded_at is not null
            """,
            (prepared.installation_id,),
        )
        superseded = await cur.fetchone()
    assert dispatch is not None
    assert dispatch["outcome"] == "failed"
    assert dispatch["outcome_reason"] == "outcome_unknown"
    assert result is not None
    assert result["disposition"] == "failed"
    assert result["effect_state"] == "indeterminate"
    assert result["technical_category"] == "outcome_unknown"
    assert superseded is not None
    assert superseded["superseded"] == before["last_sequence"] - acknowledged_before


async def test_an_installation_the_control_plane_does_not_hold_is_told_so(
    database: Database,
    running_api: FastAPI,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Unknown and revoked installations get their own close code after the handshake.

    A close before the handshake reaches the browser as a network failure, so the
    extension could only retry; this code is what lets it drop the installation
    and offer to link the browser again.
    """
    unknown = DeviceKey.generate()
    async with connect(device=unknown, installation_id=uuid7()) as client:
        with pytest.raises(ConnectionClosed) as closed:
            await client.receive()
    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == INSTALLATION_NOT_LINKED_CLOSE_CODE
    assert closed.value.rcvd.reason == "installation_unknown"

    prepared = await prepare_session(database, auth_users, "socket-revoked")
    challenge, signature = prepared.device.prove(
        audience="utrace-extension-api",
        purpose="installation_revocation",
        installation_id=prepared.installation_id,
    )
    await revoke_installation(
        database,
        installation_id=prepared.installation_id,
        proof=ProofOfPossession(
            algorithm="ES256", challenge_b64u=challenge, signature_b64u=signature
        ),
        audience="utrace-extension-api",
        possession_max_age_seconds=60,
    )
    async with connect(
        device=prepared.device, installation_id=prepared.installation_id
    ) as client:
        with pytest.raises(ConnectionClosed) as closed:
            await client.receive()
    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == INSTALLATION_NOT_LINKED_CLOSE_CODE
    assert closed.value.rcvd.reason == "installation_revoked"
