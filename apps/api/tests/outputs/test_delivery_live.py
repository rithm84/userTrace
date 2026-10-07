"""The GitHub delivery protocol, against a real throwaway private repository.

Nothing is mocked: the connector talks to github.com, opens one real draft pull
request, and the assertions read what GitHub actually stored. The repository is
created at the start of the module and deleted at the end.
"""

from __future__ import annotations

import warnings
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from utrace_contracts.domain.lifecycle import (
    CaseFileStage,
    CaseFileVariant,
    CriterionObservability,
    DeliveryState,
    EvidenceClass,
    ProductSignalState,
    ValidationOutcome,
)

from app.connectors.github_delivery import FilePatch, idempotency_marker
from app.db.pool import Database
from app.ids import uuid7
from app.outputs.bundle import build_bundle
from app.outputs.delivery import (
    delivery_branch,
    load_delivery,
    pull_request_body,
    reserve_delivery,
)
from app.outputs.delivery_runner import DeliveryRequest, run_delivery
from app.outputs.handlers import assemble, delivery_patches
from app.validation.completion import CriterionConfirmation, complete_validation
from app.validation.controller import begin_validation, open_validation_attempt
from app.validation.evidence import DurableEvidenceQuery, EvidenceStance
from tests.helpers import read_state
from tests.outputs.github_repository import (
    SmokeRepository,
    TokenAuthorizedDelivery,
    delete_repository,
    ensure_repository,
    gh_token,
)
from tests.supabase_auth import SignedInUser
from tests.validation.scenario import (
    ValidationScenario,
    add_check_result,
    add_criterion_observation,
    add_implementation_artifact,
    add_intake_source,
    add_transcript_fragment,
    add_workflow_observation,
    binding_of,
    build_scenario,
)

ASSEMBLY_DEADLINE_SECONDS = 7 * 24 * 60 * 60
REQUEST_TIMEOUT_SECONDS = 30.0

ONE_CRITERION: tuple[tuple[str, CriterionObservability], ...] = (
    ("the saved view reopens with every filter", CriterionObservability.OBSERVED),
)


@pytest.fixture(scope="module")
def smoke_repository() -> Iterator[SmokeRepository]:
    """Create the throwaway private repository, then delete it."""
    token = gh_token()
    if token is None:
        pytest.skip("the gh CLI holds no token; the delivery protocol proof needs one")
    repository = ensure_repository(token=token)
    try:
        yield repository
    finally:
        refusal = delete_repository()
        if refusal:
            warnings.warn(
                f"the throwaway repository {repository.full_name} was not deleted: "
                f"{refusal}",
                stacklevel=1,
            )


@pytest_asyncio.fixture
async def delivery_connector(
    smoke_repository: SmokeRepository,
) -> AsyncIterator[TokenAuthorizedDelivery]:
    """One connector bound to the throwaway repository."""
    connector = TokenAuthorizedDelivery(
        repository=smoke_repository, request_timeout_seconds=REQUEST_TIMEOUT_SECONDS
    )
    try:
        yield connector
    finally:
        await connector.__aexit__(None, None, None)


async def _validated_case(
    database: Database,
    auth_users: dict[str, SignedInUser],
    *,
    name: str,
    repository: SmokeRepository,
) -> tuple[ValidationScenario, UUID]:
    """Build a case, validate it, and leave a frozen validated result behind."""
    scenario = await build_scenario(
        database,
        name=name,
        contact_budget=2,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=auth_users["participant"].auth_user_id,
        criteria=ONE_CRITERION,
        signal_state=ProductSignalState.AWAITING_VALIDATION,
        candidate_ready=True,
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.implementation_attempts set repository_ref = %s where id = %s",
            (repository.full_name, scenario.implementation_attempt_id),
        )
    await add_intake_source(
        database,
        scenario,
        summary="A growth lead asks to save a conversion filter stack and reopen it.",
    )
    await add_transcript_fragment(
        database,
        scenario,
        field="expected_behavior",
        summary="the filter stack should survive a refresh",
    )
    await add_transcript_fragment(
        database,
        scenario,
        field="actual_behavior",
        summary="leaving the dashboard clears the applied filters",
    )
    await add_transcript_fragment(
        database, scenario, field="impact", summary="twenty minutes rebuilt weekly"
    )
    criterion = scenario.criteria[0]
    await add_criterion_observation(
        database,
        scenario,
        criterion=criterion,
        stance=EvidenceStance.SUPPORTS,
        evidence_class=EvidenceClass.VALIDATION_OBSERVATION,
        summary="the reopened view restored every saved filter value",
        supersedes_id=None,
    )
    await add_workflow_observation(
        database,
        scenario,
        milestone="reopen-saved-view",
        summary="the chart re-rendered with the saved filter values",
        preview_version_id=scenario.preview_version_id,
        coverage_limit=None,
    )
    await add_check_result(database, scenario, name="pnpm test", outcome="passed")
    await add_implementation_artifact(
        database,
        scenario,
        files=[
            (
                f"utrace/{scenario.product_signal_id.hex[:8]}/saved-views.md",
                b"# Saved conversion views\n\nPersist the named filter stack.\n",
            )
        ],
    )
    attempt_id = await open_validation_attempt(database, binding=binding_of(scenario))
    await begin_validation(
        database, attempt_id=attempt_id, originating_user_id=scenario.client_user_id
    )
    window = await DurableEvidenceQuery(database).freeze_window(
        client_id=scenario.client_id,
        product_signal_id=scenario.product_signal_id,
        interview_session_id=scenario.interview_session_id,
        purpose="validation",
        starts_at=datetime.now(tz=UTC) - timedelta(hours=2),
        ends_at=datetime.now(tz=UTC) + timedelta(minutes=5),
    )
    completion = await complete_validation(
        database,
        DurableEvidenceQuery(database),
        attempt_id=attempt_id,
        evidence_window_id=window.evidence_window_id,
        confirmations=[
            CriterionConfirmation(
                criterion_id=criterion.id,
                user_confirmed=True,
                user_rejected=False,
                confirmation_evidence_record_id=None,
                workflow_observed=True,
            )
        ],
        assembly_deadline_seconds=ASSEMBLY_DEADLINE_SECONDS,
    )
    assert completion.outcome is ValidationOutcome.VALIDATED
    return scenario, attempt_id


async def test_validated_work_delivers_one_exact_draft_pull_request(
    database: Database,
    auth_users: dict[str, SignedInUser],
    smoke_repository: SmokeRepository,
    delivery_connector: TokenAuthorizedDelivery,
) -> None:
    """One draft PR, the deterministic branch, and the exact verified head."""
    scenario, attempt_id = await _validated_case(
        database, auth_users, name="delivery", repository=smoke_repository
    )
    result = await assemble(
        database,
        DurableEvidenceQuery(database),
        product_signal_id=scenario.product_signal_id,
        variant=CaseFileVariant.VALIDATED_IMPLEMENTATION,
        validation_attempt_id=attempt_id,
        blocker_reason=None,
        closure_trigger="validated",
        closure_reason="validated",
        narrative_source="evidence_derived",
        drafter=None,
        repository=delivery_connector,
    )
    assert result.stage is CaseFileStage.PUBLISHED
    assert result.variant is CaseFileVariant.VALIDATED_IMPLEMENTATION
    assert result.delivery_id is not None

    async with database.connection() as conn:
        delivery = await load_delivery(conn, delivery_id=result.delivery_id)
    assert delivery.state is DeliveryState.DELIVERED
    assert delivery.branch_ref == delivery_branch(
        product_signal_id=scenario.product_signal_id
    )
    assert delivery.verified_head_sha is not None
    assert delivery.pull_request_number is not None

    adopted = await delivery_connector.find_existing_delivery(
        marker=delivery.idempotency_marker
    )
    assert adopted is not None
    assert adopted.draft is True
    assert adopted.number == delivery.pull_request_number
    assert adopted.head_sha == delivery.verified_head_sha
    assert adopted.base_branch == smoke_repository.default_branch
    assert await delivery_connector.verify_head(
        branch=delivery.branch_ref, expected_head_sha=delivery.verified_head_sha
    )

    state, _ = await read_state(
        database,
        table="product_signals",
        row_id=scenario.product_signal_id,
        column="state",
    )
    assert state == ProductSignalState.CLOSED_VALIDATED.value

    # A replayed delivery adopts its own pull request instead of opening a second.
    async with database.connection() as conn:
        bundle = await build_bundle(
            conn,
            client_id=scenario.client_id,
            product_signal_id=scenario.product_signal_id,
            variant=CaseFileVariant.VALIDATED_IMPLEMENTATION,
            evidence_window_id=(
                await DurableEvidenceQuery(database).freeze_window(
                    client_id=scenario.client_id,
                    product_signal_id=scenario.product_signal_id,
                    interview_session_id=None,
                    purpose="case_file",
                    starts_at=datetime.now(tz=UTC) - timedelta(hours=2),
                    ends_at=datetime.now(tz=UTC) + timedelta(minutes=5),
                )
            ).evidence_window_id,
            validation_attempt_id=attempt_id,
            blocker_reason=None,
        )
    assert bundle.implementation is not None
    replay = await run_delivery(
        database,
        delivery_connector,
        request=DeliveryRequest(
            delivery_id=delivery.id,
            title="Saved conversion views",
            problem_summary="rebuilt weekly",
            implementation_summary="persist the named filter stack",
            risks=("dashboard state only",),
            implementation=bundle.implementation,
            patches=await delivery_patches(
                database, preview_version_id=bundle.implementation.preview_version_id
            ),
        ),
    )
    assert replay.state is DeliveryState.DELIVERED
    assert replay.pull_request_number == delivery.pull_request_number


async def test_an_existing_branch_without_this_marker_records_a_blocker(
    database: Database,
    auth_users: dict[str, SignedInUser],
    smoke_repository: SmokeRepository,
    delivery_connector: TokenAuthorizedDelivery,
) -> None:
    """uTrace never force-pushes over a branch it cannot prove is its own."""
    scenario, attempt_id = await _validated_case(
        database, auth_users, name="blocked-delivery", repository=smoke_repository
    )
    branch = delivery_branch(
        product_signal_id=scenario.product_signal_id
    )
    head = await delivery_connector.resolve_branch_head(
        branch=smoke_repository.default_branch
    )
    assert head is not None
    await delivery_connector.create_branch_commit(
        branch=branch,
        base_sha=head,
        patches=[
            FilePatch(
                path=f"utrace/{scenario.product_signal_id.hex[:8]}/decoy.md",
                content=b"a branch this delivery did not write\n",
                mode="100644",
            )
        ],
        commit_message="a branch this delivery did not write",
    )

    window = await DurableEvidenceQuery(database).freeze_window(
        client_id=scenario.client_id,
        product_signal_id=scenario.product_signal_id,
        interview_session_id=None,
        purpose="case_file",
        starts_at=datetime.now(tz=UTC) - timedelta(hours=2),
        ends_at=datetime.now(tz=UTC) + timedelta(minutes=5),
    )
    async with database.connection() as conn:
        bundle = await build_bundle(
            conn,
            client_id=scenario.client_id,
            product_signal_id=scenario.product_signal_id,
            variant=CaseFileVariant.VALIDATED_IMPLEMENTATION,
            evidence_window_id=window.evidence_window_id,
            validation_attempt_id=attempt_id,
            blocker_reason=None,
        )
    assert bundle.implementation is not None

    case_file_id = uuid7()
    delivery_id = uuid7()
    command_id = uuid7()
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.case_files (
                    id, client_id, product_signal_id, variant, stage, authenticated_url
                )
                values (%s, %s, %s, %s, 'draft', %s)
                """,
                (
                    case_file_id,
                    scenario.client_id,
                    scenario.product_signal_id,
                    CaseFileVariant.VALIDATED_IMPLEMENTATION.value,
                    f"https://app.utrace.dev/case-files/{case_file_id}",
                ),
            )
        await reserve_delivery(
            conn,
            delivery_id=delivery_id,
            client_id=scenario.client_id,
            product_signal_id=scenario.product_signal_id,
            case_file_id=case_file_id,
            validation_attempt_id=attempt_id,
            command_id=command_id,
            repository_ref=smoke_repository.full_name,
            branch_ref=branch,
            base_branch=smoke_repository.default_branch,
            head_commit=scenario.source_commit,
            base_revision=scenario.base_revision,
            idempotency_marker=idempotency_marker(command_id=str(command_id)),
            case_file_url=f"https://app.utrace.dev/case-files/{case_file_id}",
        )

    outcome = await run_delivery(
        database,
        delivery_connector,
        request=DeliveryRequest(
            delivery_id=delivery_id,
            title="Saved conversion views",
            problem_summary="rebuilt weekly",
            implementation_summary="persist the named filter stack",
            risks=(),
            implementation=bundle.implementation,
            patches=await delivery_patches(
                database, preview_version_id=bundle.implementation.preview_version_id
            ),
        ),
    )
    assert outcome.state is DeliveryState.BLOCKED
    assert outcome.blocker_reason is not None
    assert "force-push" in outcome.blocker_reason


def test_a_pull_request_body_carries_no_customer_identity() -> None:
    """The body is sanitized by construction: it is composed from identities."""
    from app.outputs.bundle import ImplementationRecord
    from app.outputs.sections import CheckResult

    implementation = ImplementationRecord(
        implementation_attempt_id=uuid7(),
        repository_ref="ShopalyticsStore/shopalytics",
        base_revision="a" * 40,
        preview_version_id=uuid7(),
        source_commit="b" * 40,
        source_digest="sha256:source",
        build_digest="sha256:build",
        dataset_manifest_digest="sha256:dataset",
        files_touched=("apps/dashboard/saved-views.ts",),
        cleanup_state="workspace destroyed",
        checks=(CheckResult(name="pnpm test", outcome="passed"),),
    )
    body = pull_request_body(
        problem_summary="A recurring conversion filter stack is rebuilt weekly.",
        implementation_summary="Persist and restore the named filter stack.",
        implementation=implementation,
        risks=("dashboard state only",),
        case_file_url="https://app.utrace.dev/case-files/abc",
        delivered_commit="c" * 40,
    )
    assert "Maya" not in body
    assert "@" not in body
    assert "https://app.utrace.dev/case-files/abc" in body
    assert "does not merge its own pull requests" in body
