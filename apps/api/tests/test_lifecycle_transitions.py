"""Every lifecycle transition, committed through the kernel.

The pure machine tests prove the transition tables. These prove that each move
commits against the real schema: the projection advances, the Event is sequenced,
the audit record lands, and the constraints that protect the invariants hold.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from psycopg.errors import RestrictViolation, UniqueViolation
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    AggregateKind,
    CaseFileStage,
    CaseFileVariant,
    CriterionObservability,
    EnvironmentLeaseState,
    ImplementationAttemptState,
    IntakeItemState,
    InterviewSessionState,
    OutreachAttemptState,
    PreviewAllocationKind,
    PreviewRuntimeAllocationState,
    PreviewVersionState,
    ProductSignalState,
    RoutingOutcome,
    ValidationAttemptState,
    ValidationOutcome,
    VoiceConnectionState,
)

from app.db.kernel import ColumnValue, apply_transition
from app.db.pool import Database
from app.errors import GuardRejectedError
from app.ids import uuid7
from app.lifecycle.attempts import grant_environment_lease
from app.lifecycle.case_file import CASE_FILE_MACHINE, CaseFileFacts
from app.lifecycle.implementation import (
    IMPLEMENTATION_ATTEMPT_MACHINE,
    ImplementationAttemptFacts,
)
from app.lifecycle.intake import INTAKE_ITEM_MACHINE, IntakeItemFacts
from app.lifecycle.interview import (
    INTERVIEW_SESSION_MACHINE,
    VOICE_CONNECTION_MACHINE,
    InterviewSessionFacts,
    VoiceConnectionFacts,
)
from app.lifecycle.machine import Machine, plan_transition
from app.lifecycle.outreach import OUTREACH_ATTEMPT_MACHINE, OutreachAttemptFacts
from app.lifecycle.product_signal import PRODUCT_SIGNAL_MACHINE, ProductSignalFacts
from app.lifecycle.validation import VALIDATION_ATTEMPT_MACHINE, ValidationAttemptFacts
from tests.factories import (
    CaseFixture,
    create_case,
    create_gate_decision,
    create_interview_session,
    create_outreach_attempt,
)
from tests.helpers import audit_actions, event_sequences, make_audit, make_event, read_state
from tests.supabase_auth import SignedInUser


async def _commit[FactsT](
    database: Database,
    machine: Machine[FactsT],
    facts: FactsT,
    *,
    client_id: UUID,
    kind: AggregateKind,
    row_id: UUID,
    table: str,
    column: str,
    trigger: str,
    mutation: dict[str, ColumnValue],
) -> str:
    """Commit one transition and return the state the row now holds."""
    current, revision = await read_state(
        database, table=table, row_id=row_id, column=column
    )
    request = plan_transition(
        machine,
        facts,
        trigger=trigger,
        current_state=current,
        client_id=client_id,
        aggregate_kind=kind,
        aggregate_id=row_id,
        expected_revision=revision,
        state_column=column,
        mutation=mutation,
        event=make_event(
            stream_kind=kind, stream_id=row_id, event_type=trigger, payload={}
        ),
        jobs=(),
        commands=(),
        audit=make_audit(
            subject_kind=kind, subject_id=row_id, action=trigger, outcome="committed"
        ),
        additional_locks=(),
    )
    outcome = await apply_transition(database, request)
    return outcome.to_state


def _signal_facts(**overrides: object) -> ProductSignalFacts:
    base: dict[str, object] = {
        "participation_occurred": True,
        "case_file_published": True,
    }
    base.update(overrides)
    return ProductSignalFacts(**base)  # type: ignore[arg-type]


async def _new_case(
    database: Database, owner: SignedInUser, name: str, state: ProductSignalState
) -> CaseFixture:
    return await create_case(
        database,
        name=name,
        state=state,
        authorizing_auth_user_id=owner.auth_user_id,
        participant_auth_user_id=None,
    )


async def test_intake_item_transitions_commit(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """received -> routing -> routed, plus the routing_blocked recovery loop."""
    case = await _new_case(
        database, auth_users["owner_a"], "intake-walk", ProductSignalState.AWAITING_INTERVIEW
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.intake_items set state = %s, routing_outcome = null "
            "where id = %s",
            (IntakeItemState.RECEIVED.value, case.intake_item_id),
        )

    blocked_facts = IntakeItemFacts(
        routing_outcome=None, blocked_reason="required facts unresolved", route_committed=False
    )
    committed_facts = IntakeItemFacts(
        routing_outcome=RoutingOutcome.CREATE_PRODUCT_SIGNAL,
        blocked_reason=None,
        route_committed=False,
    )
    walk: Sequence[tuple[str, IntakeItemFacts, dict[str, ColumnValue], str]] = (
        ("begin_routing", committed_facts, {}, IntakeItemState.ROUTING.value),
        (
            "block_routing",
            blocked_facts,
            {"blocked_reason": "required facts unresolved"},
            IntakeItemState.ROUTING_BLOCKED.value,
        ),
        ("begin_routing", committed_facts, {}, IntakeItemState.ROUTING.value),
        (
            "commit_route",
            committed_facts,
            {"routing_outcome": RoutingOutcome.CREATE_PRODUCT_SIGNAL.value},
            IntakeItemState.ROUTED.value,
        ),
    )
    for trigger, facts, mutation, expected in walk:
        reached = await _commit(
            database,
            INTAKE_ITEM_MACHINE,
            facts,
            client_id=case.client_id,
            kind=AggregateKind.INTAKE_ITEM,
            row_id=case.intake_item_id,
            table="intake_items",
            column="state",
            trigger=trigger,
            mutation=mutation,
        )
        assert reached == expected
    assert await event_sequences(
        database, stream_kind=AggregateKind.INTAKE_ITEM, stream_id=case.intake_item_id
    ) == [1, 2, 3, 4]


async def test_product_signal_walks_to_closed_validated(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The diagram's happy path commits end to end with sequenced Events."""
    case = await _new_case(
        database, auth_users["owner_a"], "signal-happy", ProductSignalState.AWAITING_INTERVIEW
    )
    for trigger, expected in (
        ("setup_ready", ProductSignalState.INTERVIEWING),
        ("evidence_sufficient", ProductSignalState.GATE_PENDING),
        ("authorize_implementation", ProductSignalState.IMPLEMENTING),
        ("candidate_ready", ProductSignalState.AWAITING_VALIDATION),
        ("participant_ready", ProductSignalState.VALIDATING),
        ("validated", ProductSignalState.CLOSED_VALIDATED),
    ):
        reached = await _commit(
            database,
            PRODUCT_SIGNAL_MACHINE,
            _signal_facts(),
            client_id=case.client_id,
            kind=AggregateKind.PRODUCT_SIGNAL,
            row_id=case.product_signal_id,
            table="product_signals",
            column="state",
            trigger=trigger,
            mutation={},
        )
        assert reached == expected.value
    assert await event_sequences(
        database, stream_kind=AggregateKind.PRODUCT_SIGNAL, stream_id=case.product_signal_id
    ) == [1, 2, 3, 4, 5, 6]
    assert len(await audit_actions(database, subject_id=case.product_signal_id)) == 6


async def test_terminal_expiry_transaction_shape(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Terminal expiry closes as a blocker and records its reason in one commit."""
    case = await _new_case(
        database, auth_users["owner_a"], "signal-expiry", ProductSignalState.AWAITING_VALIDATION
    )
    reached = await _commit(
        database,
        PRODUCT_SIGNAL_MACHINE,
        _signal_facts(participation_occurred=True, case_file_published=True),
        client_id=case.client_id,
        kind=AggregateKind.PRODUCT_SIGNAL,
        row_id=case.product_signal_id,
        table="product_signals",
        column="state",
        trigger="close_blocked",
        mutation={
            "closure_reason": "validation_contact_expiry",
            "return_deadline_at": None,
        },
    )
    assert reached == ProductSignalState.CLOSED_BLOCKED.value
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select closure_reason, return_deadline_at from public.product_signals "
            "where id = %s",
            (case.product_signal_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["closure_reason"] == "validation_contact_expiry"
    assert row["return_deadline_at"] is None


async def test_terminal_expiry_without_participation_is_refused(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Closing as a blocker before participation is refused, and nothing commits."""
    case = await _new_case(
        database, auth_users["owner_a"], "signal-expiry-early", ProductSignalState.AWAITING_INTERVIEW
    )
    with pytest.raises(GuardRejectedError, match="recorded participation"):
        await _commit(
            database,
            PRODUCT_SIGNAL_MACHINE,
            _signal_facts(participation_occurred=False),
            client_id=case.client_id,
            kind=AggregateKind.PRODUCT_SIGNAL,
            row_id=case.product_signal_id,
            table="product_signals",
            column="state",
            trigger="close_blocked",
            mutation={},
        )
    state, revision = await read_state(
        database, table="product_signals", row_id=case.product_signal_id, column="state"
    )
    assert (state, revision) == (ProductSignalState.AWAITING_INTERVIEW.value, 0)


async def test_outreach_attempt_transitions_commit(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The outreach lifecycle reaches claimed and its alternates commit too."""
    case = await _new_case(
        database, auth_users["owner_a"], "outreach-walk", ProductSignalState.AWAITING_INTERVIEW
    )
    attempt_id = await create_outreach_attempt(
        database, case=case, state=OutreachAttemptState.SCHEDULED, expires_in_seconds=3600
    )
    facts = OutreachAttemptFacts(
        bound_identity_matches=True, expired=False, contact_budget_remaining=2
    )
    for trigger, expected in (
        ("dispatch", OutreachAttemptState.DISPATCHING),
        ("delivery_confirmed", OutreachAttemptState.AWAITING_RESPONSE),
        ("claim", OutreachAttemptState.CLAIMED),
    ):
        reached = await _commit(
            database,
            OUTREACH_ATTEMPT_MACHINE,
            facts,
            client_id=case.client_id,
            kind=AggregateKind.OUTREACH_ATTEMPT,
            row_id=attempt_id,
            table="outreach_attempts",
            column="state",
            trigger=trigger,
            mutation={},
        )
        assert reached == expected.value

    revoked = await create_outreach_attempt(
        database, case=case, state=OutreachAttemptState.AWAITING_RESPONSE, expires_in_seconds=60
    )
    assert (
        await _commit(
            database,
            OUTREACH_ATTEMPT_MACHINE,
            facts,
            client_id=case.client_id,
            kind=AggregateKind.OUTREACH_ATTEMPT,
            row_id=revoked,
            table="outreach_attempts",
            column="state",
            trigger="revoke",
            mutation={"reason": "client_case_stop"},
        )
        == OutreachAttemptState.REVOKED.value
    )


async def test_interview_session_and_voice_connection_transitions_commit(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Session and voice lifecycles commit independently on the same case."""
    case = await _new_case(
        database, auth_users["owner_a"], "session-walk", ProductSignalState.INTERVIEWING
    )
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.INVITED, conversational_ready=True
    )
    session_facts = InterviewSessionFacts(
        identity_matches=True,
        participant_setup_passed=True,
        conversational_ready=True,
        observation_ready=True,
    )
    for trigger, expected in (
        ("claim", InterviewSessionState.CLAIMED),
        ("begin_preparation", InterviewSessionState.PREPARING),
        ("admit", InterviewSessionState.LIVE),
    ):
        reached = await _commit(
            database,
            INTERVIEW_SESSION_MACHINE,
            session_facts,
            client_id=case.client_id,
            kind=AggregateKind.INTERVIEW_SESSION,
            row_id=session_id,
            table="interview_sessions",
            column="state",
            trigger=trigger,
            mutation={},
        )
        assert reached == expected.value

    connection_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.voice_connections (
                id, client_id, interview_session_id, state, voice_run_id,
                participant_connection_id
            )
            values (%s, %s, %s, %s, %s, %s)
            """,
            (
                connection_id,
                case.client_id,
                session_id,
                VoiceConnectionState.CONNECTING.value,
                uuid7(),
                connection_id,
            ),
        )
    voice_facts = VoiceConnectionFacts(provider_session_bound=True, superseded=False)
    for trigger, expected in (
        ("activate", VoiceConnectionState.ACTIVE),
        ("lose_transport", VoiceConnectionState.RECONNECTING),
        ("restore", VoiceConnectionState.ACTIVE),
        ("close", VoiceConnectionState.CLOSED),
    ):
        reached = await _commit(
            database,
            VOICE_CONNECTION_MACHINE,
            voice_facts,
            client_id=case.client_id,
            kind=AggregateKind.VOICE_CONNECTION,
            row_id=connection_id,
            table="voice_connections",
            column="state",
            trigger=trigger,
            mutation={},
        )
        assert reached == expected.value

    assert (
        await _commit(
            database,
            INTERVIEW_SESSION_MACHINE,
            session_facts,
            client_id=case.client_id,
            kind=AggregateKind.INTERVIEW_SESSION,
            row_id=session_id,
            table="interview_sessions",
            column="state",
            trigger="close",
            mutation={"closure_reason": "finish_later", "closed_at": datetime.now(tz=UTC)},
        )
        == InterviewSessionState.CLOSED.value
    )


async def _seed_attempt(
    database: Database,
    case: CaseFixture,
    state: ImplementationAttemptState,
) -> tuple[UUID, UUID]:
    decision_id = await create_gate_decision(
        database, case=case, decision="approved_live", classification_revision=0, blocker_reason=None
    )
    attempt_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.implementation_attempts (
                id, client_id, product_signal_id, gate_decision_id, state,
                repository_ref, base_revision, consumed_budgets
            )
            values (%s, %s, %s, %s, %s, 'shopalytics/app', 'abc123', %s)
            """,
            (
                attempt_id,
                case.client_id,
                case.product_signal_id,
                decision_id,
                state.value,
                Jsonb({}),
            ),
        )
    return attempt_id, decision_id


async def test_implementation_attempt_transitions_commit(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """An attempt reaches candidate_ready through the kernel under a live lease."""
    case = await _new_case(
        database, auth_users["owner_a"], "attempt-walk", ProductSignalState.IMPLEMENTING
    )
    attempt_id, _ = await _seed_attempt(
        database, case, ImplementationAttemptState.AUTHORIZED)
    facts = ImplementationAttemptFacts(
        lease_active=True,
        fencing_token_current=True,
        execution_budget_remaining=True,
        within_return_deadline=True,
        readiness_fresh=True,
        diff_within_approved_scope=True,
    )
    for trigger, expected in (
        ("queue", ImplementationAttemptState.QUEUED),
        ("acquire_environment", ImplementationAttemptState.ACQUIRING_ENVIRONMENT),
        ("begin_execution", ImplementationAttemptState.EXECUTING),
        ("run_checks", ImplementationAttemptState.CHECKING),
        ("correction_cycle", ImplementationAttemptState.EXECUTING),
        ("pause", ImplementationAttemptState.PAUSED),
        ("resume", ImplementationAttemptState.EXECUTING),
        ("block_infrastructure", ImplementationAttemptState.INFRASTRUCTURE_BLOCKED),
        ("resume_after_repair", ImplementationAttemptState.EXECUTING),
        ("run_checks", ImplementationAttemptState.CHECKING),
        ("checks_passed", ImplementationAttemptState.PUBLISHING_CANDIDATE),
        ("publish_candidate", ImplementationAttemptState.CANDIDATE_READY),
    ):
        reached = await _commit(
            database,
            IMPLEMENTATION_ATTEMPT_MACHINE,
            facts,
            client_id=case.client_id,
            kind=AggregateKind.IMPLEMENTATION_ATTEMPT,
            row_id=attempt_id,
            table="implementation_attempts",
            column="state",
            trigger=trigger,
            mutation={},
        )
        assert reached == expected.value


async def test_one_attempt_in_flight_per_product_signal_is_enforced(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A case holds at most one attempt in flight; a finished one frees it for the next."""
    case = await _new_case(
        database, auth_users["owner_a"], "attempt-unique", ProductSignalState.IMPLEMENTING
    )
    await _seed_attempt(database, case, ImplementationAttemptState.EXECUTING)
    with pytest.raises(UniqueViolation, match="implementation_attempts_one_in_flight"):
        await _seed_attempt(database, case, ImplementationAttemptState.AUTHORIZED)

    # A change the person asks for after the first candidate is a second attempt.
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.implementation_attempts set state = %s "
            "where product_signal_id = %s",
            (ImplementationAttemptState.CANDIDATE_READY.value, case.product_signal_id),
        )
    await _seed_attempt(database, case, ImplementationAttemptState.AUTHORIZED)


async def test_validation_attempt_transitions_commit(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A Validation Attempt binds one candidate and completes with its outcome."""
    case = await _new_case(
        database, auth_users["owner_a"], "validation-walk", ProductSignalState.VALIDATING
    )
    attempt_id, _ = await _seed_attempt(
        database, case, ImplementationAttemptState.CANDIDATE_READY)
    session_id = await create_interview_session(
        database, case=case, state=InterviewSessionState.LIVE, conversational_ready=True
    )
    version_id = uuid7()
    allocation_id = uuid7()
    validation_id = uuid7()
    criterion_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.preview_versions (
                id, client_id, product_signal_id, implementation_attempt_id, state,
                source_commit, source_digest, build_digest, base_revision
            )
            values (%s, %s, %s, %s, %s, 'deadbeef', 'sha256:src', 'sha256:build', 'abc123')
            """,
            (
                version_id,
                case.client_id,
                case.product_signal_id,
                attempt_id,
                PreviewVersionState.READY.value,
            ),
        )
        await cur.execute(
            """
            insert into public.preview_runtime_allocations (
                id, client_id, product_signal_id, allocation_kind, pool_role, state,
                preview_version_id, origin_host, fencing_token
            )
            values (
                %s, %s, %s, %s, 'candidate', %s, %s, %s, utrace.next_fencing_token()
            )
            """,
            (
                allocation_id,
                case.client_id,
                case.product_signal_id,
                PreviewAllocationKind.CANDIDATE.value,
                PreviewRuntimeAllocationState.READY.value,
                version_id,
                f"{uuid7().hex}.preview.utrace.dev",
            ),
        )
        await cur.execute(
            """
            insert into public.validation_criteria (
                id, client_id, product_signal_id, criteria_revision, ordinal,
                statement, observability
            )
            values (%s, %s, %s, 0, 1, 'the saved view reopens with every filter', %s)
            """,
            (
                criterion_id,
                case.client_id,
                case.product_signal_id,
                CriterionObservability.OBSERVED.value,
            ),
        )
        await cur.execute(
            """
            insert into public.validation_attempts (
                id, client_id, product_signal_id, implementation_attempt_id,
                preview_version_id, preview_runtime_allocation_id,
                interview_session_id, client_user_id, state,
                dataset_manifest_digest, acceptance_criteria_revision,
                authenticated_identity_ref, workflow_ref
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'sha256:seed', 0,
                    'seeded-identity', 'saved-conversion-views')
            """,
            (
                validation_id,
                case.client_id,
                case.product_signal_id,
                attempt_id,
                version_id,
                allocation_id,
                session_id,
                case.client_user_id,
                ValidationAttemptState.PENDING.value,
            ),
        )

    facts = ValidationAttemptFacts(
        originating_user_matches=True,
        candidate_ready=True,
        observation_ready=True,
        same_candidate=True,
        outcome=ValidationOutcome.VALIDATED,
        criteria_total=1,
        criteria_affirmatively_proven=1,
        user_confirmed_every_criterion=True,
        unresolved_contradiction=False,
    )
    for trigger, expected, mutation in (
        ("begin", ValidationAttemptState.IN_PROGRESS, {}),
        ("interrupt", ValidationAttemptState.INTERRUPTED, {}),
        ("resume", ValidationAttemptState.IN_PROGRESS, {}),
        (
            "complete",
            ValidationAttemptState.COMPLETED,
            {
                "outcome": ValidationOutcome.VALIDATED.value,
                "completed_at": datetime.now(tz=UTC),
                # A completed attempt always carries the frozen result delivery
                # may begin from, and its digest.
                "frozen_result": Jsonb({"outcome": ValidationOutcome.VALIDATED.value}),
                "frozen_result_digest": "sha256:frozen",
            },
        ),
    ):
        reached = await _commit(
            database,
            VALIDATION_ATTEMPT_MACHINE,
            facts,
            client_id=case.client_id,
            kind=AggregateKind.VALIDATION_ATTEMPT,
            row_id=validation_id,
            table="validation_attempts",
            column="state",
            trigger=trigger,
            mutation=dict(mutation),
        )
        assert reached == expected.value


async def test_case_file_stages_commit(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """The Case File reaches published with its immutable core digest set."""
    case = await _new_case(
        database, auth_users["owner_a"], "casefile-walk", ProductSignalState.VALIDATING
    )
    case_file_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.case_files (
                id, client_id, product_signal_id, variant, stage
            )
            values (%s, %s, %s, %s, %s)
            """,
            (
                case_file_id,
                case.client_id,
                case.product_signal_id,
                CaseFileVariant.BLOCKED.value,
                CaseFileStage.DRAFT.value,
            ),
        )
    facts = CaseFileFacts(
        variant=CaseFileVariant.BLOCKED,
        citations_verified=True,
        mandatory_evidence_present=True,
        delivery_identity_verified=False,
        core_digest_present=True,
    )
    for trigger, expected, mutation in (
        ("verify", CaseFileStage.VERIFIED, {}),
        ("resolve_delivery", CaseFileStage.DELIVERY_RESOLVED, {}),
        (
            "seal",
            CaseFileStage.READY,
            {
                "core": Jsonb({"summary": "blocked before a candidate"}),
                "core_canonical": '{"summary":"blocked before a candidate"}',
                "core_digest": "sha256:core",
                "sealed_at": datetime.now(tz=UTC),
            },
        ),
        ("publish", CaseFileStage.PUBLISHED, {"published_at": datetime.now(tz=UTC)}),
    ):
        reached = await _commit(
            database,
            CASE_FILE_MACHINE,
            facts,
            client_id=case.client_id,
            kind=AggregateKind.CASE_FILE,
            row_id=case_file_id,
            table="case_files",
            column="stage",
            trigger=trigger,
            mutation=dict(mutation),
        )
        assert reached == expected.value


async def test_immutable_records_reject_updates(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Gate decisions, Events, and audit records are append-only in the database."""
    case = await _new_case(
        database, auth_users["owner_a"], "immutability", ProductSignalState.GATE_PENDING
    )
    decision_id = await create_gate_decision(
        database, case=case, decision="approved_live", classification_revision=0, blocker_reason=None
    )
    with pytest.raises(RestrictViolation, match="append-only"):
        async with database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                "update public.gate_decisions set blocker_reason = 'rewritten' "
                "where id = %s",
                (decision_id,),
            )
    with pytest.raises(RestrictViolation, match="append-only"):
        async with database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                "delete from public.gate_decisions where id = %s", (decision_id,)
            )

    # Events and audit records written by a committed transition are frozen too.
    await _commit(
        database,
        PRODUCT_SIGNAL_MACHINE,
        _signal_facts(),
        client_id=case.client_id,
        kind=AggregateKind.PRODUCT_SIGNAL,
        row_id=case.product_signal_id,
        table="product_signals",
        column="state",
        trigger="authorize_implementation",
        mutation={},
    )
    with pytest.raises(RestrictViolation, match="append-only"):
        async with database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                "update public.events set event_type = 'rewritten' where stream_id = %s",
                (case.product_signal_id,),
            )
    with pytest.raises(RestrictViolation, match="append-only"):
        async with database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                "delete from public.audit_records where subject_id = %s",
                (case.product_signal_id,),
            )


async def test_environment_lease_fencing_tokens_increase(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A new lease outranks the previous one and only one stays active."""
    case = await _new_case(
        database, auth_users["owner_a"], "lease-fencing", ProductSignalState.IMPLEMENTING
    )
    attempt_id, _ = await _seed_attempt(
        database, case, ImplementationAttemptState.ACQUIRING_ENVIRONMENT)
    first = await grant_environment_lease(
        database,
        client_id=case.client_id,
        implementation_attempt_id=attempt_id,
        workspace_ref="sandbox://one",
        duration_seconds=60,
    )
    second = await grant_environment_lease(
        database,
        client_id=case.client_id,
        implementation_attempt_id=attempt_id,
        workspace_ref="sandbox://two",
        duration_seconds=60,
    )
    assert second.fencing_token > first.fencing_token
    assert second.expires_at > datetime.now(tz=UTC) - timedelta(seconds=1)

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select id, state from public.environment_leases "
            "where implementation_attempt_id = %s order by fencing_token",
            (attempt_id,),
        )
        rows = await cur.fetchall()
    assert [str(row["state"]) for row in rows] == [
        EnvironmentLeaseState.FENCED.value,
        EnvironmentLeaseState.ACTIVE.value,
    ]
