"""Running one GitHub delivery against the authorized repository.

Retries reuse the same command identity and adopt only an exact marker, branch,
and head match. An unknown provider outcome reconciles before any repetition, and
a delivery that cannot apply the exact validated source records a blocker rather
than claiming a different head is validated.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import ActorKind, AggregateKind, DeliveryState

from app.connectors.failures import ConnectorFailure, EffectState
from app.connectors.github_delivery import DeliveredPullRequest, FilePatch
from app.db.kernel import ColumnValue, apply_transition_on_connection
from app.db.pool import Database
from app.envelope import Actor, AuditIntent, EventAppend, JsonValue, StreamRef
from app.ids import uuid7
from app.lifecycle.machine import plan_transition
from app.logging import get_logger
from app.outputs.bundle import ImplementationRecord
from app.outputs.delivery import (
    DELIVERY_MACHINE,
    DeliveryFacts,
    DeliveryRepositoryPort,
    DeliveryRow,
    delivered_identities,
    load_delivery,
    pull_request_body,
    pull_request_title,
)

__all__ = ["DeliveryOutcome", "DeliveryRequest", "run_delivery"]

_log = get_logger(__name__)

_SERVICE_ACTOR = Actor(kind=ActorKind.SERVICE, ref="outputs.delivery")


@dataclass(frozen=True, slots=True)
class DeliveryRequest:
    """Everything one delivery writes, composed before any provider call."""

    delivery_id: UUID
    title: str
    problem_summary: str
    implementation_summary: str
    risks: tuple[str, ...]
    implementation: ImplementationRecord
    patches: tuple[FilePatch, ...]


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """The delivery's terminal state and the identities it verified."""

    delivery_id: UUID
    state: DeliveryState
    pull_request_ref: str | None
    pull_request_number: int | None
    verified_head_sha: str | None
    blocker_reason: str | None
    adopted_existing: bool


async def _transition(
    conn: AsyncConnection[DictRow],
    delivery: DeliveryRow,
    *,
    trigger: str,
    facts: DeliveryFacts,
    mutation: Mapping[str, ColumnValue],
    payload: Mapping[str, JsonValue],
) -> None:
    stream = StreamRef(kind=AggregateKind.DELIVERY, id=delivery.id)
    request = plan_transition(
        DELIVERY_MACHINE,
        facts,
        trigger=trigger,
        current_state=delivery.state.value,
        client_id=delivery.client_id,
        aggregate_kind=AggregateKind.DELIVERY,
        aggregate_id=delivery.id,
        expected_revision=delivery.revision,
        state_column="state",
        mutation=mutation,
        event=EventAppend(
            message_id=uuid7(),
            stream=stream,
            event_type=f"delivery_{trigger}",
            payload=payload,
            actor=_SERVICE_ACTOR,
            causation_id=delivery.command_id,
            correlation_id=delivery.product_signal_id,
            occurred_at=datetime.now(tz=UTC),
        ),
        jobs=(),
        commands=(),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=stream,
            action=f"delivery_{trigger}",
            outcome=trigger,
            reason_code=delivery.blocker_reason,
            actor=_SERVICE_ACTOR,
            pinned_configuration_release_id=None,
            detail=payload,
        ),
        additional_locks=(),
    )
    await apply_transition_on_connection(conn, request)


async def _step(
    database: Database,
    delivery_id: UUID,
    *,
    trigger: str,
    facts: DeliveryFacts,
    mutation: Mapping[str, ColumnValue],
    payload: Mapping[str, JsonValue],
) -> DeliveryRow:
    """Commit one delivery transition and return the refreshed row."""
    async with database.transaction() as conn:
        delivery = await load_delivery(conn, delivery_id=delivery_id)
        await _transition(
            conn,
            delivery,
            trigger=trigger,
            facts=facts,
            mutation=mutation,
            payload=payload,
        )
        return await load_delivery(conn, delivery_id=delivery_id)


async def _block(
    database: Database, delivery_id: UUID, *, reason: str
) -> DeliveryOutcome:
    """Record a delivery blocker instead of forcing or repeating the write."""
    facts = DeliveryFacts(
        validation_frozen=True,
        preflight_passed=False,
        marker_matches=False,
        branch_matches=False,
        head_verified=False,
        blocker_reason=reason,
    )
    delivery = await _step(
        database,
        delivery_id,
        trigger="block",
        facts=facts,
        mutation={"blocker_reason": reason},
        payload={"blocker_reason": reason},
    )
    _log.info(
        "delivery_blocked",
        delivery_id=str(delivery_id),
        repository=delivery.repository_ref,
        branch=delivery.branch_ref,
        blocker_reason=reason,
    )
    return DeliveryOutcome(
        delivery_id=delivery_id,
        state=DeliveryState.BLOCKED,
        pull_request_ref=None,
        pull_request_number=None,
        verified_head_sha=None,
        blocker_reason=reason,
        adopted_existing=False,
    )


async def _validation_is_frozen(database: Database, delivery: DeliveryRow) -> bool:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select frozen_result_digest is not null as frozen
            from public.validation_attempts where id = %s
            """,
            (delivery.validation_attempt_id,),
        )
        row = await cur.fetchone()
    return bool(row is not None and row["frozen"])


async def _adopt(
    database: Database,
    repository: DeliveryRepositoryPort,
    delivery: DeliveryRow,
    existing: DeliveredPullRequest,
) -> DeliveryOutcome:
    """Adopt an existing pull request only on an exact marker, branch, and head."""
    marker_matches = existing.idempotency_marker == delivery.idempotency_marker
    branch_matches = existing.head_branch == delivery.branch_ref
    head_verified = False
    if marker_matches and branch_matches:
        head_verified = await repository.verify_head(
            branch=existing.head_branch, expected_head_sha=existing.head_sha
        )
    facts = DeliveryFacts(
        validation_frozen=True,
        preflight_passed=True,
        marker_matches=marker_matches,
        branch_matches=branch_matches,
        head_verified=head_verified,
        blocker_reason=None,
    )
    if not (marker_matches and branch_matches and head_verified):
        return await _block(
            database,
            delivery.id,
            reason=(
                "an existing pull request does not match this delivery exactly "
                f"(marker={marker_matches}, branch={branch_matches}, "
                f"head={head_verified}); uTrace does not force-push or close it"
            ),
        )
    identities = dict(delivered_identities(existing))
    delivered = await _step(
        database,
        delivery.id,
        trigger="deliver",
        facts=facts,
        mutation={
            "pull_request_ref": str(identities["pull_request_ref"]),
            "pull_request_number": int(str(identities["pull_request_number"])),
            "pull_request_node_id": str(identities["pull_request_node_id"]),
            "verified_head_sha": str(identities["verified_head_sha"]),
            "branch_ref": str(identities["branch_ref"]),
            "base_branch": str(identities["base_branch"]),
            "reconciliation": Jsonb({"adopted_existing": True}),
        },
        payload={"pull_request_number": existing.number, "adopted_existing": True},
    )
    return DeliveryOutcome(
        delivery_id=delivery.id,
        state=DeliveryState.DELIVERED,
        pull_request_ref=delivered.pull_request_ref,
        pull_request_number=delivered.pull_request_number,
        verified_head_sha=delivered.verified_head_sha,
        blocker_reason=None,
        adopted_existing=True,
    )


async def run_delivery(
    database: Database,
    repository: DeliveryRepositoryPort,
    *,
    request: DeliveryRequest,
) -> DeliveryOutcome:
    """Run the ordered delivery protocol for one reserved delivery."""
    async with database.connection() as conn:
        delivery = await load_delivery(conn, delivery_id=request.delivery_id)
    if delivery.state is DeliveryState.DELIVERED:
        return DeliveryOutcome(
            delivery_id=delivery.id,
            state=DeliveryState.DELIVERED,
            pull_request_ref=delivery.pull_request_ref,
            pull_request_number=delivery.pull_request_number,
            verified_head_sha=delivery.verified_head_sha,
            blocker_reason=None,
            adopted_existing=True,
        )

    frozen = await _validation_is_frozen(database, delivery)
    delivery = await _step(
        database,
        delivery.id,
        trigger="preflight",
        facts=DeliveryFacts(
            validation_frozen=frozen,
            preflight_passed=False,
            marker_matches=False,
            branch_matches=False,
            head_verified=False,
            blocker_reason=None,
        ),
        mutation={},
        payload={"repository_ref": delivery.repository_ref},
    )

    try:
        preflight, refusal = await _preflight(repository, delivery)
    except ConnectorFailure as failure:
        return await _block(
            database,
            delivery.id,
            reason=f"preflight could not read the repository: {failure.detail.message}",
        )
    if refusal is not None:
        return await _block(database, delivery.id, reason=refusal)

    existing = preflight.existing
    if existing is not None:
        return await _adopt(database, repository, delivery, existing)

    delivery = await _step(
        database,
        delivery.id,
        trigger="create",
        facts=DeliveryFacts(
            validation_frozen=True,
            preflight_passed=True,
            marker_matches=False,
            branch_matches=False,
            head_verified=False,
            blocker_reason=None,
        ),
        mutation={"preflight": Jsonb(preflight.as_record())},
        payload={"base_branch": delivery.base_branch, "branch_ref": delivery.branch_ref},
    )

    try:
        # The commit is the validated candidate exactly: its patches on the base it
        # was tested on, so the pull request shows any later change to the base
        # branch as a merge rather than presenting an untested commit as validated.
        head_sha = await repository.create_branch_commit(
            branch=delivery.branch_ref,
            base_sha=request.implementation.base_revision,
            patches=list(request.patches),
            commit_message=_commit_message(request, delivery),
        )
        delivered = await repository.create_draft_pull_request(
            head_branch=delivery.branch_ref,
            base_branch=delivery.base_branch,
            title=pull_request_title(
                title=request.title, product_signal_id=delivery.product_signal_id
            ),
            body=pull_request_body(
                problem_summary=request.problem_summary,
                implementation_summary=request.implementation_summary,
                implementation=request.implementation,
                risks=request.risks,
                case_file_url=delivery.case_file_url,
                delivered_commit=head_sha,
            ),
            marker=delivery.idempotency_marker,
        )
    except ConnectorFailure as failure:
        if failure.detail.effect_state is EffectState.ABSENT:
            return await _block(
                database,
                delivery.id,
                reason=f"delivery failed with no effect: {failure.detail.message}",
            )
        return await _reconcile_after_uncertainty(
            database, repository, delivery, reason=failure.detail.message
        )

    if head_sha != delivered.head_sha:
        return await _block(
            database,
            delivery.id,
            reason=(
                f"the pull request head {delivered.head_sha} is not the commit "
                f"delivery wrote ({head_sha})"
            ),
        )
    return await _finish(database, repository, delivery, delivered)


async def _finish(
    database: Database,
    repository: DeliveryRepositoryPort,
    delivery: DeliveryRow,
    delivered: DeliveredPullRequest,
) -> DeliveryOutcome:
    """Reconcile the created pull request and verify its exact head."""
    delivery = await _step(
        database,
        delivery.id,
        trigger="reconcile",
        facts=DeliveryFacts(
            validation_frozen=True,
            preflight_passed=True,
            marker_matches=True,
            branch_matches=True,
            head_verified=False,
            blocker_reason=None,
        ),
        mutation={},
        payload={"pull_request_number": delivered.number},
    )
    confirmed = await repository.find_existing_delivery(
        marker=delivery.idempotency_marker
    )
    marker_matches = confirmed is not None and confirmed.number == delivered.number
    branch_matches = delivered.head_branch == delivery.branch_ref
    head_verified = await repository.verify_head(
        branch=delivered.head_branch, expected_head_sha=delivered.head_sha
    )
    if not (marker_matches and branch_matches and head_verified):
        return await _block(
            database,
            delivery.id,
            reason=(
                "the created pull request did not reconcile exactly "
                f"(marker={marker_matches}, branch={branch_matches}, "
                f"head={head_verified})"
            ),
        )
    identities = dict(delivered_identities(delivered))
    final = await _step(
        database,
        delivery.id,
        trigger="deliver",
        facts=DeliveryFacts(
            validation_frozen=True,
            preflight_passed=True,
            marker_matches=True,
            branch_matches=True,
            head_verified=True,
            blocker_reason=None,
        ),
        mutation={
            "pull_request_ref": str(identities["pull_request_ref"]),
            "pull_request_number": int(str(identities["pull_request_number"])),
            "pull_request_node_id": str(identities["pull_request_node_id"]),
            "verified_head_sha": str(identities["verified_head_sha"]),
            "reconciliation": Jsonb({"adopted_existing": False}),
        },
        payload={"pull_request_number": delivered.number, "adopted_existing": False},
    )
    _log.info(
        "delivery_completed",
        delivery_id=str(delivery.id),
        repository=delivery.repository_ref,
        branch=delivery.branch_ref,
        pull_request_number=final.pull_request_number,
        verified_head_sha=final.verified_head_sha,
    )
    return DeliveryOutcome(
        delivery_id=delivery.id,
        state=DeliveryState.DELIVERED,
        pull_request_ref=final.pull_request_ref,
        pull_request_number=final.pull_request_number,
        verified_head_sha=final.verified_head_sha,
        blocker_reason=None,
        adopted_existing=False,
    )


async def _reconcile_after_uncertainty(
    database: Database,
    repository: DeliveryRepositoryPort,
    delivery: DeliveryRow,
    *,
    reason: str,
) -> DeliveryOutcome:
    """Resolve an uncertain write by its marker before any repetition."""
    delivery = await _step(
        database,
        delivery.id,
        trigger="reconcile",
        facts=DeliveryFacts(
            validation_frozen=True,
            preflight_passed=True,
            marker_matches=False,
            branch_matches=False,
            head_verified=False,
            blocker_reason=None,
        ),
        mutation={"reconciliation": Jsonb({"uncertain_outcome": reason})},
        payload={"uncertain_outcome": reason},
    )
    existing = await repository.find_existing_delivery(
        marker=delivery.idempotency_marker
    )
    if existing is None:
        return await _block(
            database,
            delivery.id,
            reason=f"the delivery outcome could not be established: {reason}",
        )
    return await _adopt(database, repository, delivery, existing)


@dataclass(frozen=True, slots=True)
class _Preflight:
    """What preflight established before anything was written."""

    repository_full_name: str
    default_branch: str
    base_branch: str
    base_sha: str
    branch_head: str | None
    existing: DeliveredPullRequest | None

    def as_record(self) -> dict[str, JsonValue]:
        """Return the preflight result the delivery row records."""
        return {
            "repository": self.repository_full_name,
            "default_branch": self.default_branch,
            "base_branch": self.base_branch,
            "base_sha": self.base_sha,
            "branch_head": self.branch_head,
            "existing_pull_request": (
                None if self.existing is None else self.existing.number
            ),
        }


async def _preflight(
    repository: DeliveryRepositoryPort, delivery: DeliveryRow
) -> tuple[_Preflight, str | None]:
    """Check installation, repository, branches, permissions, and marker state."""
    if repository.repository_full_name != delivery.repository_ref:
        return (
            _Preflight(
                repository_full_name=repository.repository_full_name,
                default_branch="",
                base_branch=delivery.base_branch,
                base_sha="",
                branch_head=None,
                existing=None,
            ),
            (
                "the installation is bound to "
                f"{repository.repository_full_name}, not the authorized "
                f"{delivery.repository_ref}"
            ),
        )
    default_branch = await repository.read_default_branch()
    base_sha = await repository.resolve_branch_head(branch=delivery.base_branch)
    branch_head = await repository.resolve_branch_head(branch=delivery.branch_ref)
    existing = await repository.find_existing_delivery(
        marker=delivery.idempotency_marker
    )
    preflight = _Preflight(
        repository_full_name=repository.repository_full_name,
        default_branch=default_branch,
        base_branch=delivery.base_branch,
        base_sha=base_sha or "",
        branch_head=branch_head,
        existing=existing,
    )
    if base_sha is None:
        return preflight, f"the target branch {delivery.base_branch} does not exist"
    if branch_head is not None and existing is None:
        return preflight, (
            f"branch {delivery.branch_ref} already exists at {branch_head} without "
            "this delivery's pull request; uTrace does not force-push or delete it"
        )
    return preflight, None


def _commit_message(request: DeliveryRequest, delivery: DeliveryRow) -> str:
    """Compose the app-authored commit message for the validated change."""
    return (
        f"{request.title}\n\n"
        f"uTrace Product Signal {delivery.product_signal_id}\n"
        f"Source digest {request.implementation.source_digest}\n"
    )
