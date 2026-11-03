"""The whole preview path against real Vercel Sandbox, a real front door, real Chrome.

One test walks the path end to end because that is the only thing that proves
it: a warm runtime provisioned from the pinned template snapshot with a dormant
Server SDK, a claim that installs the allocation-bound bootstrap credential and
restarts the supervisor into an active one, an opaque session origin the real
preview front door serves, a participant's Chrome that claims it and renders the
seeded Dudulemon dashboard, the browser and server halves of one trace joining in
the control plane, a candidate built from a frozen artifact, and every sandbox
deleted afterwards.

The control plane runs on a real socket and is published at a temporary public
origin, because a Vercel sandbox reaches the internet and not a workstation.
It creates real sandboxes and deletes them in a ``finally`` regardless of the
outcome. It skips when the sandbox grant, template, or fixture variables are absent,
naming the one that is missing.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

import httpx
import pytest
from utrace_contracts.domain.lifecycle import (
    InterviewSessionState,
    PreviewRuntimeAllocationState,
    ProductSignalState,
)

from app.adapters.observation_readiness import IngestionObservationReadiness
from app.connectors.failures import ConnectorFailure
from app.db.pool import Database
from app.preview.activation import ServerActivationContext, restart_observed_runtime
from app.preview.artifacts import patch_digest_for, record_artifact
from app.preview.candidate import deploy_candidate
from app.preview.config import TemplateBuildSettings, get_preview_settings
from app.preview.handoff import mint_product_handoff
from app.preview.pool import load_runtime_secrets
from app.preview.provisioning import (
    prepare_session_preview,
    provision_warm_runtime,
)
from app.preview.readiness import route_context_token
from app.preview.recipe import TemplateRecipe
from app.preview.records import (
    PreviewRouteRecord,
    ReadinessOutcome,
    ReadinessProbe,
    ReadinessResult,
    RouteReadinessState,
)
from app.preview.recovery import (
    recover_route_after_restart,
    withdraw_readiness_for_restart,
)
from app.preview.routing import route_for_session
from app.preview.runtime import (
    PreviewRuntimeHandle,
    PreviewRuntimeProvider,
)
from app.preview.seeding import read_installed_dataset
from app.preview.spare import render_spare, serve_staged, stage_files, start_spare_candidate
from app.preview.teardown import tear_down_allocation
from app.preview.template import load_recipe
from app.settings import Settings
from tests.factories import (
    create_case,
    create_interview_session,
)
from tests.helpers import read_state
from tests.ingestion.control_observations import drain_ingestion_jobs
from tests.ingestion.keys import SigningKeys
from tests.preview.conftest import install_template
from tests.preview.live_fixtures import (
    MARKER,
    ORIGIN_SUFFIX,
    artifact_for,
    digest_of,
    publish_release,
    require_committed_template,
    start_corrected_version,
    start_preview_version,
)
from tests.preview.live_harness import (
    FrontDoor,
    PreviewTls,
    PublicTunnel,
    open_preview_in_chrome,
)
from tests.supabase_auth import SignedInUser

#: Where the fixture's preview handoff lands the signed-in growth lead, and a
#: value from the seeded dataset that only the real dashboard renders.
DASHBOARD_PATH = "/app/conversion"
DASHBOARD_MARKER = "Conversion"

#: How long the browser and correlation projections may take to follow the
#: traffic Chrome produced. Export, correlation, and readiness are three queued
#: derivations, so this outlasts several of their cycles.
_OBSERVATION_DEADLINE_SECONDS = 180.0


def _outcomes(
    results: Sequence[ReadinessResult],
) -> dict[ReadinessProbe, ReadinessOutcome]:
    return {result.probe: result.outcome for result in results}


async def _until[T](description: str, probe: Callable[[], Awaitable[T | None]]) -> T:
    """Poll until the probe answers, or fail naming what never happened."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _OBSERVATION_DEADLINE_SECONDS
    while loop.time() < deadline:
        outcome = await probe()
        if outcome is not None:
            return outcome
        await asyncio.sleep(1.0)
    raise AssertionError(f"timed out waiting for {description}")


async def _table_exists(
    *,
    provider: PreviewRuntimeProvider,
    handle: PreviewRuntimeHandle,
    recipe: TemplateRecipe,
    table: str,
) -> bool:
    """Whether one table exists in the runtime's own fixture schema.

    psql echoes ``SET`` for the ``search_path`` command even under ``-At``, so
    the answer is the last line rather than the whole output.
    """
    result = await provider.execute(
        handle=handle,
        script=(
            'psql -At -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            '-c "set search_path to \\"$SHOPALYTICS_DATABASE_SCHEMA\\"" '
            f"-c \"select to_regclass('{table}') is not null\""
        ),
        working_directory=recipe.application_directory,
        timeout_seconds=60,
    )
    if result.exit_code != 0:
        raise AssertionError(f"the table probe failed: {result.stdout[-500:]}")
    answers = result.stdout.split()
    if not answers or answers[-1] not in {"t", "f"}:
        raise AssertionError(f"the table probe answered {result.stdout!r}")
    return answers[-1] == "t"


async def test_the_preview_path_from_warm_pool_to_validated_observation(
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
) -> None:
    """Provision, claim, activate, route, observe, advance, tear down."""
    await require_committed_template(
        provider=provider,
        recipe=recipe,
        build_settings=build_settings,
        template_snapshot_id=template_snapshot_id,
        template_image=template_image,
    )
    case = await create_case(
        database,
        name="preview-live",
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=auth_users["participant"].auth_user_id,
    )
    release_id = await publish_release(
        database,
        case=case,
        build_settings=build_settings,
        keys=keys,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
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
    observation = IngestionObservationReadiness(database)
    live_settings = get_preview_settings()
    activation = ServerActivationContext.build(
        settings=live_settings,
        lane_signing_key=settings.lane_signing_key,
        service_token=settings.front_door_service_token,
        manifest_key_id=keys.manifest_key_id,
        manifest_public_key=keys.manifest_private.public_key().public_bytes_raw(),
    )
    assert activation.endpoints.hosts() == (tunnel.host,)

    created: list[UUID] = []
    try:
        base_id = await provision_warm_runtime(
            database,
            provider=provider,
            client=probe_client,
            template=template,
            recipe=recipe,
            settings=live_settings,
            activation=activation,
            origin_suffix=ORIGIN_SUFFIX,
        )
        created.append(base_id)
        state, _ = await _state(database, base_id)
        assert state == PreviewRuntimeAllocationState.READY.value
        # A warm runtime carries no Interview Session, so its Server SDK is
        # dormant and no activation exists for it yet.
        dormant = await observation.activation(
            client_id=case.client_id,
            interview_session_id=session_id,
            preview_runtime_allocation_id=base_id,
        )
        assert not dormant.activated

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
        outcomes = _outcomes(prepared.report.results)
        assert outcomes[ReadinessProbe.SERVICE_HEALTH] is ReadinessOutcome.PASSED
        assert outcomes[ReadinessProbe.DATA_IDENTITY] is ReadinessOutcome.PASSED
        assert outcomes[ReadinessProbe.ROUTE_RESOLUTION] is ReadinessOutcome.PASSED
        assert outcomes[ReadinessProbe.STALE_ROUTE_REJECTION] is ReadinessOutcome.PASSED
        assert outcomes[ReadinessProbe.AUTHENTICATED_HANDOFF] is ReadinessOutcome.PASSED
        # The claim installed the allocation-bound credential and restarted the
        # runtime, so its own Server SDK activated before the route was proved.
        assert outcomes[ReadinessProbe.SERVER_SDK_ACTIVATION] is ReadinessOutcome.PASSED
        assert prepared.report.preview_entry_token is not None
        # No participant has arrived, so the halves only they produce are not
        # ready and are recorded as such rather than assumed.
        assert outcomes[ReadinessProbe.BROWSER_OBSERVATION] is ReadinessOutcome.FAILED
        assert outcomes[ReadinessProbe.TRACE_CORRELATION] is ReadinessOutcome.FAILED

        observed = await observation.activation(
            client_id=case.client_id,
            interview_session_id=session_id,
            preview_runtime_allocation_id=prepared.pins.preview_runtime_allocation_id,
        )
        assert observed.activated
        assert observed.service_name == live_settings.server_service_name
        assert observed.preview_entry_token == prepared.report.preview_entry_token

        state, _ = await _state(database, base_id)
        assert state == PreviewRuntimeAllocationState.BOUND.value
        assert prepared.route.public_preview_host.endswith(f".{ORIGIN_SUFFIX}")
        assert prepared.route.upstream.application_port == recipe.application_port

        # The door resolves the session origin through the control plane and
        # serves the product; a bare origin with no claim has no authority.
        bare = await probe_client.get(
            f"{door.base_url}/",
            headers=door.headers_for(prepared.route.public_preview_host),
        )
        assert bare.status_code == 404

        claim_url = await _mint_claim(
            api,
            session_id=session_id,
            participant=auth_users["participant"],
            public_preview_host=prepared.route.public_preview_host,
        )
        # A real browser claims the session origin through the real door: the
        # claim exchanges the one-time code for the origin-bound cookie, and the
        # door then serves the client product's own document, gate and all.
        async with open_preview_in_chrome(
            session_origin=prepared.route.public_preview_host, tls=door_tls
        ) as browser:
            state, text = await browser.gate(claim_url)
            assert (await browser.url()).startswith(
                f"https://{prepared.route.public_preview_host}"
            )
            assert state != "absent", (
                "the door served no Shopalytics document to the browser: " + text[:1000]
            )
            assert state == "ready", (
                "the embedded browser SDK did not reach a ready observation "
                f"runtime; the gate reported {state}: {text[:1000]}"
            )

            # The originating user signs in through the product's own preview
            # handoff, exactly as the readiness probe proved it server-side, and
            # then opens the dashboard. Only a page that queries the product
            # produces the browser CLIENT span the server span joins under.
            runtime_secrets = await load_runtime_secrets(
                database, allocation_id=prepared.pins.preview_runtime_allocation_id
            )
            signed_in = await browser.post_json(
                recipe.handoff_path,
                {
                    "token": mint_product_handoff(
                        handoff_secret=runtime_secrets.handoff_secret,
                        audience_host=prepared.route.public_preview_host,
                        subject_email=prepared.pins.authenticated_identity,
                        lifetime_seconds=60,
                    ).token
                },
            )
            assert signed_in == 200
            dashboard, rendered = await browser.gate(
                f"https://{prepared.route.public_preview_host}{DASHBOARD_PATH}"
            )
            assert dashboard == "ready", rendered[:1000]
            assert DASHBOARD_MARKER in rendered, rendered[:2000]

            # The participant's halves are read back through the same port the
            # preview stream uses, while their browser is still open: closing it
            # would end the stream whose readiness is the thing being observed.
            #
            # Trace correlation is not asserted here. It is the one observation
            # answer this area cannot reach on its own: it needs the browser
            # CLIENT span and every hop the signed server manifest declares to
            # join under one trace, which is the browser SDK's and the ingestion
            # stream's contract.
            await _until(
                "browser observation and the Server SDK activation to be ready",
                lambda: _observation_ready(
                    database,
                    observation_port=observation,
                    client_id=case.client_id,
                    interview_session_id=session_id,
                    preview_runtime_allocation_id=prepared.pins.preview_runtime_allocation_id,
                ),
            )

        base_handle = await provider.attach(
            runtime_ref=prepared.pins.runtime_ref,
            application_port=prepared.pins.application_port,
        )
        installed = await read_installed_dataset(
            provider=provider, handle=base_handle, recipe=recipe
        )
        assert installed.content_hash == template.seed.manifest_digest
        assert not await _table_exists(
            provider=provider,
            handle=base_handle,
            recipe=recipe,
            table="saved_conversion_views",
        )

        await _prove_the_runtime_rejects_foreign_route_authority(
            probe_client, record=prepared.route
        )

        # A runtime restart withdraws readiness until a new revision is committed.
        await withdraw_readiness_for_restart(
            database, interview_session_id=session_id, reason="runtime_restart"
        )
        refused = await api.post(
            f"/v1/sessions/{session_id}/preview-handoff",
            headers={
                "Authorization": f"Bearer {auth_users['participant'].access_token}"
            },
        )
        assert refused.status_code == 409
        assert "readiness is withdrawn" in refused.json()["detail"]
        # The door's reconnecting surface needs the session cookie, which only
        # the participant's browser holds, so the withdrawal is read where it is
        # durable: on the route revision itself.
        withdrawn_route = await route_for_session(
            database, interview_session_id=session_id
        )
        assert withdrawn_route is not None
        assert withdrawn_route.readiness_state is RouteReadinessState.RECONNECTING

        await restart_observed_runtime(
            database,
            provider=provider,
            commands=activation.commands,
            client_id=case.client_id,
            preview_runtime_allocation_id=prepared.pins.preview_runtime_allocation_id,
            handle=base_handle,
            supervisor_command=recipe.supervisor_command,
            application_directory=recipe.application_directory,
        )
        recovered = await recover_route_after_restart(
            database,
            provider=provider,
            client=probe_client,
            settings=live_settings,
            interview_session_id=session_id,
            template=template,
            observation_port=observation,
        )
        assert recovered.route.route_revision == 2
        assert recovered.route.public_preview_host == prepared.route.public_preview_host
        # The sandbox and its port URL survive a supervisor restart; the new
        # revision is what re-proves the path.
        assert recovered.route.upstream.port_url == prepared.route.upstream.port_url
        base_handle = await provider.attach(
            runtime_ref=prepared.pins.runtime_ref,
            application_port=prepared.pins.application_port,
        )

        version_id, attempt_id = await start_preview_version(database, case=case)
        artifact = artifact_for(
            case=case,
            preview_version_id=version_id,
            attempt_id=attempt_id,
            template=template,
        )
        await record_artifact(database, artifact=artifact)
        # The session's spare starts beside the interview, and the candidate's files
        # are staged into it as they are while an attempt's checks run; the
        # deployment serves that staging because its files match the artifact.
        candidate_recipe = await load_recipe(database, template_id=template.id)
        spare_started = time.monotonic()
        spare = await start_spare_candidate(
            database,
            provider=provider,
            client=probe_client,
            settings=live_settings,
            template=template,
            recipe=candidate_recipe,
            interview_session_id=session_id,
            activation=activation,
        )
        created.append(spare.preview_runtime_allocation_id)
        spare_ready = time.monotonic()
        staged = await stage_files(
            database,
            provider=provider,
            recipe=candidate_recipe,
            spare=spare,
            files=artifact.files,
        )
        assert staged.file_digests == {item.path: item.digest for item in artifact.files}
        route_now = await route_for_session(database, interview_session_id=session_id)
        assert route_now is not None
        staged_at = time.monotonic()
        _, build = await serve_staged(
            database,
            provider=provider,
            client=probe_client,
            recipe=candidate_recipe,
            spare=spare,
            staged=staged,
            files=artifact.files,
            activation=activation,
            observation_port=observation,
            client_id=case.client_id,
            interview_session_id=session_id,
            public_preview_host=route_now.public_preview_host,
        )
        assert build.passed, build.output_tail
        built_at = time.monotonic()
        spare_render = await render_spare(
            client=probe_client,
            recipe=candidate_recipe,
            spare=spare,
            current=route_now,
            pages=("/candidate-marker",),
            expected_targets=frozenset(),
        )
        assert spare_render.passed, spare_render.failures
        rendered_at = time.monotonic()
        deployed = await deploy_candidate(
            database,
            provider=provider,
            client=probe_client,
            settings=live_settings,
            template=template,
            recipe=candidate_recipe,
            artifact=artifact,
            interview_session_id=session_id,
            activation=activation,
            observation_port=observation,
            spare=spare,
        )
        assert deployed.preview_runtime_allocation_id == spare.preview_runtime_allocation_id
        print(
            "BUILD_SPEED_TIMINGS "
            + json.dumps(
                {
                    "spare_start_seconds": round(spare_ready - spare_started, 1),
                    "stage_seconds": round(staged_at - spare_ready, 1),
                    "build_seconds": round(build.seconds, 1),
                    "restart_seconds": round(build.restart_seconds, 1),
                    "render_check_seconds": round(rendered_at - built_at, 1),
                    "deploy_and_swap_seconds": round(time.monotonic() - rendered_at, 1),
                    "migrations": len(artifact.migrations),
                }
            )
        )
        assert deployed.route.route_revision == 3
        assert deployed.route.public_preview_host == prepared.route.public_preview_host
        assert deployed.route.preview_version_id == version_id
        # The candidate is a separate allocation, so it activated its own stream.
        candidate_activation = await observation.activation(
            client_id=case.client_id,
            interview_session_id=session_id,
            preview_runtime_allocation_id=deployed.preview_runtime_allocation_id,
        )
        assert candidate_activation.activated

        current = await route_for_session(database, interview_session_id=session_id)
        assert current is not None
        assert current.route_revision == 3

        candidate_handle = await provider.attach(
            runtime_ref=(
                await _runtime_ref(database, deployed.preview_runtime_allocation_id)
            ),
            application_port=recipe.application_port,
        )
        assert await _table_exists(
            provider=provider,
            handle=candidate_handle,
            recipe=recipe,
            table="saved_conversion_views",
        )
        assert not await _table_exists(
            provider=provider,
            handle=base_handle,
            recipe=recipe,
            table="saved_conversion_views",
        )

        marker = await probe_client.get(
            f"{deployed.route.upstream.port_url.rstrip('/')}/candidate-marker",
            headers={
                "x-utrace-route-context": route_context_token(
                    record=deployed.route,
                    signing_secret=deployed.route.route_context_signing_secret,
                    lifetime_seconds=60,
                ),
            },
        )
        assert marker.status_code == 200, marker.text
        assert MARKER in marker.text

        stale = await probe_client.get(
            f"{deployed.route.upstream.port_url.rstrip('/')}/candidate-marker",
            headers={
                "x-utrace-route-context": route_context_token(
                    record=deployed.route,
                    signing_secret=prepared.route.route_context_signing_secret,
                    lifetime_seconds=60,
                ),
            },
        )
        assert stale.status_code == 401

        # A corrected version swaps in from the session's next spare. It carries no
        # migration, so its swap costs what an ordinary candidate's does.
        corrected_id = await start_corrected_version(database, case=case, attempt_id=attempt_id)
        first = artifact_for(
            case=case,
            preview_version_id=corrected_id,
            attempt_id=attempt_id,
            template=template,
        )
        page = first.files[0].model_copy(
            update={
                "content": first.files[0].content.replace("</main>", " corrected</main>"),
                "digest": digest_of(first.files[0].content.replace("</main>", " corrected</main>")),
            }
        )
        corrected = first.model_copy(
            update={
                "files": (page,),
                "migrations": (),
                "source_manifest": {page.path: page.digest},
                "patch_digest": patch_digest_for({page.path: page.digest}),
            }
        )
        await record_artifact(database, artifact=corrected)
        next_started = time.monotonic()
        next_spare = await start_spare_candidate(
            database,
            provider=provider,
            client=probe_client,
            settings=live_settings,
            template=template,
            recipe=candidate_recipe,
            interview_session_id=session_id,
            activation=activation,
        )
        created.append(next_spare.preview_runtime_allocation_id)
        next_ready = time.monotonic()
        next_staged_files = await stage_files(
            database,
            provider=provider,
            recipe=candidate_recipe,
            spare=next_spare,
            files=corrected.files,
        )
        route_now = await route_for_session(database, interview_session_id=session_id)
        assert route_now is not None
        _, next_build = await serve_staged(
            database,
            provider=provider,
            client=probe_client,
            recipe=candidate_recipe,
            spare=next_spare,
            staged=next_staged_files,
            files=corrected.files,
            activation=activation,
            observation_port=observation,
            client_id=case.client_id,
            interview_session_id=session_id,
            public_preview_host=route_now.public_preview_host,
        )
        assert next_build.passed, next_build.output_tail
        next_staged = time.monotonic()
        next_render = await render_spare(
            client=probe_client,
            recipe=candidate_recipe,
            spare=next_spare,
            current=route_now,
            pages=("/candidate-marker",),
            expected_targets=frozenset(),
        )
        assert next_render.passed, next_render.failures
        next_rendered = time.monotonic()
        swapped = await deploy_candidate(
            database,
            provider=provider,
            client=probe_client,
            settings=live_settings,
            template=template,
            recipe=candidate_recipe,
            artifact=corrected,
            interview_session_id=session_id,
            activation=activation,
            observation_port=observation,
            spare=next_spare,
        )
        print(
            "BUILD_SPEED_TIMINGS "
            + json.dumps(
                {
                    "next_spare_start_seconds": round(next_ready - next_started, 1),
                    "stage_seconds": round(next_staged - next_ready, 1),
                    "render_check_seconds": round(next_rendered - next_staged, 1),
                    "deploy_and_swap_seconds": round(time.monotonic() - next_rendered, 1),
                    "migrations": 0,
                }
            )
        )
        assert swapped.route.route_revision == 4
        assert swapped.route.preview_version_id == corrected_id
    finally:
        for allocation_id in created:
            await tear_down_allocation(
                database,
                provider=provider,
                activation=activation,
                allocation_id=allocation_id,
                reason="live_test_cleanup",
            )

    for allocation_id in created:
        state, _ = await _state(database, allocation_id)
        assert state == PreviewRuntimeAllocationState.TERMINATED.value
        with pytest.raises(ConnectorFailure):
            await provider.attach(
                runtime_ref=await _runtime_ref(database, allocation_id),
                application_port=recipe.application_port,
            )
    # Every Server SDK stream ended with a terminal report before the sandbox
    # that held it was destroyed, including the one a supervisor restart replaced.
    open_streams, streams = await _server_streams(database)
    assert streams >= len(created)
    assert open_streams == 0


async def _observation_ready(
    database: Database,
    *,
    observation_port: IngestionObservationReadiness,
    client_id: UUID,
    interview_session_id: UUID,
    preview_runtime_allocation_id: UUID,
) -> bool | None:
    """Drain the queued derivations and read the participant's halves from the port.

    It answers once the Server SDK has activated and the browser observation path
    is ready, the two the participant's own traffic drives.
    """
    await drain_ingestion_jobs(database)
    activation = await observation_port.activation(
        client_id=client_id,
        interview_session_id=interview_session_id,
        preview_runtime_allocation_id=preview_runtime_allocation_id,
    )
    readiness = await observation_port.readiness(
        client_id=client_id, interview_session_id=interview_session_id
    )
    return True if activation.activated and readiness.browser_ready else None


async def _server_streams(database: Database) -> tuple[int, int]:
    """Count the server streams still open, and all of them."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select count(*) filter (where state = 'open') as open_streams,
                   count(*) as streams
            from public.server_streams
            """
        )
        row = await cur.fetchone()
    if row is None:
        raise AssertionError("the stream count returned no row")
    return int(str(row["open_streams"])), int(str(row["streams"]))


async def _prove_the_runtime_rejects_foreign_route_authority(
    client: httpx.AsyncClient, *, record: PreviewRouteRecord
) -> None:
    """A context signed with a secret this runtime never received is refused."""
    response = await client.get(
        record.upstream.port_url.rstrip("/") + "/",
        headers={
            "x-utrace-route-context": route_context_token(
                record=record,
                signing_secret="a-secret-this-runtime-never-received",
                lifetime_seconds=60,
            ),
        },
    )
    assert response.status_code == 401


async def _mint_claim(
    api: httpx.AsyncClient,
    *,
    session_id: UUID,
    participant: SignedInUser,
    public_preview_host: str,
) -> str:
    """Mint the session's one-time claim and return the URL Chrome opens.

    The URL is the control plane's own: the session origin resolves to the TLS
    front this run put before the door, so nothing about it is rewritten.
    """
    minted = await api.post(
        f"/v1/sessions/{session_id}/preview-handoff",
        headers={"Authorization": f"Bearer {participant.access_token}"},
    )
    assert minted.status_code == 200, minted.text
    url = str(minted.json()["front_door_url"])
    assert url.startswith(f"https://{public_preview_host}/__utrace/claim?code=")
    return url


async def _state(database: Database, allocation_id: UUID) -> tuple[str, int]:
    return await read_state(
        database,
        table="preview_runtime_allocations",
        row_id=allocation_id,
        column="state",
    )


async def _runtime_ref(database: Database, allocation_id: UUID) -> str:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select runtime_ref from public.preview_runtime_allocations where id = %s",
            (allocation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AssertionError(f"allocation {allocation_id} does not exist")
    return str(row["runtime_ref"])
