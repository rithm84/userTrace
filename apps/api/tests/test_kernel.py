"""Transition kernel tests against the real database.

These prove the kernel's transactional guarantees: deterministic locking, the
expected-revision check, the legal-predecessor check, the monotonic per-stream
event sequence, the outbox write, and the audit record, all committed once.
"""

from __future__ import annotations

import asyncio

import pytest
from utrace_contracts.domain.lifecycle import (
    AggregateKind,
    JobState,
    ProductSignalState,
    TechnicalCategory,
)

from app.db.kernel import AggregateRef, apply_transition
from app.db.pool import Database
from app.errors import (
    AggregateNotFoundError,
    IllegalTransitionError,
    RevisionConflictError,
)
from app.ids import uuid7
from app.lifecycle.machine import plan_transition
from app.lifecycle.product_signal import PRODUCT_SIGNAL_MACHINE, ProductSignalFacts
from tests.factories import create_case
from tests.helpers import (
    audit_actions,
    count_rows,
    event_sequences,
    make_audit,
    make_event,
    make_job,
    read_state,
)
from tests.supabase_auth import SignedInUser

PERMISSIVE_FACTS = ProductSignalFacts(
    participation_occurred=True,
    case_file_published=True,
)


def _request(case_id, client_id, expected_revision, trigger, current_state, jobs):
    return plan_transition(
        PRODUCT_SIGNAL_MACHINE,
        PERMISSIVE_FACTS,
        trigger=trigger,
        current_state=current_state,
        client_id=client_id,
        aggregate_kind=AggregateKind.PRODUCT_SIGNAL,
        aggregate_id=case_id,
        expected_revision=expected_revision,
        state_column="state",
        mutation={},
        event=make_event(
            stream_kind=AggregateKind.PRODUCT_SIGNAL,
            stream_id=case_id,
            event_type=trigger,
            payload={"trigger": trigger},
        ),
        jobs=jobs,
        commands=(),
        audit=make_audit(
            subject_kind=AggregateKind.PRODUCT_SIGNAL,
            subject_id=case_id,
            action=trigger,
            outcome="committed",
        ),
        additional_locks=(),
    )


async def test_transition_writes_projection_event_outbox_and_audit(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """One transition commits the projection, Event, outbox Job, and audit once."""
    case = await create_case(
        database,
        name="kernel-happy",
        state=ProductSignalState.AWAITING_INTERVIEW,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    job = make_job(
        job_type="control_plane.test_followup",
        input={"product_signal_id": str(case.product_signal_id)},
        concurrency_key=None,
        cancellation_scope_id=case.cancellation_scope_id,
        max_runs=3,
        retryable_categories=[TechnicalCategory.PROVIDER_UNAVAILABLE],
        not_before_seconds=0,
    )
    outcome = await apply_transition(
        database,
        _request(
            case.product_signal_id,
            case.client_id,
            0,
            "setup_ready",
            ProductSignalState.AWAITING_INTERVIEW.value,
            (job,),
        ),
    )

    assert outcome.from_state == ProductSignalState.AWAITING_INTERVIEW.value
    assert outcome.to_state == ProductSignalState.INTERVIEWING.value
    assert outcome.revision == 1
    assert outcome.sequence == 1
    assert outcome.job_ids == (job.job_id,)

    state, revision = await read_state(
        database, table="product_signals", row_id=case.product_signal_id, column="state"
    )
    assert (state, revision) == (ProductSignalState.INTERVIEWING.value, 1)
    assert await event_sequences(
        database, stream_kind=AggregateKind.PRODUCT_SIGNAL, stream_id=case.product_signal_id
    ) == [1]
    assert ("setup_ready", "committed") in await audit_actions(
        database, subject_id=case.product_signal_id
    )
    job_state, _ = await read_state(
        database, table="jobs", row_id=job.job_id, column="state"
    )
    assert job_state == JobState.READY.value
    # The outbox row is the only path to an external effect and pgmq woke it.
    assert (
        await count_rows(
            database,
            table="outbox",
            where="job_id = %s and state = 'enqueued'",
            params=[job.job_id],
        )
        == 1
    )


async def test_stale_expected_revision_is_a_revision_conflict(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A transition planned against an old revision is refused, not applied."""
    case = await create_case(
        database,
        name="kernel-conflict",
        state=ProductSignalState.AWAITING_INTERVIEW,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    await apply_transition(
        database,
        _request(
            case.product_signal_id,
            case.client_id,
            0,
            "setup_ready",
            ProductSignalState.AWAITING_INTERVIEW.value,
            (),
        ),
    )
    with pytest.raises(RevisionConflictError) as refusal:
        await apply_transition(
            database,
            _request(
                case.product_signal_id,
                case.client_id,
                0,
                "evidence_sufficient",
                ProductSignalState.INTERVIEWING.value,
                (),
            ),
        )
    assert refusal.value.expected == 0
    assert refusal.value.actual == 1
    state, revision = await read_state(
        database, table="product_signals", row_id=case.product_signal_id, column="state"
    )
    assert (state, revision) == (ProductSignalState.INTERVIEWING.value, 1)


async def test_only_one_of_two_concurrent_transitions_wins(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Row locking plus the revision check makes conflicting transitions exclusive."""
    case = await create_case(
        database,
        name="kernel-race",
        state=ProductSignalState.AWAITING_INTERVIEW,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    requests = [
        _request(
            case.product_signal_id,
            case.client_id,
            0,
            "setup_ready",
            ProductSignalState.AWAITING_INTERVIEW.value,
            (),
        )
        for _ in range(2)
    ]
    results = await asyncio.gather(
        apply_transition(database, requests[0]),
        apply_transition(database, requests[1]),
        return_exceptions=True,
    )
    succeeded = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, BaseException)]
    assert len(succeeded) == 1
    assert len(refused) == 1
    assert isinstance(refused[0], RevisionConflictError | IllegalTransitionError)
    assert await event_sequences(
        database, stream_kind=AggregateKind.PRODUCT_SIGNAL, stream_id=case.product_signal_id
    ) == [1]


async def test_illegal_predecessor_state_is_refused_and_writes_nothing(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A trigger whose predecessor does not hold leaves no Event or audit row."""
    case = await create_case(
        database,
        name="kernel-illegal",
        state=ProductSignalState.AWAITING_INTERVIEW,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    request = plan_transition(
        PRODUCT_SIGNAL_MACHINE,
        PERMISSIVE_FACTS,
        trigger="validated",
        current_state=ProductSignalState.VALIDATING.value,
        client_id=case.client_id,
        aggregate_kind=AggregateKind.PRODUCT_SIGNAL,
        aggregate_id=case.product_signal_id,
        expected_revision=0,
        state_column="state",
        mutation={},
        event=make_event(
            stream_kind=AggregateKind.PRODUCT_SIGNAL,
            stream_id=case.product_signal_id,
            event_type="validated",
            payload={},
        ),
        jobs=(),
        commands=(),
        audit=make_audit(
            subject_kind=AggregateKind.PRODUCT_SIGNAL,
            subject_id=case.product_signal_id,
            action="validated",
            outcome="committed",
        ),
        additional_locks=(),
    )
    with pytest.raises(IllegalTransitionError):
        await apply_transition(database, request)
    assert await event_sequences(
        database, stream_kind=AggregateKind.PRODUCT_SIGNAL, stream_id=case.product_signal_id
    ) == []
    assert await audit_actions(database, subject_id=case.product_signal_id) == []


async def test_sequence_is_monotonic_per_primary_lifecycle_stream(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Each stream numbers its own Events; separate cases do not share a counter."""
    first = await create_case(
        database,
        name="kernel-seq-a",
        state=ProductSignalState.AWAITING_INTERVIEW,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    second = await create_case(
        database,
        name="kernel-seq-b",
        state=ProductSignalState.AWAITING_INTERVIEW,
        authorizing_auth_user_id=auth_users["owner_b"].auth_user_id,
        participant_auth_user_id=None,
    )
    for case, states in (
        (first, [("setup_ready", "awaiting_interview"), ("evidence_sufficient", "interviewing")]),
        (second, [("setup_ready", "awaiting_interview")]),
    ):
        for revision, (trigger, current) in enumerate(states):
            await apply_transition(
                database,
                _request(case.product_signal_id, case.client_id, revision, trigger, current, ()),
            )
    assert await event_sequences(
        database, stream_kind=AggregateKind.PRODUCT_SIGNAL, stream_id=first.product_signal_id
    ) == [1, 2]
    assert await event_sequences(
        database, stream_kind=AggregateKind.PRODUCT_SIGNAL, stream_id=second.product_signal_id
    ) == [1]


async def test_missing_aggregate_is_reported_by_table_and_identifier(
    database: Database,
) -> None:
    """A transition against a row that does not exist names the row it wanted."""
    missing = uuid7()
    request = _request(
        missing, uuid7(), 0, "setup_ready", ProductSignalState.AWAITING_INTERVIEW.value, ()
    )
    with pytest.raises(AggregateNotFoundError) as refusal:
        await apply_transition(database, request)
    assert refusal.value.table == "product_signals"
    assert refusal.value.row_id == missing


async def test_additional_locks_are_taken_in_deterministic_identifier_order(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Locking a parent alongside the aggregate still commits one transition."""
    case = await create_case(
        database,
        name="kernel-locks",
        state=ProductSignalState.AWAITING_INTERVIEW,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    request = plan_transition(
        PRODUCT_SIGNAL_MACHINE,
        PERMISSIVE_FACTS,
        trigger="setup_ready",
        current_state=ProductSignalState.AWAITING_INTERVIEW.value,
        client_id=case.client_id,
        aggregate_kind=AggregateKind.PRODUCT_SIGNAL,
        aggregate_id=case.product_signal_id,
        expected_revision=0,
        state_column="state",
        mutation={},
        event=make_event(
            stream_kind=AggregateKind.PRODUCT_SIGNAL,
            stream_id=case.product_signal_id,
            event_type="setup_ready",
            payload={},
        ),
        jobs=(),
        commands=(),
        audit=make_audit(
            subject_kind=AggregateKind.PRODUCT_SIGNAL,
            subject_id=case.product_signal_id,
            action="setup_ready",
            outcome="committed",
        ),
        additional_locks=(
            AggregateRef(
                kind=AggregateKind.INTAKE_ITEM,
                row_id=case.intake_item_id,
                expected_revision=0,
            ),
        ),
    )
    outcome = await apply_transition(database, request)
    assert outcome.to_state == ProductSignalState.INTERVIEWING.value


async def test_additional_lock_revision_mismatch_refuses_the_transition(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A stale parent revision refuses the child's transition before any write."""
    case = await create_case(
        database,
        name="kernel-lock-conflict",
        state=ProductSignalState.AWAITING_INTERVIEW,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    request = plan_transition(
        PRODUCT_SIGNAL_MACHINE,
        PERMISSIVE_FACTS,
        trigger="setup_ready",
        current_state=ProductSignalState.AWAITING_INTERVIEW.value,
        client_id=case.client_id,
        aggregate_kind=AggregateKind.PRODUCT_SIGNAL,
        aggregate_id=case.product_signal_id,
        expected_revision=0,
        state_column="state",
        mutation={},
        event=make_event(
            stream_kind=AggregateKind.PRODUCT_SIGNAL,
            stream_id=case.product_signal_id,
            event_type="setup_ready",
            payload={},
        ),
        jobs=(),
        commands=(),
        audit=make_audit(
            subject_kind=AggregateKind.PRODUCT_SIGNAL,
            subject_id=case.product_signal_id,
            action="setup_ready",
            outcome="committed",
        ),
        additional_locks=(
            AggregateRef(
                kind=AggregateKind.INTAKE_ITEM,
                row_id=case.intake_item_id,
                expected_revision=99,
            ),
        ),
    )
    with pytest.raises(RevisionConflictError) as refusal:
        await apply_transition(database, request)
    assert refusal.value.table == "intake_items"
    state, _ = await read_state(
        database, table="product_signals", row_id=case.product_signal_id, column="state"
    )
    assert state == ProductSignalState.AWAITING_INTERVIEW.value
