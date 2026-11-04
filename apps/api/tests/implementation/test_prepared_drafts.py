"""A proposed change is drafted before the participant agrees, and only its agreement adopts it.

These tests drive the voice agent's tools and the draft records against the local
database; the draft Job's Codex run and the spare runtime are covered by the live
tests.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    InterviewPhase,
    ProductSignalState,
)

from app.clients.shopalytics_release import SHOPALYTICS_BUILD_RECIPE, SHOPALYTICS_REQUIRED_CHECKS
from app.context.bundles import AssembledBundle, assemble_bundle
from app.context.purposes import BundlePurpose
from app.db.pool import Database
from app.db.rows import as_uuid
from app.ids import uuid7
from app.implementation import prepared as prepared_module
from app.implementation.draft_records import ProposedChange, ProposedCriterion
from app.implementation.drafts import (
    PREPARE_DRAFT,
    _already_agreed,
    _earlier_patch,
    _take_workspace,
    adoptable_draft,
    files_digest,
    record_intake_draft,
)
from app.implementation.prepared import (
    _agreed_draft_waiting,
    claim_prepared_workspace,
    hand_over_workspace,
    pins_for_attempt,
    release_claimed_workspace,
)
from app.implementation.specification import AttemptSpecification
from app.interview.voice.tools import ToolContext, answer_follows, execute_tool
from tests.factories import CaseFixture
from tests.gate.factories import SNAPSHOT, create_gated_case, gate_policy
from tests.implementation.factories import authorize_attempt, saved_views_scope
from tests.interview.fixtures import create_live_session, interviewing_case
from tests.supabase_auth import SignedInUser

OUTCOME = "save a conversion view and reopen it later"
CRITERIA = [
    {
        "statement": "the filter stack saves under a name I choose",
        "observability": CriterionObservability.OBSERVED.value,
    },
    {
        "statement": "reopening restores every saved filter",
        "observability": CriterionObservability.OBSERVED.value,
    },
]


async def _context(database: Database, *, case: CaseFixture, session_id: UUID) -> ToolContext:
    bundle: AssembledBundle = await assemble_bundle(
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
    return ToolContext(
        database=database,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        bundle=bundle,
        observation_window_seconds=300,
    )


async def _propose(context: ToolContext, *, outcome: str = OUTCOME) -> dict[str, object]:
    result: dict[str, object] = json.loads(
        await execute_tool(
            context,
            name="propose_change",
            arguments={
                "proposed_outcome": outcome,
                "workflow": "saved_conversion_views",
                "criteria": CRITERIA,
            },
        )
    )
    return result


async def _drafts(database: Database, case: CaseFixture) -> list[dict[str, object]]:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select id, state, settled_at is not null as settled from public.prepared_drafts "
            "where product_signal_id = %s order by proposed_at",
            (case.product_signal_id,),
        )
        return [dict(row) for row in await cur.fetchall()]


async def test_a_proposal_queues_its_draft_and_a_newer_one_replaces_it(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await interviewing_case(database, auth_users, "drafts-proposal")
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    context = await _context(database, case=case, session_id=session_id)

    assert await _propose(context) == {"noted": True}
    assert await _propose(context, outcome=OUTCOME + ", and rename it") == {"noted": True}

    drafts = await _drafts(database, case)
    # The first draft's Job never touched a workspace, so it settles as it is discarded.
    assert [(row["state"], row["settled"]) for row in drafts] == [
        ("discarded", True),
        ("proposed", False),
    ]
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select input->>'prepared_draft_id' as draft from public.jobs where job_type = %s "
            "order by created_at",
            (PREPARE_DRAFT,),
        )
        queued = [row["draft"] for row in await cur.fetchall()]
        await cur.execute(
            "select count(*) as spares from public.jobs where job_type = 'preview.prepare_spare_candidate' "
            "and input->>'interview_session_id' = %s",
            (str(session_id),),
        )
        spares = await cur.fetchone()
    assert queued == [str(row["id"]) for row in drafts]
    # A proposal makes sure the session's spare runtime is warming, once.
    assert spares is not None and spares["spares"] == 1
    # The proposal is not an agreement: the case is still being interviewed.
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state::text as state, acceptance_criteria_revision from public.product_signals where id = %s",
            (case.product_signal_id,),
        )
        signal = await cur.fetchone()
    assert signal is not None
    assert (signal["state"], signal["acceptance_criteria_revision"]) == ("interviewing", 0)


async def test_an_agreement_as_proposed_records_the_draft_and_a_different_one_drafts_anew(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await interviewing_case(database, auth_users, "drafts-agreed")
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    await _propose(await _context(database, case=case, session_id=session_id))
    # The agreement restates the change in its own words, which still records the
    # draft because the agent says it agreed exactly as proposed.
    recorded = json.loads(
        await execute_tool(
            await _context(database, case=case, session_id=session_id),
            name="record_acceptance_criteria",
            arguments={
                "agreed_outcome": "Save conversion views to reopen later.",
                "workflow": "saved_conversion_views",
                "criteria": CRITERIA,
                "as_proposed": True,
            },
        )
    )
    assert recorded["acceptance_criteria_revision"] == 1
    drafts = await _drafts(database, case)
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select prepared_draft_id from public.acceptance_criteria_agreements where product_signal_id = %s",
            (case.product_signal_id,),
        )
        agreement = await cur.fetchone()
    assert agreement is not None and agreement["prepared_draft_id"] == drafts[0]["id"]
    assert drafts[0]["state"] == "proposed"

    other = await interviewing_case(database, auth_users, "drafts-different")
    other_session = await create_live_session(database, case=other, phase=InterviewPhase.DISCOVERY)
    await _propose(await _context(database, case=other, session_id=other_session))
    await execute_tool(
        await _context(database, case=other, session_id=other_session),
        name="record_acceptance_criteria",
        arguments={
            "agreed_outcome": "export the conversion view as a spreadsheet",
            "workflow": "conversion_export",
            "criteria": [CRITERIA[0]],
        },
    )
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select prepared_draft_id from public.acceptance_criteria_agreements where product_signal_id = %s",
            (other.product_signal_id,),
        )
        linked = await cur.fetchone()
    # The proposal it differs from is discarded, and the agreed change is drafted
    # from the agreement, so the attempt still adopts a draft.
    drafts = await _drafts(database, other)
    assert [row["state"] for row in drafts] == ["discarded", "proposed"]
    assert linked is not None and linked["prepared_draft_id"] == drafts[1]["id"]


async def test_an_agreement_that_repeats_the_proposal_records_its_draft_without_the_flag(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await interviewing_case(database, auth_users, "drafts-repeat")
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    await _propose(await _context(database, case=case, session_id=session_id))
    await execute_tool(
        await _context(database, case=case, session_id=session_id),
        name="record_acceptance_criteria",
        arguments={
            # Case and spacing differ; the change does not.
            "agreed_outcome": "Save a conversion view and  reopen it later",
            "workflow": "saved_conversion_views",
            "criteria": CRITERIA,
        },
    )
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select prepared_draft_id is not null as linked from public.acceptance_criteria_agreements "
            "where product_signal_id = %s",
            (case.product_signal_id,),
        )
        row = await cur.fetchone()
    assert row is not None and row["linked"] is True


async def test_a_proposal_that_cannot_be_noted_asks_nothing_of_the_agent(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await interviewing_case(database, auth_users, "drafts-refused")
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.product_signals set state = 'gate_pending' where id = %s",
            (case.product_signal_id,),
        )
    result = await _propose(await _context(database, case=case, session_id=session_id))

    assert result == {"noted": False}
    # Neither a noted nor an unnoted proposal makes the agent speak twice.
    assert answer_follows("propose_change", refused=False, answer_spoke=True) is False
    assert await _drafts(database, case) == []


def test_a_change_is_compared_ignoring_case_and_spacing_only() -> None:
    change = ProposedChange(
        intended_outcome="Save a view",
        workflow_ref="saved_conversion_views",
        criteria=(ProposedCriterion(statement="It saves", observability=CriterionObservability.OBSERVED),),
    )
    same = change.model_copy(update={"intended_outcome": "  save  a VIEW "})
    other = change.model_copy(update={"workflow_ref": "conversion_export"})
    assert same.digest() == change.digest()
    assert other.digest() != change.digest()


async def _drafted_in_workspace(database: Database, case: CaseFixture, draft_id: UUID) -> UUID:
    """Record the draft as written in a prepared workspace, as its Job would."""
    workspace_id = uuid7()
    files = {"src/components/dashboard/SavedViews.tsx": "export const SavedViews = 1;\n"}
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.prepared_workspaces (
                id, client_id, product_signal_id, state, sandbox_name, pins_digest,
                baseline_checks, prepared_draft_id, expires_at
            )
            values (%s, %s, %s, 'drafting', %s, 'sha256:pins', '[]', %s, now() + interval '1 hour')
            """,
            (workspace_id, case.client_id, case.product_signal_id, f"ws-{workspace_id.hex}", draft_id),
        )
        await cur.execute(
            """
            update public.prepared_drafts
            set state = 'drafted', prepared_workspace_id = %s, files = %s, files_digest = %s,
                patch = 'diff', consumed_budgets = %s, fast_checks = %s, settled_at = now()
            where id = %s
            """,
            (
                workspace_id,
                Jsonb(files),
                files_digest(files),
                Jsonb(
                    {
                        "elapsed_seconds": 14,
                        "agent_turns": 6,
                        "agent_cost_cents": 9,
                        "process_executions": 4,
                        "artifact_bytes": 120,
                        "correction_cycles": 0,
                    }
                ),
                Jsonb({"runs": [], "journal": [{"tool": "run_process", "subject": "rg saved"}]}),
                draft_id,
            ),
        )
    return workspace_id


def _approved_for(case: CaseFixture, revision: int) -> AttemptSpecification:
    """The two facts adoption reads from an attempt's specification."""
    return cast(
        AttemptSpecification,
        SimpleNamespace(
            product_signal_id=case.product_signal_id,
            approved_scope=SimpleNamespace(acceptance_criteria_revision=revision),
        ),
    )


async def test_an_attempt_adopts_only_the_draft_its_approved_agreement_recorded(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await interviewing_case(database, auth_users, "drafts-adopt")
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    await _propose(await _context(database, case=case, session_id=session_id))
    draft_id = (await _drafts(database, case))[0]["id"]
    assert isinstance(draft_id, UUID)
    workspace_id = await _drafted_in_workspace(database, case, draft_id)

    # Before any agreement, nothing approved names the draft.
    assert await adoptable_draft(
        database, specification=_approved_for(case, 1), prepared_workspace_id=workspace_id
    ) is None
    await execute_tool(
        await _context(database, case=case, session_id=session_id),
        name="record_acceptance_criteria",
        arguments={
            "agreed_outcome": OUTCOME,
            "workflow": "saved_conversion_views",
            "criteria": CRITERIA,
            "as_proposed": True,
        },
    )
    draft = await adoptable_draft(
        database, specification=_approved_for(case, 1), prepared_workspace_id=workspace_id
    )
    assert draft is not None and draft.prepared_draft_id == draft_id
    assert draft.consumed.agent_turns == 6
    assert [subject for _, subject in draft.journal] == ["rg saved"]
    assert draft.files_digest == files_digest(draft.files)


REQUEST = "I keep rebuilding the same conversion filters every Monday. Can I save a view?"


async def _record_concern(database: Database, case: CaseFixture) -> None:
    """The concern intake records from the request, with its exact wording."""
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
                "rebuilding the same conversion filters every week",
                "a saved conversion view reopens with every filter",
                Jsonb({"messages": [REQUEST]}),
            ),
        )


async def test_the_original_request_is_drafted_once_and_a_proposal_replaces_it(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await interviewing_case(database, auth_users, "drafts-intake")
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    await _record_concern(database, case)

    intake_id = await record_intake_draft(
        database, client_id=case.client_id, product_signal_id=case.product_signal_id
    )
    assert intake_id is not None
    assert await record_intake_draft(
        database, client_id=case.client_id, product_signal_id=case.product_signal_id
    ) is None
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select kind, state, proposed_scope from public.prepared_drafts where id = %s",
            (intake_id,),
        )
        intake = await cur.fetchone()
        await cur.execute(
            "select count(*) as queued from public.jobs "
            "where job_type = %s and input->>'prepared_draft_id' = %s",
            (PREPARE_DRAFT, str(intake_id)),
        )
        job = await cur.fetchone()
    assert intake is not None and intake["kind"] == "intake" and intake["state"] == "proposed"
    assert intake["proposed_scope"]["request"]["messages"] == [REQUEST]
    assert job is not None and job["queued"] == 1

    # The proposal the agent makes takes over the case's draft; it is the only kind an
    # agreement records.
    await _propose(await _context(database, case=case, session_id=session_id))
    drafts = await _drafts(database, case)
    assert [draft["state"] for draft in drafts] == ["discarded", "proposed"]


async def test_a_proposal_revises_an_intake_draft_still_being_written(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await interviewing_case(database, auth_users, "drafts-running-intake")
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    await _record_concern(database, case)
    intake_id = await record_intake_draft(
        database, client_id=case.client_id, product_signal_id=case.product_signal_id
    )
    assert intake_id is not None
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.prepared_drafts set state = 'drafting' where id = %s", (intake_id,)
        )

    # Proposals and the agreement leave the running intake draft to finish; only the
    # older proposal goes.
    await _propose(await _context(database, case=case, session_id=session_id))
    await _propose(
        await _context(database, case=case, session_id=session_id),
        outcome=OUTCOME + ", and rename it",
    )
    await execute_tool(
        await _context(database, case=case, session_id=session_id),
        name="record_acceptance_criteria",
        arguments={
            "agreed_outcome": OUTCOME + ", and rename it",
            "workflow": "saved_conversion_views",
            "criteria": CRITERIA,
            "as_proposed": True,
        },
    )
    drafts = await _drafts(database, case)
    assert [draft["state"] for draft in drafts] == ["drafting", "discarded", "proposed"]
    agreed = drafts[2]["id"]
    assert isinstance(agreed, UUID)
    # The attempt's claim waits for the agreed draft while it has yet to take the
    # workspace.
    assert await _agreed_draft_waiting(database, specification=_approved_for(case, 1)) == agreed

    # The intake draft finishes; the agreed proposal takes the workspace and retires
    # it, keeping its patch to revise.
    workspace_id = await _drafted_in_workspace(database, case, intake_id)
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute("select * from public.prepared_drafts where id = %s", (agreed,))
        row = await cur.fetchone()
    assert row is not None
    taken = await _take_workspace(database, draft_id=agreed, draft=row)
    assert taken is not None and taken.prepared_workspace_id == workspace_id
    assert taken.earlier_draft_id == intake_id
    assert await _earlier_patch(database, earlier=intake_id) == "diff"
    assert [draft["state"] for draft in await _drafts(database, case)] == [
        "discarded",
        "discarded",
        "drafting",
    ]
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select prepared_draft_id from public.prepared_workspaces where id = %s",
            (workspace_id,),
        )
        bound = await cur.fetchone()
    assert bound is not None and bound["prepared_draft_id"] == agreed
    assert await _agreed_draft_waiting(database, specification=_approved_for(case, 1)) is None


async def test_an_agreement_no_proposal_drafted_drafts_the_agreed_change_on_the_intake(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The agent proposed out loud but never called propose_change before the yes."""
    case = await interviewing_case(database, auth_users, "drafts-unproposed-agreement")
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    await _record_concern(database, case)
    intake_id = await record_intake_draft(
        database, client_id=case.client_id, product_signal_id=case.product_signal_id
    )
    assert intake_id is not None
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.prepared_drafts set state = 'drafting' where id = %s", (intake_id,)
        )

    recorded = json.loads(
        await execute_tool(
            await _context(database, case=case, session_id=session_id),
            name="record_acceptance_criteria",
            arguments={
                "agreed_outcome": OUTCOME,
                "workflow": "saved_conversion_views",
                "criteria": CRITERIA,
                "as_proposed": True,
            },
        )
    )
    assert recorded["acceptance_criteria_revision"] == 1

    # The running intake draft is left to finish, and the agreed change is drafted
    # as a proposal that revises it; the agreement records that draft.
    drafts = await _drafts(database, case)
    assert [row["state"] for row in drafts] == ["drafting", "proposed"]
    agreed = drafts[1]["id"]
    assert isinstance(agreed, UUID)
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select kind, criteria from public.prepared_drafts where id = %s", (agreed,)
        )
        draft = await cur.fetchone()
        await cur.execute(
            "select prepared_draft_id from public.acceptance_criteria_agreements "
            "where product_signal_id = %s",
            (case.product_signal_id,),
        )
        agreement = await cur.fetchone()
        await cur.execute(
            "select count(*) as queued from public.jobs "
            "where job_type = %s and input->>'prepared_draft_id' = %s",
            (PREPARE_DRAFT, str(agreed)),
        )
        job = await cur.fetchone()
    assert draft is not None and draft["kind"] == "proposal"
    assert [criterion["statement"] for criterion in draft["criteria"]] == [
        criterion["statement"] for criterion in CRITERIA
    ]
    assert agreement is not None and agreement["prepared_draft_id"] == agreed
    assert job is not None and job["queued"] == 1
    # The gate is already deciding the agreement, so the draft prepares no
    # evaluation of its own, and the attempt's claim waits for it.
    assert await _already_agreed(database, draft_id=agreed)
    assert await _agreed_draft_waiting(database, specification=_approved_for(case, 1)) == agreed


async def test_a_draft_revises_an_earlier_proposal_the_workspace_holds(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await interviewing_case(database, auth_users, "drafts-earlier-proposal")
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    await _propose(await _context(database, case=case, session_id=session_id))
    earlier = (await _drafts(database, case))[0]["id"]
    assert isinstance(earlier, UUID)
    await _drafted_in_workspace(database, case, earlier)
    await _propose(
        await _context(database, case=case, session_id=session_id),
        outcome=OUTCOME + ", and rename it",
    )

    # The newer proposal discarded the earlier one, whose finished files it revises.
    assert [row["state"] for row in await _drafts(database, case)] == ["discarded", "proposed"]
    assert await _earlier_patch(database, earlier=earlier) == "diff"
    assert not await _already_agreed(database, draft_id=earlier)


async def test_a_proposal_discarded_while_it_waits_leaves_the_workspace_as_it_was(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await interviewing_case(database, auth_users, "drafts-discarded-waiting")
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    await _record_concern(database, case)
    intake_id = await record_intake_draft(
        database, client_id=case.client_id, product_signal_id=case.product_signal_id
    )
    assert intake_id is not None
    workspace_id = await _drafted_in_workspace(database, case, intake_id)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.prepared_drafts set state = 'drafting' where id = %s", (intake_id,)
        )
    await _propose(await _context(database, case=case, session_id=session_id))
    first = (await _drafts(database, case))[1]
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute("select * from public.prepared_drafts where id = %s", (first["id"],))
        row = await cur.fetchone()
    assert row is not None
    await _propose(
        await _context(database, case=case, session_id=session_id),
        outcome=OUTCOME + ", and rename it",
    )

    assert await _take_workspace(database, draft_id=as_uuid(row, "id"), draft=row) is None
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select prepared_draft_id from public.prepared_workspaces where id = %s",
            (workspace_id,),
        )
        bound = await cur.fetchone()
    # The workspace still holds the intake draft, for the newer proposal to revise.
    assert bound is not None and bound["prepared_draft_id"] == intake_id


async def test_a_claim_waits_for_the_agreed_draft_to_take_the_workspace(
    database: Database, auth_users: dict[str, SignedInUser], monkeypatch: pytest.MonkeyPatch
) -> None:
    gated = await create_gated_case(
        database,
        name="claim-waits-for-draft",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        policy=gate_policy(),
        state=ProductSignalState.IMPLEMENTING,
    )
    attempt = await authorize_attempt(
        database,
        case=gated,
        scope=saved_views_scope(checks=SHOPALYTICS_REQUIRED_CHECKS),
        workspace_snapshot_id=SNAPSHOT,
    )
    pins = pins_for_attempt(attempt.specification, build_recipe=SHOPALYTICS_BUILD_RECIPE)
    intake_id, agreed_id, workspace_id = uuid7(), uuid7(), uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.prepared_workspaces (
                id, client_id, product_signal_id, state, sandbox_name, pins_digest,
                baseline_checks, prepared_draft_id, expires_at
            )
            values (%s, %s, %s, 'drafting', 'ws-claim-waits', %s, '[]', %s,
                    now() + interval '1 hour')
            """,
            (workspace_id, gated.client_id, gated.product_signal_id, pins.digest(), intake_id),
        )
    waiting: list[UUID | None] = [agreed_id]

    async def agreed_waiting(_database: Database, *, specification: object) -> UUID | None:
        return waiting[0]

    monkeypatch.setattr(prepared_module, "_agreed_draft_waiting", agreed_waiting)
    monkeypatch.setattr(prepared_module, "_CLAIM_POLL_SECONDS", 0.05)

    async def take_after_a_moment() -> None:
        await asyncio.sleep(0.3)
        async with database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                "update public.prepared_workspaces set prepared_draft_id = %s where id = %s",
                (agreed_id, workspace_id),
            )
        waiting[0] = None

    taking = asyncio.create_task(take_after_a_moment())
    claimed = await claim_prepared_workspace(
        database, specification=attempt.specification, pins=pins
    )
    await taking
    # The attempt claims the workspace once the agreed draft is in it, so it adopts it.
    assert claimed is not None and claimed.prepared_draft_id == agreed_id


async def test_a_version_that_passed_its_full_checks_hands_its_workspace_to_the_next_change(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    gated = await create_gated_case(
        database,
        name="workspace-hand-over",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        policy=gate_policy(),
        state=ProductSignalState.AWAITING_VALIDATION,
    )
    attempt = await authorize_attempt(
        database,
        case=gated,
        scope=saved_views_scope(checks=SHOPALYTICS_REQUIRED_CHECKS),
        workspace_snapshot_id=SNAPSHOT,
    )
    pins = pins_for_attempt(attempt.specification, build_recipe=SHOPALYTICS_BUILD_RECIPE)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.prepared_workspaces (
                id, client_id, product_signal_id, state, sandbox_name, pins_digest,
                baseline_checks, implementation_attempt_id, lease_fencing_token, expires_at
            )
            values (%s, %s, %s, 'claimed', 'ws-hand-over', %s, '[]', %s, %s,
                    now() + interval '1 hour')
            """,
            (
                uuid7(),
                gated.client_id,
                gated.product_signal_id,
                pins.digest(),
                attempt.implementation_attempt_id,
                attempt.fencing_token,
            ),
        )
    version_id = uuid7()

    async def hand_over(limit: int) -> UUID | None:
        return await hand_over_workspace(
            database,
            client_id=gated.client_id,
            product_signal_id=gated.product_signal_id,
            sandbox_name="ws-hand-over",
            pins=pins,
            baseline=(),
            starting_tree="4b825dc642cb6eb9a060e54bf8d69288fbee4904",
            preview_version_id=version_id,
            implementation_limit=limit,
        )

    # A case that used its last attempt keeps no workspace for another change.
    assert await hand_over(1) is None
    handed = await hand_over(3)
    assert handed is not None
    # The case holds one open workspace at a time.
    assert await hand_over(3) is None

    claimed = await claim_prepared_workspace(
        database, specification=attempt.specification, pins=pins
    )
    assert claimed is not None and claimed.prepared_workspace_id == handed
    assert claimed.sandbox_name == "ws-hand-over"
    assert claimed.starting_tree == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    assert claimed.preview_version_id == version_id

    await release_claimed_workspace(database, prepared_workspace_id=handed)
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select generation, state from public.prepared_workspaces "
            "where product_signal_id = %s order by generation",
            (gated.product_signal_id,),
        )
        rows = [(row["generation"], row["state"]) for row in await cur.fetchall()]
    assert rows == [(1, "claimed"), (2, "released")]
