"""A spare candidate runtime takes one set of staged files at a time, and renders them.

The spare's sandbox is an in-memory file system here, so the tests are about what
the control plane records and restores, not about a real development server; the
live preview test covers that end to end.
"""

from __future__ import annotations

import hashlib
import shlex
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import httpx
import pytest
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import InterviewSessionState, ProductSignalState

from app.connectors.ports import WorkspaceExecution
from app.connectors.vercel_sandboxes import SandboxHandle
from app.db.pool import Database
from app.errors import GuardRejectedError
from app.ids import uuid7
from app.preview.activation import ServerActivationContext
from app.preview.candidate import prepared_readiness
from app.preview.ports import ObservationReadinessPort
from app.preview.readiness import ProbeTarget
from app.preview.recipe import TemplateRecipe
from app.preview.records import CandidateFile, ReadinessReport
from app.preview.render_check import added_targets, pages_for_targets, run_render_check
from app.preview.runtime import PreviewRuntimeHandle, PreviewRuntimeProvider
from app.preview.schema_delta import SchemaMode
from app.preview.spare import (
    ServedBuild,
    SpareRuntime,
    request_spare_candidate,
    serve_staged,
    session_needs_spare,
    settle_standby_spares,
    stage_files,
    standby_disposition,
    take_spare_candidate,
)
from tests.factories import CaseFixture, create_case, create_interview_session
from tests.supabase_auth import SignedInUser

ROOT = "/srv/shopalytics"
FILTER_BAR = "src/components/dashboard/FilterBar.tsx"
BASE_FILTER_BAR = '<button data-utrace-target="conversion_filter_reset">Reset</button>\n'
SAVE_CONTROL = '<button data-utrace-target="saved_view_save_control">Save view</button>\n'


class MemoryRuntime:
    """The three file operations staging uses, over an in-memory file system."""

    def __init__(self, files: Mapping[str, str], *, build_exit_code: int = 0) -> None:
        self.files = {f"{ROOT}/{path}": body for path, body in files.items()}
        self.scripts: list[str] = []
        self._build_exit_code = build_exit_code

    async def execute(
        self, *, handle: PreviewRuntimeHandle, script: str, working_directory: str, timeout_seconds: int
    ) -> WorkspaceExecution:
        del handle, working_directory, timeout_seconds
        self.scripts.append(script)
        command, *arguments = shlex.split(script)
        exit_code = 0
        if command == "shopalytics-rebuild":
            exit_code = self._build_exit_code
        elif command == "test":
            exit_code = 0 if arguments[-1] in self.files else 1
        elif command == "rm":
            for path in arguments[1:]:
                self.files.pop(path, None)
        return WorkspaceExecution(command=script, exit_code=exit_code, stdout="", duration_seconds=0.0)

    async def read_file(self, *, handle: PreviewRuntimeHandle, path: str) -> bytes:
        del handle
        return self.files[path].encode("utf-8")

    async def write_file(self, *, handle: PreviewRuntimeHandle, path: str, content: bytes) -> None:
        del handle
        self.files[path] = content.decode("utf-8")


def _file(path: str, content: str) -> CandidateFile:
    return CandidateFile(
        path=path,
        content=content,
        digest=f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}",
    )


def _recipe() -> TemplateRecipe:
    return cast(TemplateRecipe, _RecipeStub())


class _RecipeStub:
    application_directory = ROOT
    handoff_path = "/api/utrace/preview-handoff"
    schema_path = "db/schema.sql"
    schema_command = "psql -1 -f {file}"
    rebuild_command = "shopalytics-rebuild"
    restart_application_command = "shopalytics-restart-app"


async def _spare(database: Database, case: CaseFixture, session_id: UUID) -> SpareRuntime:
    allocation_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.preview_runtime_allocations (
                id, client_id, product_signal_id, allocation_kind, pool_role, state,
                interview_session_id, runtime_ref, spare_ready_at, fencing_token
            )
            values (%s, %s, %s, 'candidate', 'candidate', 'seeding', %s, %s, now(),
                    utrace.next_fencing_token())
            """,
            (allocation_id, case.client_id, case.product_signal_id, session_id, "spare-under-test"),
        )
    handle = PreviewRuntimeHandle(
        runtime_ref="spare-under-test",
        application_port=3300,
        sandbox=cast(SandboxHandle, None),
    )
    return SpareRuntime(
        preview_runtime_allocation_id=allocation_id,
        handle=handle,
        upstream_url="https://spare.example.test",
        handoff_secret="handoff-secret",
        route_secret="route-secret",
        authenticated_identity="maya@dudulemon.example.com",
    )


async def _case(database: Database, owner: SignedInUser, name: str) -> tuple[CaseFixture, UUID]:
    case = await create_case(
        database,
        name=name,
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=owner.auth_user_id,
        participant_auth_user_id=None,
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    return case, session_id


async def test_staging_a_new_set_restores_what_the_earlier_set_replaced(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case, session_id = await _case(database, auth_users["owner_a"], "spare-restage")
    spare = await _spare(database, case, session_id)
    runtime = MemoryRuntime({FILTER_BAR: BASE_FILTER_BAR})
    provider = cast(PreviewRuntimeProvider, runtime)
    draft_component = "src/components/dashboard/SavedViews.tsx"

    first = await stage_files(
        database,
        provider=provider,
        recipe=_recipe(),
        spare=spare,
        files=(_file(FILTER_BAR, BASE_FILTER_BAR + SAVE_CONTROL), _file(draft_component, SAVE_CONTROL)),
    )
    assert first.originals == {FILTER_BAR: BASE_FILTER_BAR, draft_component: None}
    assert runtime.files[f"{ROOT}/{draft_component}"] == SAVE_CONTROL

    # A newer proposal's draft touches only the filter bar: the earlier draft's new
    # component is removed, and the filter bar keeps its true base as its original.
    second = await stage_files(
        database,
        provider=provider,
        recipe=_recipe(),
        spare=spare,
        files=(_file(FILTER_BAR, BASE_FILTER_BAR + "<span>later</span>\n"),),
    )
    assert f"{ROOT}/{draft_component}" not in runtime.files
    assert second.originals == {FILTER_BAR: BASE_FILTER_BAR}
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select staged_files from public.preview_runtime_allocations where id = %s",
            (spare.preview_runtime_allocation_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["staged_files"] == dict(second.file_digests)


async def test_a_spare_that_serves_a_version_is_never_staged_again(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case, session_id = await _case(database, auth_users["owner_a"], "spare-bound")
    spare = await _spare(database, case, session_id)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.preview_runtime_allocations set state = 'bound' where id = %s",
            (spare.preview_runtime_allocation_id,),
        )
    runtime = MemoryRuntime({FILTER_BAR: BASE_FILTER_BAR})

    with pytest.raises(GuardRejectedError, match="never staged again"):
        await stage_files(
            database,
            provider=cast(PreviewRuntimeProvider, runtime),
            recipe=_recipe(),
            spare=spare,
            files=(_file(FILTER_BAR, SAVE_CONTROL),),
        )
    assert runtime.files[f"{ROOT}/{FILTER_BAR}"] == BASE_FILTER_BAR


async def test_a_session_queues_one_spare_until_it_has_one(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case, session_id = await _case(database, auth_users["owner_a"], "spare-request")

    async with database.transaction() as conn:
        first = await request_spare_candidate(
            conn,
            client_id=case.client_id,
            product_signal_id=case.product_signal_id,
            interview_session_id=session_id,
        )
    async with database.transaction() as conn:
        again = await request_spare_candidate(
            conn,
            client_id=case.client_id,
            product_signal_id=case.product_signal_id,
            interview_session_id=session_id,
        )

    assert (first, again) == (True, False)
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as queued from public.jobs where job_type = %s and input->>'interview_session_id' = %s",
            ("preview.prepare_spare_candidate", str(session_id)),
        )
        row = await cur.fetchone()
    assert row is not None and row["queued"] == 1


def test_the_targets_a_change_adds_are_the_ones_its_base_did_not_declare() -> None:
    added = added_targets(
        files={FILTER_BAR: BASE_FILTER_BAR + SAVE_CONTROL, "src/New.tsx": SAVE_CONTROL},
        originals={FILTER_BAR: BASE_FILTER_BAR, "src/New.tsx": None},
    )
    assert added == frozenset({"saved_view_save_control"})
    manifest = {
        "targets": [
            {"definition_id": "saved_view_save_control", "route_templates": ["/app/conversion"]},
            {"definition_id": "product_detail", "route_templates": ["/app/products/[id]"]},
        ]
    }
    assert pages_for_targets(targets=added, annotation_manifest=manifest) == ("/app/conversion",)
    assert pages_for_targets(targets={"product_detail"}, annotation_manifest=manifest) == ()


def _render_transport(*, page_status: int, chunk: str) -> httpx.MockTransport:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/utrace/preview-handoff":
            return httpx.Response(200, json={"redirect_to": "/app/conversion"})
        if request.url.path == "/app/conversion":
            return httpx.Response(page_status, text='<script src="/_next/static/chunks/page.js"></script>')
        return httpx.Response(200, text=chunk)

    return httpx.MockTransport(respond)


@pytest.mark.parametrize(
    ("page_status", "chunk", "passed", "missing"),
    [
        (200, '{"data-utrace-target":"saved_view_save_control"}', True, ()),
        (200, '{"data-utrace-target":"conversion_filter_reset"}', False, ("saved_view_save_control",)),
        (500, "", False, ("saved_view_save_control",)),
    ],
)
async def test_the_render_check_needs_the_page_and_every_added_target_in_its_modules(
    page_status: int, chunk: str, passed: bool, missing: tuple[str, ...]
) -> None:
    transport = _render_transport(page_status=page_status, chunk=chunk)
    async with httpx.AsyncClient(transport=transport) as browser:
        check = await run_render_check(
            browser=browser,
            target=ProbeTarget(
                base_url="https://spare.example.test",
                public_preview_host="probe.preview.utrace.dev",
                route_context="signed",
                public_path=False,
            ),
            recipe=_recipe(),
            handoff_secret="handoff-secret-handoff-secret-handoff-secret",
            authenticated_identity="maya@dudulemon.example.com",
            pages=None,
            expected_targets=frozenset({"saved_view_save_control"}),
        )
    assert check.passed is passed
    assert check.missing_targets == missing
    assert check.pages == ("/app/conversion",)


SCHEMA = "db/schema.sql"
BASE_SCHEMA = "create table products (id uuid primary key, name text not null);\n"
SAVED_VIEWS = "create table saved_views (id uuid primary key, filters jsonb not null);\n"


class _ActivatedAgain:
    """The observation port, reporting that the restarted process's Server SDK activated."""

    async def activation(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(activated=True, activated_at=datetime.now(tz=UTC) + timedelta(seconds=1))


async def _serve(
    database: Database,
    provider: PreviewRuntimeProvider,
    spare: SpareRuntime,
    session_id: UUID,
    files: tuple[CandidateFile, ...],
) -> tuple[SchemaMode, ServedBuild]:
    staged = await stage_files(database, provider=provider, recipe=_recipe(), spare=spare, files=files)
    delta, build = await serve_staged(
        database,
        provider=provider,
        client=cast(httpx.AsyncClient, None),
        recipe=_recipe(),
        spare=spare,
        staged=staged,
        files=files,
        # The spare has no live server stream, so ending one sends no command.
        activation=cast(ServerActivationContext, SimpleNamespace(commands=None)),
        observation_port=cast(ObservationReadinessPort, _ActivatedAgain()),
        client_id=uuid7(),
        interview_session_id=session_id,
        public_preview_host="maya.preview.utrace.dev",
    )
    return delta.mode, build


async def test_a_spare_builds_its_staged_files_beside_their_additive_schema_once(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case, session_id = await _case(database, auth_users["owner_a"], "spare-schema")
    spare = await _spare(database, case, session_id)
    runtime = MemoryRuntime({SCHEMA: BASE_SCHEMA})
    provider = cast(PreviewRuntimeProvider, runtime)
    files = (_file(SCHEMA, BASE_SCHEMA + SAVED_VIEWS),)

    mode, build = await _serve(database, provider, spare, session_id, files)
    assert mode is SchemaMode.IN_PLACE
    assert build.passed and not build.reused and build.restart == "application"
    assert {"shopalytics-rebuild", "shopalytics-restart-app", "psql -1 -f /tmp/utrace-schema-delta.sql"} <= set(
        runtime.scripts
    )
    assert runtime.files["/tmp/utrace-schema-delta.sql"] == SAVED_VIEWS.strip().rstrip(";") + ";\n"
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select staged_schema, built_files from public.preview_runtime_allocations "
            "where id = %s",
            (spare.preview_runtime_allocation_id,),
        )
        row = await cur.fetchone()
    assert row is not None and row["staged_schema"]["mode"] == "in_place"
    assert row["built_files"] == {SCHEMA: files[0].digest}

    # The same files again: the spare already serves their build and their schema.
    runtime.scripts.clear()
    mode, build = await _serve(database, provider, spare, session_id, files)
    assert build.reused and build.restart == "none"
    assert not [script for script in runtime.scripts if script.startswith(("psql", "shopalytics-"))]


async def test_a_build_that_fails_is_not_served(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case, session_id = await _case(database, auth_users["owner_a"], "spare-build-fails")
    spare = await _spare(database, case, session_id)
    runtime = MemoryRuntime({FILTER_BAR: BASE_FILTER_BAR}, build_exit_code=1)
    provider = cast(PreviewRuntimeProvider, runtime)

    _, build = await _serve(
        database, provider, spare, session_id, (_file(FILTER_BAR, BASE_FILTER_BAR + SAVE_CONTROL),)
    )
    assert not build.passed and build.restart == "none"
    assert "shopalytics-restart-app" not in runtime.scripts
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select built_files from public.preview_runtime_allocations where id = %s",
            (spare.preview_runtime_allocation_id,),
        )
        row = await cur.fetchone()
    assert row is not None and row["built_files"] is None


async def test_a_proof_stands_only_for_its_version_its_revision_and_its_window(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case, session_id = await _case(database, auth_users["owner_a"], "spare-proof")
    spare = await _spare(database, case, session_id)
    allocation_id = spare.preview_runtime_allocation_id
    version_id = uuid7()
    report = ReadinessReport(
        preview_runtime_allocation_id=allocation_id,
        preview_version_id=version_id,
        route_revision=2,
        results=(),
        gating_probes=(),
        preview_entry_token=None,
    )

    async def prove(*, seconds_ago: int) -> None:
        async with database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                update public.preview_runtime_allocations
                set prepared_route = %s, prepared_readiness = %s,
                    prepared_at = now() - make_interval(secs => %s)
                where id = %s
                """,
                (
                    Jsonb({"preview_version_id": str(version_id), "route_revision": 2}),
                    Jsonb(report.model_dump(mode="json")),
                    seconds_ago,
                    allocation_id,
                ),
            )

    await prove(seconds_ago=5)
    assert await prepared_readiness(
        database, allocation_id=allocation_id, preview_version_id=version_id, route_revision=2
    ) == report
    for other_version, other_revision in ((uuid7(), 2), (version_id, 3)):
        assert await prepared_readiness(
            database,
            allocation_id=allocation_id,
            preview_version_id=other_version,
            route_revision=other_revision,
        ) is None
    await prove(seconds_ago=600)
    assert await prepared_readiness(
        database, allocation_id=allocation_id, preview_version_id=version_id, route_revision=2
    ) is None

    # Staging other files into the spare withdraws the proof outright.
    await prove(seconds_ago=5)
    await stage_files(
        database,
        provider=cast(PreviewRuntimeProvider, MemoryRuntime({})),
        recipe=_recipe(),
        spare=spare,
        files=(_file(FILTER_BAR, SAVE_CONTROL),),
    )
    assert await prepared_readiness(
        database, allocation_id=allocation_id, preview_version_id=version_id, route_revision=2
    ) is None


def test_a_standby_spare_becomes_the_next_spare_only_for_the_served_files() -> None:
    served = {FILTER_BAR: "sha256:served"}
    assert standby_disposition(built_files=served, ready=True, served_files=served) == "release"
    assert standby_disposition(
        built_files={FILTER_BAR: "sha256:stale"}, ready=True, served_files=served
    ) == "drop"
    assert standby_disposition(built_files=None, ready=True, served_files=served) == "drop"
    # Still building: its own Job settles it when it is done.
    assert standby_disposition(built_files=None, ready=False, served_files=served) == "keep"


async def _mark(database: Database, spare: SpareRuntime, **columns: object) -> None:
    assignments = ", ".join(f"{name} = %s" for name in columns)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            f"update public.preview_runtime_allocations set {assignments} where id = %s",
            (*(Jsonb(value) if isinstance(value, dict) else value for value in columns.values()),
             spare.preview_runtime_allocation_id),
        )


async def test_staging_never_takes_the_standby_and_a_swap_releases_it(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case, session_id = await _case(database, auth_users["owner_a"], "spare-standby")
    standby = await _spare(database, case, session_id)
    served = {FILTER_BAR: _file(FILTER_BAR, SAVE_CONTROL).digest}
    await _mark(database, standby, standby_draft_id=uuid7(), built_files=served)

    # The standby is reserved for the version about to be served: no candidate is
    # staged in it, and the session still needs a spare to stage one in.
    provider = cast(PreviewRuntimeProvider, MemoryRuntime({}))
    assert await take_spare_candidate(
        database, provider=provider, interview_session_id=session_id
    ) is None
    assert await session_needs_spare(database, interview_session_id=session_id)

    # Once that version is served, the standby is the session's next spare.
    assert await settle_standby_spares(
        database,
        provider=provider,
        activation=cast(ServerActivationContext, None),
        client_id=case.client_id,
        interview_session_id=session_id,
        served_files=served,
    )
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select standby_draft_id, spare_ready_at is not null as ready "
            "from public.preview_runtime_allocations where id = %s",
            (standby.preview_runtime_allocation_id,),
        )
        row = await cur.fetchone()
    assert row is not None and row["standby_draft_id"] is None and row["ready"]
    assert not await session_needs_spare(database, interview_session_id=session_id)


async def test_a_closed_sessions_spare_is_torn_down_while_the_case_goes_on(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case, session_id = await _case(database, auth_users["owner_a"], "spare-session-closed")
    spare = await _spare(database, case, session_id)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.interview_sessions set state = 'closed' where id = %s", (session_id,)
        )
        await cur.execute("select utrace.scan_preview_teardowns() as queued")
        await cur.execute(
            "select input from public.jobs where job_type = 'preview.tear_down_allocation' "
            "and input->>'preview_runtime_allocation_id' = %s",
            (str(spare.preview_runtime_allocation_id),),
        )
        jobs = await cur.fetchall()
    assert [job["input"]["reason"] for job in jobs] == ["session_ended"]
