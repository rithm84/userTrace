"""Preview runtimes that can serve no session any more get torn down.

That is every runtime of a case that has ended, whichever path ended it, every
unclaimed warm runtime of a template a newer pin has retired, and every unclaimed
warm runtime too close to its sandbox's execution limit to serve a session.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from utrace_contracts.domain.lifecycle import InterviewSessionState, ProductSignalState

from app.db.pool import Database
from app.ids import uuid7
from app.preview.jobs import TEAR_DOWN_ALLOCATION
from app.preview.pool import mint_secret
from app.preview.recipe import TemplateRecipe
from tests.factories import CaseFixture, create_case, create_interview_session
from tests.helpers import count_rows
from tests.interview.fixtures import create_preview_allocation
from tests.preview.conftest import install_template
from tests.supabase_auth import SignedInUser


async def _case_with_bound_preview(
    database: Database, owner: SignedInUser, *, name: str, state: ProductSignalState
) -> CaseFixture:
    case = await create_case(
        database,
        name=name,
        state=state,
        authorizing_auth_user_id=owner.auth_user_id,
        participant_auth_user_id=None,
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.PREPARING, conversational_ready=False
    )
    await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="bound"
    )
    return case


async def _scan(database: Database) -> int:
    async with database.connection() as conn:
        row = await (
            await conn.execute("select utrace.scan_preview_teardowns() as queued")
        ).fetchone()
    assert row is not None
    return int(row["queued"])


async def test_an_ended_case_queues_one_teardown_per_live_runtime(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    ended = await _case_with_bound_preview(
        database, auth_users["owner_a"], name="teardown-ended", state=ProductSignalState.CANCELED
    )
    ongoing = await _case_with_bound_preview(
        database,
        auth_users["owner_a"],
        name="teardown-ongoing",
        state=ProductSignalState.INTERVIEWING,
    )

    assert await _scan(database) == 1
    # A teardown already in flight is not queued again.
    assert await _scan(database) == 0
    for case, expected in ((ended, 1), (ongoing, 0)):
        queued = await count_rows(
            database,
            table="jobs",
            where="job_type = %s and client_id = %s",
            params=[TEAR_DOWN_ALLOCATION, case.client_id],
        )
        assert queued == expected


async def _warm_runtime(
    database: Database, *, template_id: UUID, runtime_expires_at: datetime
) -> UUID:
    allocation_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.preview_runtime_allocations (
                id, client_id, allocation_kind, state, seed_manifest_digest,
                runtime_ref, preview_runtime_template_id, pool_role,
                pinned_configuration_release_id, image_digest, recipe_digest,
                fixture_clock, authenticated_identity, sdk_versions, application_port,
                handoff_secret, route_context_signing_secret, runtime_expires_at,
                fencing_token
            )
            select %s, templates.client_id, 'base', 'ready',
                   templates.seed_manifest_digest, %s, templates.id, 'warm_pool',
                   templates.pinned_configuration_release_id, templates.image_digest,
                   templates.recipe_digest, templates.fixture_clock,
                   templates.authenticated_identity, templates.sdk_versions,
                   templates.application_port, %s, %s, %s, utrace.next_fencing_token()
            from public.preview_runtime_templates as templates
            where templates.id = %s
            """,
            (
                allocation_id,
                f"test-warm-{allocation_id}",
                mint_secret(),
                mint_secret(),
                runtime_expires_at,
                template_id,
            ),
        )
    return allocation_id


async def test_a_retired_templates_unclaimed_warm_runtime_is_torn_down(
    database: Database,
    auth_users: dict[str, SignedInUser],
    recipe: TemplateRecipe,
    template_snapshot_id: str,
    template_image: str,
) -> None:
    case = await create_case(
        database,
        name="teardown-retired-template",
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    template = await install_template(
        database,
        client_id=case.client_id,
        configuration_release_id=case.configuration_release_id,
        recipe=recipe,
        template_snapshot_id=template_snapshot_id,
        template_image=template_image,
        warm_pool_target=1,
    )
    warm = await _warm_runtime(
        database,
        template_id=template.id,
        runtime_expires_at=datetime.now(tz=UTC) + timedelta(hours=2),
    )
    # A warm runtime of the current template can still serve the next claim.
    assert await _scan(database) == 0
    # Pinning a newer template retires this one in the same way.
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.preview_runtime_templates set retired_at = now() where id = %s",
            (template.id,),
        )

    assert await _scan(database) == 1
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select input from public.jobs where job_type = %s and client_id = %s",
            (TEAR_DOWN_ALLOCATION, case.client_id),
        )
        queued = await cur.fetchall()
    assert [row["input"] for row in queued] == [
        {"preview_runtime_allocation_id": str(warm), "reason": "template_retired"}
    ]


async def test_a_warm_runtime_near_its_execution_limit_is_replaced(
    database: Database,
    auth_users: dict[str, SignedInUser],
    recipe: TemplateRecipe,
    template_snapshot_id: str,
    template_image: str,
) -> None:
    case = await create_case(
        database,
        name="teardown-expiring-warm",
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    template = await install_template(
        database,
        client_id=case.client_id,
        configuration_release_id=case.configuration_release_id,
        recipe=recipe,
        template_snapshot_id=template_snapshot_id,
        template_image=template_image,
        warm_pool_target=1,
    )
    expiring = await _warm_runtime(
        database,
        template_id=template.id,
        runtime_expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
    )

    assert await _scan(database) == 1
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select input from public.jobs where job_type = %s and client_id = %s",
            (TEAR_DOWN_ALLOCATION, case.client_id),
        )
        queued = await cur.fetchall()
        await cur.execute(
            "select shortfall from utrace.preview_warm_pool_shortfall() "
            "where preview_runtime_template_id = %s",
            (template.id,),
        )
        shortfall = await cur.fetchone()
    assert [row["input"] for row in queued] == [
        {"preview_runtime_allocation_id": str(expiring), "reason": "warm_runtime_expiring"}
    ]
    # It no longer counts toward the target, so the pool provisions its replacement.
    assert shortfall is not None and shortfall["shortfall"] == 1
