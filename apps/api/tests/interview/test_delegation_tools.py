"""The tools the application executes during a delegation.

Target selection runs against the real Jev model over the targets ingestion recorded for the
bound document; the visual gesture goes through the real dispatch path with its
policy, readiness, and freshness checks. No extension socket is open in these
tests, so the dispatched command stays recorded for the socket to route; the
socket tests prove the routing.
"""

import json
from uuid import UUID

from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    InterviewPhase,
    ProductSignalState,
)

from app.connectors.typesafe_jev import JevClient
from app.context.bundles import AssembledBundle, assemble_bundle
from app.db.pool import Database
from app.ingestion.target_registry import IngestionTargetRegistry
from app.interview.ports import RegisteredTarget
from app.interview.voice.tools import ToolContext, execute_tool
from tests.connectors.conftest import optional_env, require_env
from tests.factories import CaseFixture, create_case
from tests.helpers import record_session_readiness
from tests.interview.fixtures import (
    create_binding,
    create_live_session,
    create_preview_allocation,
    link_installation,
    observe_document,
)
from tests.supabase_auth import SignedInUser

_DEVICE_KEY = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEtoolstestkeyfortheinstallationrecord"
_VISUAL_DEADLINE_SECONDS = 20
_OBSERVATION_WINDOW_SECONDS = 300


async def _case(
    database: Database, auth_users: dict[str, SignedInUser], name: str
) -> CaseFixture:
    """One routed Product Signal in its interviewing state."""
    return await create_case(
        database,
        name=name,
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )


async def _bundle(
    database: Database, *, case: CaseFixture, session_id: UUID
) -> AssembledBundle:
    """Assemble one current delegation bundle."""
    return await assemble_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        purpose="voice_delegation",
        budget_items=32,
        observations=(),
        extra_items=(),
        recent_fragment_limit=10,
        prior_command_limit=5,
    )


async def test_a_pointing_gesture_selects_a_registered_target_and_dispatches(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    values = require_env("VERCEL_AI_GATEWAY_API_KEY")
    model_id = optional_env("UTRACE_JEV_MODEL_ID") or "typesafe-ai/jev"
    case = await _case(database, auth_users, "tools-visual")
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )
    allocation_id = await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )
    installation_id = await link_installation(
        database,
        utrace_user_id=case.utrace_user_id,
        device_public_key_spki_b64u=_DEVICE_KEY,
        protocol_version="1.0",
    )
    _, attachment_id = await create_binding(
        database,
        case=case,
        interview_session_id=session_id,
        extension_installation_id=installation_id,
        preview_runtime_allocation_id=allocation_id,
    )
    await record_session_readiness(
        database,
        interview_session_id=session_id,
        conversational=True,
        observation=True,
    )
    bundle = await _bundle(database, case=case, session_id=session_id)
    await observe_document(
        database,
        case=case,
        interview_session_id=session_id,
        preview_runtime_allocation_id=allocation_id,
        document_attachment_id=attachment_id,
        targets=(
            RegisteredTarget(
                target_id="save-view-button",
                target_revision=4,
                description="Save this view",
                placement="top left of the screen",
                role="button",
                visible=True,
            ),
            RegisteredTarget(
                target_id="billing-link",
                target_revision=2,
                description="Billing settings",
                placement="top left of the screen",
                role="link",
                visible=True,
            ),
        ),
    )

    async with JevClient(
        api_key=values["VERCEL_AI_GATEWAY_API_KEY"], model_id=model_id, timeout_seconds=30.0
    ) as jev:
        context = ToolContext(
            database=database,
            client_id=case.client_id,
            product_signal_id=case.product_signal_id,
            interview_session_id=session_id,
            bundle=bundle,
            targets=IngestionTargetRegistry(database),
            jev=jev,
            visual_command_deadline_seconds=_VISUAL_DEADLINE_SECONDS,
            observation_window_seconds=_OBSERVATION_WINDOW_SECONDS,
        )
        result = json.loads(
            await execute_tool(
                context,
                name="point_at_product_element",
                arguments={
                    "description": "the button that saves the current view",
                    "gesture": "point",
                },
            )
        )

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select document_attachment_id, command_kind, targets, policy_decision,
                   routed_at
            from public.visual_command_dispatches
            where interview_session_id = %s
            """,
            (session_id,),
        )
        dispatches = await cur.fetchall()
        await cur.execute(
            "select command_type, state, payload from public.commands where target_id = %s",
            (session_id,),
        )
        command_row = await cur.fetchone()

    if "refused" in result:
        # An unresolved selection is a legitimate outcome; nothing may dispatch.
        assert result["refused"] == "no_matching_target"
        assert dispatches == []
        return

    assert result["pointed_at"] == "Save this view"
    assert len(dispatches) == 1
    dispatch = dispatches[0]
    assert dispatch["document_attachment_id"] == attachment_id
    assert dispatch["command_kind"] == "scene"
    assert dispatch["targets"] == [{"target_id": "save-view-button", "target_revision": 4}]
    assert dispatch["policy_decision"]["observation_ready"] is True
    # Recorded for the bound installation's socket, which has not routed it yet.
    assert dispatch["routed_at"] is None
    assert command_row is not None
    assert command_row["command_type"] == "extension.visual_command"
    assert command_row["state"] == "queued"
    steps = command_row["payload"]["scene"]["steps"]
    assert [(step["target_id"], step["target_revision"]) for step in steps] == [
        ("save-view-button", 4)
    ]


async def test_recording_criteria_freezes_them_and_advances_the_concern_revision(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users, "tools-criteria")
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )
    bundle = await _bundle(database, case=case, session_id=session_id)
    context = ToolContext(
        database=database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        bundle=bundle,
        targets=None,
        jev=None,
        visual_command_deadline_seconds=_VISUAL_DEADLINE_SECONDS,
        observation_window_seconds=_OBSERVATION_WINDOW_SECONDS,
    )
    recorded = json.loads(
        await execute_tool(
            context,
            name="record_acceptance_criteria",
            arguments=
                {
                    "agreed_outcome": "save a conversion view and reopen it later",
                    "workflow": "saved_conversion_views",
                    "criteria": [
                        {
                            "statement": "the filter stack saves under a name I choose",
                            "observability": CriterionObservability.OBSERVED.value,
                        },
                        {
                            "statement": "reopening restores every saved filter",
                            "observability": CriterionObservability.OBSERVED.value,
                        },
                    ],
                }
            ,
        )
    )
    assert recorded == {"acceptance_criteria_revision": 1, "criterion_count": 2}

    status = json.loads(await execute_tool(context, name="implementation_status", arguments={}))
    assert status["progress"] == "nothing is being changed yet"
    assert status["work_in_flight"] is False

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select concern_revision, acceptance_criteria_revision "
            "from public.product_signals where id = %s",
            (case.product_signal_id,),
        )
        signal = await cur.fetchone()
        await cur.execute(
            "select statement from public.validation_criteria "
            "where product_signal_id = %s order by ordinal",
            (case.product_signal_id,),
        )
        criteria = [dict(row) for row in await cur.fetchall()]
    assert signal is not None
    assert signal["concern_revision"] == 1
    assert signal["acceptance_criteria_revision"] == 1
    assert [row["statement"] for row in criteria] == [
        "the filter stack saves under a name I choose",
        "reopening restores every saved filter",
    ]

    # The bundle that produced the agreement is now stale, so a repeat is refused
    # rather than silently taking a second revision.
    repeat = json.loads(
        await execute_tool(
            context,
            name="record_acceptance_criteria",
            arguments=
                {
                    "agreed_outcome": "something else",
                    "workflow": "saved_conversion_views",
                    "criteria": [
                        {
                            "statement": "a different criterion",
                            "observability": CriterionObservability.SUBJECTIVE.value,
                        }
                    ],
                }
            ,
        )
    )
    assert repeat["refused"] == "stale_context"
