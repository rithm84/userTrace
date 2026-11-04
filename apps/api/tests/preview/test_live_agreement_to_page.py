"""From the participant's agreement to the agreed change on their screen, live.

Every process from the claimed invitation on is the one a deployment runs: the
code investigation and the case's workspace prepared beside the base preview, the
intake draft that workspace queues, the voice agent's own `propose_change` and
`record_acceptance_criteria` tools, a real worker claiming the draft, gate, and
attempt Jobs as they are dispatched, Codex running GPT-6.1 Sol in real Vercel
Sandbox workspaces, the Implementation Gate's real proposal model, a real spare
candidate runtime, the real preview front door, and a real Chrome holding the
session origin. Only what the conversation itself would have produced is recorded
directly: the concern intake recorded, the confirmed evidence the interview
established, and its classification.

The clock starts when the agreement commits and stops when Chrome, signed in
through the door, shows the dashboard with a control the change adds. The session
tab learns of the new route from its projection over Supabase Realtime and the
extension then reloads the preview tab; here the projection's row is read every
50 milliseconds instead, which stands in for that notification.

`UTRACE_LIVE_ANSWER_AFTER_SECONDS` sets how long after the proposal the agreement
comes. Unset, it comes once the draft has been proven ready and the gate's
evaluation prepared, which is when an answer needs no wait at all.

`UTRACE_LIVE_PROPOSE_WHILE_INTAKE_DRAFTS=1` proposes while the intake draft is
still being written, as a short interview does: the proposal must then revise
that draft once it finishes rather than start over.

`UTRACE_LIVE_AGREE_WITHOUT_PROPOSAL=1` records the agreement with no proposal
before it, as when the voice agent proposes out loud but skips `propose_change`:
the agreed change is then drafted from the agreement, revising the intake draft.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from typing import cast
from uuid import UUID

import httpx
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    InterviewPhase,
    InterviewSessionState,
    ProductSignalState,
)

from app.adapters.observation_readiness import IngestionObservationReadiness
from app.clients.shopalytics_release import shopalytics_implementation_policy
from app.connectors.openai_roles import OpenAIRole, openai_configuration_for
from app.connectors.vercel_sandboxes import VercelSandboxes
from app.context.bundles import assemble_bundle
from app.context.purposes import BundlePurpose
from app.db.pool import Database
from app.execution.handlers import build_exhausted_handlers
from app.execution.worker import Worker, WorkerConfig
from app.gate.job_types import DECIDE_GATE
from app.gate.jobs import run_gate
from app.ids import uuid7
from app.implementation.artifact_store import ArtifactStore
from app.implementation.drafts import PREPARE_DRAFT, prepare_draft
from app.implementation.handlers import RUN_IMPLEMENTATION_ATTEMPT, run_implementation_attempt
from app.implementation.investigation import InvestigationRequest, investigate_repository
from app.implementation.prepared import prepare_case_workspace
from app.interview.projection import advance_phase_on_connection
from app.interview.voice.tools import ToolContext, execute_tool
from app.preview.activation import ServerActivationContext
from app.preview.config import TemplateBuildSettings, get_preview_settings
from app.preview.handoff import mint_product_handoff
from app.preview.jobs import prepare_spare_candidate_job
from app.preview.pool import load_runtime_secrets
from app.preview.provisioning import prepare_session_preview, provision_warm_runtime
from app.preview.recipe import TemplateRecipe
from app.preview.render_check import added_targets
from app.preview.runtime import PreviewRuntimeProvider
from app.preview.spare import PREPARE_SPARE_CANDIDATE, decoded_original, start_spare_candidate
from app.preview.teardown import tear_down_allocation
from app.preview.template import load_recipe
from app.settings import Settings
from tests.factories import create_case, create_interview_session
from tests.gate.factories import (
    SHOPALYTICS_BASE_REVISION,
    GatedCase,
    create_evidence_record,
)
from tests.implementation.conftest import (  # noqa: F401  (fixtures this test uses)
    artifact_store,
    github_token,
    openai_key,
    sandboxes,
    workspace_snapshot_id,
)
from tests.implementation.driver import Secret
from tests.ingestion.keys import SigningKeys
from tests.preview.conftest import install_template
from tests.preview.live_fixtures import ORIGIN_SUFFIX, publish_release, require_committed_template
from tests.preview.live_harness import (
    FrontDoor,
    PreviewBrowser,
    PreviewTls,
    PublicTunnel,
    open_preview_in_chrome,
)
from tests.supabase_auth import SignedInUser

DASHBOARD_PATH = "/app/conversion"
WORKFLOW = "saved_conversion_views"
OUTCOME = "Save the current conversion filter stack under a name you choose and reopen it later"
CRITERIA = [
    {
        "statement": "Once it's ready, you'll save your filters under a name you choose",
        "observability": CriterionObservability.OBSERVED.value,
    },
    {
        "statement": "After a refresh, reopening the saved view brings back every filter",
        "observability": CriterionObservability.OBSERVED.value,
    },
]
#: A criterion the original request does not ask for, so revising the intake draft
#: has to change its files (`UTRACE_LIVE_PROPOSAL_ADDS_CRITERION=1`).
ADDED_CRITERION = {
    "statement": "Each saved view shows the day it was saved beside its name",
    "observability": CriterionObservability.OBSERVED.value,
}
#: The change the participant asks for while trying the first version, and when, on
#: the participant's 2026-11-01 timing: proposed about 31 seconds after the first
#: version is up, and agreed 15.5 seconds after the proposal started
#: (`UTRACE_LIVE_CHANGE_DURING_VALIDATION=1`).
CHANGE_OUTCOME = OUTCOME + ", with each saved view showing the day it was saved"
CHANGE_PROPOSED_AFTER_PAGE_UP_SECONDS = 31.0
CHANGE_ANSWERED_AFTER_PROPOSAL_SECONDS = 15.5
_DEADLINE_SECONDS = 300.0
_WATCH_SECONDS = 0.05


async def _until[T](
    description: str,
    probe: Callable[[], Awaitable[T | None]],
    *,
    every: float = 0.5,
    within: float = _DEADLINE_SECONDS,
) -> T:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + within
    while loop.time() < deadline:
        answer = await probe()
        if answer is not None:
            return answer
        await asyncio.sleep(every)
    raise AssertionError(f"timed out waiting for {description}")


async def _row(database: Database, sql: str, *params: object) -> dict[str, object] | None:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(sql, params)
        row = await cur.fetchone()
    return None if row is None else dict(row)


async def _tool(
    database: Database,
    *,
    case: GatedCase,
    session_id: UUID,
    name: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    """Call one of the voice agent's tools over a context assembled now."""
    bundle = await assemble_bundle(
        database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        purpose=BundlePurpose.VOICE_TOOLS,
        budget_items=32,
        observations=(),
        extra_items=(),
        recent_fragment_limit=10,
        prior_command_limit=5,
    )
    context = ToolContext(
        database=database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        bundle=bundle,
        observation_window_seconds=300,
    )
    result: dict[str, object] = json.loads(
        await execute_tool(context, name=name, arguments=arguments)
    )
    return result


REQUEST = (
    "I keep rebuilding the same conversion filters every Monday. Can Shopalytics let me save "
    "a view for TikTok traffic from women 25-34 on products with negative sizing reviews, "
    "then reopen it later?"
)
PAIN_POINT = "Rebuilding the same conversion filter stack every Monday slows recurring analysis."
CRITERION = "A saved conversion view reopens with every filter and the conversion display it had."


async def _record_concern(database: Database, *, case: GatedCase) -> str:
    """Record the concern intake commits, and return it as the investigation reads it."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.product_signal_concerns (
                id, client_id, product_signal_id, concern_revision, origin,
                pain_point, validation_criterion, user_wording, unknowns
            )
            values (%s, %s, %s, 0, 'routing_inference', %s, %s, %s, '[]')
            """,
            (
                uuid7(),
                case.client_id,
                case.product_signal_id,
                PAIN_POINT,
                CRITERION,
                Jsonb({"messages": [REQUEST]}),
            ),
        )
    return "\n".join(
        [
            "The customer's request, before the interview has agreed a scope:",
            f"> {REQUEST}",
            f"Pain point: {PAIN_POINT}",
            f"Outcome to reach: {CRITERION}",
        ]
    )


async def _change_during_validation(
    database: Database,
    *,
    browser: PreviewBrowser,
    case: GatedCase,
    session_id: UUID,
    host: str,
    swapped_at: float,
    page_up: float,
    first_files: dict[str, str],
) -> dict[str, object]:
    """Ask for a change while trying the first version, and time it to its page.

    The change is proposed and agreed on the participant's timing, and the clock
    stops when Chrome shows the new version's dashboard with the SDK active. When
    the spare the change was built in, and the workspace it was drafted in, were
    ready is read against the first version's route switch.
    `UTRACE_LIVE_CHANGE_AFTER_PAGE_UP_SECONDS` asks for the change sooner or later.
    """
    timings: dict[str, object] = {}
    # The voice agent's cue begins validating the ready version once the extension
    # has bound the participant's page on it; this Chrome runs without the extension,
    # so the session enters validation as that call leaves it.
    async with database.transaction() as conn:
        await advance_phase_on_connection(
            conn, interview_session_id=session_id, phase=InterviewPhase.VALIDATION
        )
    criteria = [*CRITERIA, ADDED_CRITERION]
    proposed_after = float(
        os.environ.get(
            "UTRACE_LIVE_CHANGE_AFTER_PAGE_UP_SECONDS", CHANGE_PROPOSED_AFTER_PAGE_UP_SECONDS
        )
    )
    await asyncio.sleep(max(0.0, page_up + proposed_after - time.monotonic()))
    route = await _row(
        database,
        "select route_revision from public.preview_routes "
        "where interview_session_id = %s and state = 'active'",
        session_id,
    )
    assert route is not None
    first_revision = route["route_revision"]
    assert isinstance(first_revision, int)
    proposed = await _tool(
        database,
        case=case,
        session_id=session_id,
        name="propose_change",
        arguments={"proposed_outcome": CHANGE_OUTCOME, "workflow": WORKFLOW, "criteria": criteria},
    )
    proposed_at = time.monotonic()
    proposed_clock = await _row(database, "select clock_timestamp() as now")
    assert proposed_clock is not None
    timings["proposed_after_swap"] = round(proposed_at - swapped_at, 1)
    draft = await _row(
        database,
        "select id from public.prepared_drafts where product_signal_id = %s "
        "and kind = 'proposal' order by proposed_at desc limit 1",
        case.product_signal_id,
    )
    assert draft is not None, proposed
    await asyncio.sleep(
        max(0.0, proposed_at + CHANGE_ANSWERED_AFTER_PROPOSAL_SECONDS - time.monotonic())
    )
    agreed = await _tool(
        database,
        case=case,
        session_id=session_id,
        name="record_requested_change",
        arguments={
            "agreed_outcome": CHANGE_OUTCOME,
            "workflow": WORKFLOW,
            "criteria": criteria,
            "as_proposed": True,
        },
    )
    agreed_at = time.monotonic()
    assert "acceptance_criteria_revision" in agreed, agreed

    async def routed() -> dict[str, object] | None:
        row = await _row(
            database,
            "select routes.route_revision, versions.patch_text "
            "from public.preview_routes as routes "
            "join public.preview_versions as versions on versions.id = routes.preview_version_id "
            "where routes.interview_session_id = %s and routes.state = 'active' "
            "and routes.route_revision > %s",
            session_id,
            first_revision,
        )
        return row

    second = await _until("the changed version's route", routed, every=_WATCH_SECONDS)
    routed_at = time.monotonic()
    second_files = {
        str(item["path"]): str(item["content"]) for item in json.loads(str(second["patch_text"]))
    }
    added = added_targets(files=second_files, originals={**dict.fromkeys(second_files), **first_files})
    # The page is the changed version's once a navigation that began after the route
    # switched has a ready gate; a target the change adds is waited for too.
    await browser.gate(f"https://{host}{DASHBOARD_PATH}")
    if added:
        selector = ", ".join(f'[data-utrace-target="{target}"]' for target in sorted(added))

        async def shown() -> bool | None:
            found = await browser.evaluate(f"document.querySelector({json.dumps(selector)}) !== null")
            return True if found else None

        await _until("the change's control", shown, every=_WATCH_SECONDS, within=30)
    state = await browser.evaluate(
        "(() => { const g = document.querySelector('[data-utrace-gate]'); "
        "return g ? g.dataset.utraceGate : 'absent'; })()"
    )
    assert state == "ready", state
    page = time.monotonic()
    built = await _row(
        database,
        "select state, fast_checks -> 'build' as build, agent_cost_cents, "
        "extract(epoch from settled_at - %s) as proven, staged_runtime_ref "
        "from public.prepared_drafts where id = %s",
        proposed_clock["now"],
        draft["id"],
    )
    # When the spare the change was staged in, and the workspace it was drafted in,
    # were ready, against the moment the first version's route switched.
    ready = await _row(
        database,
        """
        select
          extract(epoch from (
            select spare_ready_at from public.preview_runtime_allocations
            where runtime_ref = %s
          ) - routes.created_at) as next_spare,
          extract(epoch from (
            select created_at from public.preview_runtime_allocations
            where runtime_ref = %s
          ) - routes.created_at) as next_spare_started,
          extract(epoch from (
            select created_at from public.prepared_workspaces
            where product_signal_id = %s and generation = 2
          ) - routes.created_at) as next_workspace
        from public.preview_routes as routes
        where routes.interview_session_id = %s and routes.route_revision = %s
        """,
        None if built is None else built["staged_runtime_ref"],
        None if built is None else built["staged_runtime_ref"],
        case.product_signal_id,
        session_id,
        first_revision,
    )
    assert ready is not None
    timings["next_spare_ready_after_swap"] = (
        None if ready["next_spare"] is None else round(float(str(ready["next_spare"])), 1)
    )
    timings["next_spare_started_after_swap"] = (
        None
        if ready["next_spare_started"] is None
        else round(float(str(ready["next_spare_started"])), 1)
    )
    timings["next_workspace_ready_after_swap"] = (
        None if ready["next_workspace"] is None else round(float(str(ready["next_workspace"])), 1)
    )
    assert built is not None
    build = built["build"] if isinstance(built["build"], dict) else {}
    timings.update(
        {
            "draft_state": built["state"],
            "draft_proven_after_proposal": None
            if built["proven"] is None
            else round(float(str(built["proven"])), 1),
            "rebuild_seconds": build.get("seconds"),
            "rebuild_reused": build.get("reused"),
            "restart_seconds": build.get("restart_seconds"),
            "draft_cost_cents": built["agent_cost_cents"],
            "added_targets": sorted(added),
            "yes_to_route_seen": round(routed_at - agreed_at, 2),
            "yes_to_page_up": round(page - agreed_at, 2),
        }
    )
    return timings


def _worker(database: Database) -> Worker:
    """A worker running only the Jobs this path dispatches."""
    return Worker(
        database,
        WorkerConfig(
            worker_id="live-agreement-to-page",
            lease_seconds=60,
            heartbeat_seconds=20,
            poll_interval_seconds=2.0,
            wakeup_batch_size=20,
            outbox_scan_limit=200,
            stale_outbox_seconds=300,
            stale_dispatch_seconds=900,
            concurrency=6,
        ),
        {
            PREPARE_DRAFT: prepare_draft,
            DECIDE_GATE: run_gate,
            RUN_IMPLEMENTATION_ATTEMPT: run_implementation_attempt,
            # A swap asks for the session's next spare, which a change asked for
            # during validation is staged in.
            PREPARE_SPARE_CANDIDATE: prepare_spare_candidate_job,
        },
        build_exhausted_handlers(),
    )


async def test_from_the_agreement_to_the_agreed_change_on_screen(
    database: Database,
    api: httpx.AsyncClient,
    settings: Settings,
    keys: SigningKeys,
    build_settings: TemplateBuildSettings,
    provider: PreviewRuntimeProvider,
    probe_client: httpx.AsyncClient,
    recipe: TemplateRecipe,
    template_snapshot_id: str,
    template_image: str,
    auth_users: dict[str, SignedInUser],
    tunnel: PublicTunnel,
    door: FrontDoor,
    door_tls: PreviewTls,
    sandboxes: VercelSandboxes,  # noqa: F811
    artifact_store: ArtifactStore,  # noqa: F811
    workspace_snapshot_id: str,  # noqa: F811
    github_token: Secret,  # noqa: F811
    openai_key: Secret,  # noqa: F811
) -> None:
    await require_committed_template(
        provider=provider,
        recipe=recipe,
        build_settings=build_settings,
        template_snapshot_id=template_snapshot_id,
        template_image=template_image,
    )
    answer_after = os.environ.get("UTRACE_LIVE_ANSWER_AFTER_SECONDS")
    propose_early = os.environ.get("UTRACE_LIVE_PROPOSE_WHILE_INTAKE_DRAFTS") == "1"
    skip_proposal = os.environ.get("UTRACE_LIVE_AGREE_WITHOUT_PROPOSAL") == "1"
    criteria = (
        [*CRITERIA, ADDED_CRITERION]
        if os.environ.get("UTRACE_LIVE_PROPOSAL_ADDS_CRITERION") == "1"
        else CRITERIA
    )
    base_revision = os.environ.get("UTRACE_LIVE_WORKSPACE_BASE_REVISION", SHOPALYTICS_BASE_REVISION)
    policy = shopalytics_implementation_policy(
        base_revision=base_revision,
        workspace_snapshot_id=workspace_snapshot_id,
        callback_hosts=("callbacks.utrace.dev",),
    )
    case = await create_case(
        database,
        name="agreement-to-page",
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=auth_users["participant"].auth_user_id,
    )
    gated = cast(GatedCase, case)
    release_id = await publish_release(
        database,
        case=case,
        build_settings=build_settings,
        keys=keys,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        implementation=policy.model_dump(mode="json"),
    )
    template = await install_template(
        database,
        client_id=case.client_id,
        configuration_release_id=release_id,
        recipe=recipe,
        template_snapshot_id=template_snapshot_id,
        template_image=template_image,
        warm_pool_target=1,
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    # What the interview established before the proposal.
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.product_signals set established_flow_type = initial_flow_type "
            "where id = %s",
            (case.product_signal_id,),
        )
    await create_evidence_record(
        database,
        case=gated,
        evidence_class="transcript_fragment",
        claim_state="confirmed",
        summary="rebuilding the same conversion filters every Monday",
    )
    concern = await _record_concern(database, case=gated)

    live_settings = get_preview_settings()
    observation = IngestionObservationReadiness(database)
    activation = ServerActivationContext.build(
        settings=live_settings,
        lane_signing_key=settings.lane_signing_key,
        service_token=settings.front_door_service_token,
        manifest_key_id=keys.manifest_key_id,
        manifest_public_key=keys.manifest_private.public_key().public_bytes_raw(),
    )
    worker = _worker(database)
    working = asyncio.create_task(worker.run())
    timings: dict[str, object] = {
        "answer_after": answer_after or "ready",
        "criteria": len(criteria),
    }
    try:
        # The participant's base preview, and their Chrome on it through the door.
        await provision_warm_runtime(
            database,
            provider=provider,
            client=probe_client,
            template=template,
            recipe=recipe,
            settings=live_settings,
            activation=activation,
            origin_suffix=ORIGIN_SUFFIX,
        )
        prepared = await prepare_session_preview(
            database,
            provider=provider,
            client=probe_client,
            settings=live_settings,
            origin_suffix=ORIGIN_SUFFIX,
            client_id=case.client_id,
            product_signal_id=case.product_signal_id,
            interview_session_id=session_id,
            template=template,
            activation=activation,
            observation_port=observation,
        )
        assert prepared.report.ready
        host = prepared.route.public_preview_host
        # The spare, the code investigation, and the workspace, whose readiness
        # queues the intake draft, all run while the interview does.
        candidate_recipe = await load_recipe(database, template_id=template.id)
        interview_started = time.monotonic()
        await asyncio.gather(
            investigate_repository(
                database,
                sandboxes,
                request=InvestigationRequest(
                    client_id=case.client_id,
                    product_signal_id=case.product_signal_id,
                    repository_ref=policy.repository_ref,
                    clone_url=policy.clone_url,
                    base_revision=policy.base_revision,
                    workspace_snapshot_id=policy.workspace_snapshot_id,
                    callback_hosts=policy.callback_hosts,
                    concern=concern,
                ),
                configuration=openai_configuration_for(OpenAIRole.CODE_INVESTIGATION),
                repository_token=github_token.value,
                openai_api_key=openai_key.value,
            ),
            start_spare_candidate(
                database,
                provider=provider,
                client=probe_client,
                settings=live_settings,
                template=template,
                recipe=candidate_recipe,
                interview_session_id=session_id,
                activation=activation,
            ),
            prepare_case_workspace(
                database,
                sandboxes,
                client_id=case.client_id,
                product_signal_id=case.product_signal_id,
                policy=policy,
                repository_token=github_token.value,
            ),
        )
        intake = await _row(
            database,
            "select id from public.prepared_drafts where product_signal_id = %s and kind = 'intake'",
            case.product_signal_id,
        )
        assert intake is not None, "the ready workspace queued no intake draft"

        async def intake_settled() -> dict[str, object] | None:
            row = await _row(
                database,
                "select state, failure_detail, agent_cost_cents from public.prepared_drafts "
                "where id = %s and settled_at is not null",
                intake["id"],
            )
            return row

        async def intake_drafting() -> bool | None:
            row = await _row(
                database,
                "select state from public.prepared_drafts where id = %s",
                intake["id"],
            )
            assert row is not None and row["state"] in ("proposed", "drafting"), row
            return True if row["state"] == "drafting" else None

        if propose_early:
            await _until("the intake draft to start", intake_drafting, every=0.5)
        else:
            intake_row = await _until("the intake draft", intake_settled)
            intake_drafted_at = time.monotonic()
            assert intake_row["state"] == "drafted", intake_row["failure_detail"]
            timings["intake_drafted_after_interview_start_seconds"] = round(
                time.monotonic() - interview_started, 1
            )
        minted = await api.post(
            f"/v1/sessions/{session_id}/preview-handoff",
            headers={"Authorization": f"Bearer {auth_users['participant'].access_token}"},
        )
        assert minted.status_code == 200, minted.text
        async with open_preview_in_chrome(session_origin=host, tls=door_tls) as browser:
            claimed_state, claimed_text = await browser.gate(str(minted.json()["front_door_url"]))
            assert claimed_state == "ready", claimed_text[:1000]
            secrets = await load_runtime_secrets(
                database, allocation_id=prepared.pins.preview_runtime_allocation_id
            )
            signed_in = await browser.post_json(
                recipe.handoff_path,
                {
                    "token": mint_product_handoff(
                        handoff_secret=secrets.handoff_secret,
                        audience_host=host,
                        subject_email=prepared.pins.authenticated_identity,
                        lifetime_seconds=60,
                    ).token
                },
            )
            assert signed_in == 200
            base_state, base_text = await browser.gate(f"https://{host}{DASHBOARD_PATH}")
            assert base_state == "ready", base_text[:1000]

            # The interview runs on for minutes after the intake draft builds, so the
            # standby spare that follows it is ready before any proposal; this run
            # waits for that instead.
            async def standby_ready() -> bool | None:
                row = await _row(
                    database,
                    "select count(*) as ready from public.preview_runtime_allocations "
                    "where interview_session_id = %s and standby_draft_id is not null "
                    "and spare_ready_at is not null",
                    session_id,
                )
                return True if row is not None and row["ready"] else None

            if not propose_early:
                await _until("the standby spare", standby_ready, every=0.5, within=120)
                timings["standby_ready_after_intake_drafted_seconds"] = round(
                    time.monotonic() - intake_drafted_at, 1
                )

            # The voice agent proposes the change, and the draft starts at once;
            # unless it only proposed out loud, and the agreement comes first.
            proposed = (
                None
                if skip_proposal
                else await _tool(
                    database,
                    case=gated,
                    session_id=session_id,
                    name="propose_change",
                    arguments={
                        "proposed_outcome": OUTCOME,
                        "workflow": WORKFLOW,
                        "criteria": criteria,
                    },
                )
            )
            proposed_at = time.monotonic()
            proposed_clock = await _row(database, "select clock_timestamp() as now")
            assert proposed_clock is not None
            if propose_early:
                intake_then = await _row(
                    database, "select state from public.prepared_drafts where id = %s", intake["id"]
                )
                assert intake_then is not None
                timings["intake_state_at_proposal"] = intake_then["state"]
            draft = await _row(
                database,
                "select id from public.prepared_drafts where product_signal_id = %s "
                "and kind = 'proposal' order by proposed_at desc limit 1",
                case.product_signal_id,
            )
            assert skip_proposal or draft is not None, proposed
            draft_id = None if draft is None else draft["id"]

            async def drafted() -> dict[str, object] | None:
                row = await _row(
                    database,
                    "select state, failure_detail, preview_version_id, gate_proposal is not null "
                    "as gate_prepared, settled_at from public.prepared_drafts where id = %s",
                    draft_id,
                )
                assert row is not None
                assert row["state"] not in ("failed", "discarded"), row["failure_detail"]
                return row if row["state"] == "drafted" and row["settled_at"] is not None else None

            if skip_proposal:
                pass
            elif answer_after is None:
                ready = await _until("the draft to be proven", drafted)
                timings["draft_ready_after_proposal_seconds"] = round(
                    time.monotonic() - proposed_at, 1
                )
                assert ready["preview_version_id"] is not None, "the spare was not proven"
                if propose_early:
                    revised = await _row(
                        database,
                        "select state, failure_detail from public.prepared_drafts where id = %s",
                        intake["id"],
                    )
                    assert revised is not None
                    timings["intake_after_proposal"] = revised["failure_detail"]
                timings["gate_prepared"] = ready["gate_prepared"]
                gate = await _row(
                    database,
                    "select gate_proposal from public.prepared_drafts where id = %s",
                    draft_id,
                )
                assert gate is not None
                prepared_gate = gate["gate_proposal"]
                if isinstance(prepared_gate, dict):
                    timings["gate_prepared_decision"] = prepared_gate["recommended_decision"]
                    assert prepared_gate["recommended_decision"] == "approved_live", (
                        prepared_gate["blocker"],
                        prepared_gate["blocker_detail"],
                    )
            else:
                await asyncio.sleep(max(0.0, float(answer_after) - (time.monotonic() - proposed_at)))
            route_before = prepared.route.route_revision

            # The participant says yes: the agreement commits, and the clock starts.
            agreed = await _tool(
                database,
                case=gated,
                session_id=session_id,
                name="record_acceptance_criteria",
                arguments={
                    "agreed_outcome": OUTCOME,
                    "workflow": WORKFLOW,
                    "criteria": criteria,
                    "as_proposed": True,
                },
            )
            agreed_at = time.monotonic()
            assert "acceptance_criteria_revision" in agreed, agreed
            if skip_proposal:
                recorded = await _row(
                    database,
                    "select prepared_draft_id from public.acceptance_criteria_agreements "
                    "where product_signal_id = %s",
                    case.product_signal_id,
                )
                assert recorded is not None and recorded["prepared_draft_id"] is not None, (
                    "the agreement drafted no change"
                )
                draft_id = recorded["prepared_draft_id"]
            timings["answered_after_proposal_seconds"] = round(agreed_at - proposed_at, 1)
            clock = await _row(database, "select clock_timestamp() as now")
            assert clock is not None

            async def routed() -> int | None:
                row = await _row(
                    database,
                    "select (projection->>'preview_route_revision')::int as revision "
                    "from public.session_projections where session_id = %s",
                    session_id,
                )
                revision = None if row is None else row["revision"]
                return revision if isinstance(revision, int) and revision > route_before else None

            await _until("the projection to name the new route", routed, every=_WATCH_SECONDS)
            routed_at = time.monotonic()

            # The extension reloads the preview tab; the door signs it into the
            # runtime the origin now serves, and the dashboard renders the change.
            route = await _row(
                database,
                "select allocations.staged_originals, versions.patch_text "
                "from public.preview_routes as routes "
                "join public.preview_runtime_allocations as allocations "
                "  on allocations.id = routes.preview_runtime_allocation_id "
                "join public.preview_versions as versions on versions.id = routes.preview_version_id "
                "where routes.interview_session_id = %s and routes.state = 'active'",
                session_id,
            )
            assert route is not None
            files = {
                str(item["path"]): str(item["content"])
                for item in json.loads(str(route["patch_text"]))
            }
            originals = route["staged_originals"]
            assert isinstance(originals, dict)
            expected = added_targets(
                files=files,
                originals={str(path): decoded_original(value) for path, value in originals.items()},
            )
            assert expected, "the change adds no declared control to look for"
            selector = ", ".join(f'[data-utrace-target="{target}"]' for target in sorted(expected))

            async def shown() -> bool | None:
                found = await browser.evaluate(
                    f"document.querySelector({json.dumps(selector)}) !== null"
                )
                return True if found else None

            # The tab reloads the page it shows. The candidate is another runtime,
            # so a product session it does not know goes through the door's own
            # sign-in into the runtime the origin now serves.
            await browser.gate(f"https://{host}{DASHBOARD_PATH}")
            try:
                await _until("the change's control", shown, every=_WATCH_SECONDS, within=3)
                timings["signed_in_again"] = False
            except AssertionError:
                timings["signed_in_again"] = True
                await browser.gate(f"https://{host}/__utrace/sign-in")
                page_state, page_text = await browser.gate(f"https://{host}{DASHBOARD_PATH}")
                assert page_state == "ready", page_text[:1000]
                await _until("the change's control", shown, every=_WATCH_SECONDS, within=30)
            page_up = time.monotonic()
            if os.environ.get("UTRACE_LIVE_CHANGE_DURING_VALIDATION") == "1":
                timings["change"] = await _change_during_validation(
                    database,
                    browser=browser,
                    case=gated,
                    session_id=session_id,
                    host=host,
                    swapped_at=routed_at,
                    page_up=page_up,
                    first_files=files,
                )

        stages = await _row(
            database,
            """
            select
              extract(epoch from (select min(created_at) from public.gate_decisions
                                   where product_signal_id = %s) - %s) as gate_decided,
              extract(epoch from (select min(created_at) from public.implementation_attempts
                                   where product_signal_id = %s) - %s) as attempt_authorized,
              extract(epoch from (select min(created_at) from public.preview_versions
                                   where product_signal_id = %s) - %s) as version_published,
              extract(epoch from (select min(created_at) from public.preview_routes
                                   where interview_session_id = %s and route_revision > %s) - %s)
                as route_switched
            """,
            case.product_signal_id,
            clock["now"],
            case.product_signal_id,
            clock["now"],
            case.product_signal_id,
            clock["now"],
            session_id,
            route_before,
            clock["now"],
        )
        assert stages is not None
        timings.update(
            {
                "yes_to_page_up_seconds": round(page_up - agreed_at, 2),
                "yes_to_route_seen_seconds": round(routed_at - agreed_at, 2),
                "route_seen_to_page_up_seconds": round(page_up - routed_at, 2),
                "stages_after_yes_seconds": {
                    name: None if value is None else round(float(str(value)), 2)
                    for name, value in stages.items()
                },
            }
        )

        # The full checks finish while the participant validates.
        async def settled() -> dict[str, object] | None:
            row = await _row(
                database,
                "select full_checks_state from public.preview_versions "
                "where product_signal_id = %s order by created_at desc limit 1",
                case.product_signal_id,
            )
            return row if row is not None and row["full_checks_state"] != "running" else None

        draft_ready = await _row(
            database,
            "select extract(epoch from settled_at - %s) as seconds "
            "from public.prepared_drafts where id = %s",
            proposed_clock["now"],
            draft_id,
        )
        assert draft_ready is not None
        timings["draft_proven_after_proposal_seconds"] = round(float(str(draft_ready["seconds"])), 1)
        checks = await _until("the version's full checks", settled)
        timings["full_checks_state"] = checks["full_checks_state"]
        async with database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "select kind, state, agent_cost_cents, files_digest, "
                "fast_checks -> 'build' as build from public.prepared_drafts "
                "where product_signal_id = %s order by proposed_at",
                (case.product_signal_id,),
            )
            drafts = await cur.fetchall()
        timings["drafts"] = [
            {
                "kind": row["kind"],
                "state": row["state"],
                "agent_cost_cents": row["agent_cost_cents"],
                "build": {
                    key: (row["build"] or {}).get(key)
                    for key in ("exit_code", "seconds", "reused", "restart", "restart_seconds")
                },
            }
            for row in drafts
        ]
        # The first proposal is the one that revised the intake draft.
        first = {row["kind"]: row["files_digest"] for row in reversed(drafts)}
        timings["proposal_changed_intake_files"] = first.get("intake") != first.get("proposal")
        timings["full_checks_after_page_up_seconds"] = round(time.monotonic() - page_up, 1)
        print("BUILD_SPEED_TIMINGS " + json.dumps(timings))
        assert checks["full_checks_state"] == "passed"
    finally:
        worker.request_stop()
        await asyncio.wait_for(working, timeout=600)
        async with database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "select id from public.preview_runtime_allocations "
                "where product_signal_id = %s and state <> 'terminated'",
                (case.product_signal_id,),
            )
            allocations = [row["id"] for row in await cur.fetchall()]
            await cur.execute(
                "select distinct sandbox_name from public.prepared_workspaces "
                "where product_signal_id = %s",
                (case.product_signal_id,),
            )
            workspaces = [row["sandbox_name"] for row in await cur.fetchall()]
        for allocation_id in allocations:
            await tear_down_allocation(
                database,
                provider=provider,
                activation=activation,
                allocation_id=allocation_id,
                reason="live_test_cleanup",
            )
        for name in workspaces:
            handle = await sandboxes.find(name=str(name))
            if handle is not None:
                await sandboxes.delete(handle)
