"""GitHub delivery: identity, machine, deterministic branch, and PR body.

The [delivery protocol](../../../../docs/arch-components/client-outputs.md#github-delivery)
runs in order: reserve the Case File identity and its stable authenticated URL,
preflight, create the draft PR with a stable hidden marker, reconcile and verify
the exact branch and head, then persist the verified identities.

This module owns the durable half of that protocol — the ``deliveries`` machine,
the deterministic branch name, and the sanitized pull-request body. The runner in
``app.outputs.delivery_runner`` performs the provider calls.

Nothing here force-pushes, deletes a branch, closes a human-visible draft, or
adopts anything but an exact marker, branch, and head match.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import DeliveryState

from app.connectors.github_delivery import DeliveredPullRequest, FilePatch
from app.db.rows import as_int, as_optional_str, as_str, as_uuid
from app.errors import AggregateNotFoundError
from app.lifecycle.machine import Machine, Transition
from app.outputs.bundle import ImplementationRecord

__all__ = [
    "DELIVERY_MACHINE",
    "DeliveryFacts",
    "DeliveryRepositoryPort",
    "DeliveryRow",
    "delivery_branch",
    "load_delivery",
    "pull_request_body",
    "pull_request_title",
    "reserve_delivery",
]

MACHINE_VERSION = 1

_BRANCH_PREFIX = "utrace/case-"


@dataclass(frozen=True, slots=True)
class DeliveryFacts:
    """Facts the delivery guards read. Every one is a verified provider answer."""

    validation_frozen: bool
    preflight_passed: bool
    marker_matches: bool
    branch_matches: bool
    head_verified: bool
    blocker_reason: str | None


def _may_preflight(facts: DeliveryFacts) -> str | None:
    if not facts.validation_frozen:
        return "delivery begins only from a frozen validated result"
    return None


def _may_create(facts: DeliveryFacts) -> str | None:
    if not facts.preflight_passed:
        return "preflight did not pass; delivery records a blocker instead"
    return None


def _may_deliver(facts: DeliveryFacts) -> str | None:
    if not facts.marker_matches:
        return "the pull request does not carry this delivery's exact marker"
    if not facts.branch_matches:
        return "the pull request head is not this delivery's deterministic branch"
    if not facts.head_verified:
        return "the branch no longer points at the exact delivered head"
    return None


def _may_block(facts: DeliveryFacts) -> str | None:
    if facts.blocker_reason is None:
        return "a blocked delivery must record its exact blocker"
    return None


DELIVERY_MACHINE: Machine[DeliveryFacts] = Machine(
    name="delivery",
    version=MACHINE_VERSION,
    initial=DeliveryState.RESERVED.value,
    terminal=frozenset({DeliveryState.DELIVERED.value, DeliveryState.BLOCKED.value}),
    transitions=(
        Transition(
            trigger="preflight",
            sources=frozenset({DeliveryState.RESERVED.value}),
            target=DeliveryState.PREFLIGHTING.value,
            guard=_may_preflight,
        ),
        Transition(
            trigger="create",
            sources=frozenset({DeliveryState.PREFLIGHTING.value}),
            target=DeliveryState.CREATING.value,
            guard=_may_create,
        ),
        Transition(
            trigger="reconcile",
            sources=frozenset(
                {
                    DeliveryState.CREATING.value,
                    DeliveryState.PREFLIGHTING.value,
                    DeliveryState.RECONCILING.value,
                }
            ),
            target=DeliveryState.RECONCILING.value,
            guard=None,
        ),
        Transition(
            trigger="deliver",
            sources=frozenset(
                {DeliveryState.CREATING.value, DeliveryState.RECONCILING.value}
            ),
            target=DeliveryState.DELIVERED.value,
            guard=_may_deliver,
        ),
        Transition(
            trigger="block",
            sources=frozenset(
                {
                    DeliveryState.RESERVED.value,
                    DeliveryState.PREFLIGHTING.value,
                    DeliveryState.CREATING.value,
                    DeliveryState.RECONCILING.value,
                }
            ),
            target=DeliveryState.BLOCKED.value,
            guard=_may_block,
        ),
    ),
)


@runtime_checkable
class DeliveryRepositoryPort(Protocol):
    """The exact repository operations the delivery protocol performs.

    ``GitHubAppDelivery`` satisfies this structurally. The protocol exists so the
    controller depends on the delivery role rather than on one authentication
    scheme, and so nothing wider than these seven operations is reachable.
    """

    @property
    def repository_full_name(self) -> str:
        """The one authorized repository this delivery may write to."""
        ...

    async def read_default_branch(self) -> str:
        """Return the repository's normal branch name."""
        ...

    async def resolve_branch_head(self, *, branch: str) -> str | None:
        """Return the commit a branch points at, or ``None`` when it is absent."""
        ...

    async def verify_head(self, *, branch: str, expected_head_sha: str) -> bool:
        """Confirm the branch still points at the exact delivered commit."""
        ...

    async def create_branch_commit(
        self,
        *,
        branch: str,
        base_sha: str,
        patches: Sequence[FilePatch],
        commit_message: str,
    ) -> str:
        """Create the branch and its one app-authored commit; return the head."""
        ...

    async def find_existing_delivery(
        self, *, marker: str
    ) -> DeliveredPullRequest | None:
        """Return the pull request already carrying this exact marker."""
        ...

    async def create_draft_pull_request(
        self, *, head_branch: str, base_branch: str, title: str, body: str, marker: str
    ) -> DeliveredPullRequest:
        """Open one draft pull request carrying the hidden idempotency marker."""
        ...


@dataclass(frozen=True, slots=True)
class DeliveryRow:
    """One delivery record: its identity, its state, and what it verified."""

    id: UUID
    client_id: UUID
    product_signal_id: UUID
    case_file_id: UUID
    validation_attempt_id: UUID
    state: DeliveryState
    revision: int
    command_id: UUID
    repository_ref: str
    branch_ref: str
    base_branch: str
    head_commit: str
    base_revision: str
    idempotency_marker: str
    case_file_url: str
    pull_request_ref: str | None
    pull_request_number: int | None
    verified_head_sha: str | None
    blocker_reason: str | None


def delivery_branch(*, product_signal_id: UUID) -> str:
    """Return the deterministic uTrace branch for the case's one candidate.

    Deterministic so a replayed delivery Command computes the identical name and
    reconciles against its own branch instead of opening a second one.
    """
    return f"{_BRANCH_PREFIX}{product_signal_id.hex[:12]}"


def pull_request_title(*, title: str, product_signal_id: UUID) -> str:
    """Return the PR title: the drafted title plus the case's opaque identity."""
    return f"{title} [uTrace {product_signal_id.hex[:8]}]"


def pull_request_body(
    *,
    problem_summary: str,
    implementation_summary: str,
    implementation: ImplementationRecord,
    risks: Sequence[str],
    case_file_url: str,
    validated_head: str,
) -> str:
    """Compose the sanitized pull-request body.

    It carries the problem and implementation summary, repository-specific files
    and checks, risks, and the authenticated Case File link. It carries no
    customer identity, transcript excerpt, telemetry, preview credential, or
    internal evaluation: everything here comes from repository-scoped identities
    and the two supplied summaries.
    """
    files = "\n".join(f"- `{path}`" for path in implementation.files_touched) or "- none"
    checks = (
        "\n".join(f"- `{check.name}`: {check.outcome}" for check in implementation.checks)
        or "- none recorded"
    )
    risk_lines = "\n".join(f"- {risk}" for risk in risks) or "- none recorded"
    return (
        f"## Problem\n\n{problem_summary}\n\n"
        f"## Implementation\n\n{implementation_summary}\n\n"
        f"## Files\n\n{files}\n\n"
        f"## Checks\n\n{checks}\n\n"
        f"## Risks\n\n{risk_lines}\n\n"
        "## Validated candidate\n\n"
        f"- validated source commit: `{validated_head}`\n"
        f"- source digest: `{implementation.source_digest}`\n"
        f"- build digest: `{implementation.build_digest}`\n"
        f"- tested base revision: `{implementation.base_revision}`\n\n"
        f"[Case File]({case_file_url})\n\n"
        "Validation covers this exact candidate. It does not carry over to later "
        "edits of this pull request or to a different merge result. uTrace does "
        "not merge its own pull requests.\n"
    )


async def reserve_delivery(
    conn: AsyncConnection[DictRow],
    *,
    delivery_id: UUID,
    client_id: UUID,
    product_signal_id: UUID,
    case_file_id: UUID,
    validation_attempt_id: UUID,
    command_id: UUID,
    repository_ref: str,
    branch_ref: str,
    base_branch: str,
    head_commit: str,
    base_revision: str,
    idempotency_marker: str,
    case_file_url: str,
) -> None:
    """Insert the reserved delivery row that owns this delivery's identity."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.deliveries (
                id, client_id, product_signal_id, case_file_id, validation_attempt_id,
                state, repository_ref, branch_ref, base_branch, head_commit,
                base_revision, idempotency_marker, case_file_url, command_id
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                delivery_id,
                client_id,
                product_signal_id,
                case_file_id,
                validation_attempt_id,
                DeliveryState.RESERVED.value,
                repository_ref,
                branch_ref,
                base_branch,
                head_commit,
                base_revision,
                idempotency_marker,
                case_file_url,
                command_id,
            ),
        )


async def load_delivery(
    conn: AsyncConnection[DictRow], *, delivery_id: UUID
) -> DeliveryRow:
    """Return one delivery record."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, client_id, product_signal_id, case_file_id,
                   validation_attempt_id, state, revision, command_id, repository_ref,
                   branch_ref, base_branch, head_commit, base_revision,
                   idempotency_marker, case_file_url, pull_request_ref,
                   pull_request_number, verified_head_sha, blocker_reason
            from public.deliveries where id = %s
            """,
            (delivery_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="deliveries", row_id=delivery_id)
    return DeliveryRow(
        id=as_uuid(row, "id"),
        client_id=as_uuid(row, "client_id"),
        product_signal_id=as_uuid(row, "product_signal_id"),
        case_file_id=as_uuid(row, "case_file_id"),
        validation_attempt_id=as_uuid(row, "validation_attempt_id"),
        state=DeliveryState(as_str(row, "state")),
        revision=as_int(row, "revision"),
        command_id=as_uuid(row, "command_id"),
        repository_ref=as_str(row, "repository_ref"),
        branch_ref=as_str(row, "branch_ref"),
        base_branch=as_str(row, "base_branch"),
        head_commit=as_str(row, "head_commit"),
        base_revision=as_str(row, "base_revision"),
        idempotency_marker=as_str(row, "idempotency_marker"),
        case_file_url=as_str(row, "case_file_url"),
        pull_request_ref=as_optional_str(row, "pull_request_ref"),
        pull_request_number=(
            None if row["pull_request_number"] is None else as_int(row, "pull_request_number")
        ),
        verified_head_sha=as_optional_str(row, "verified_head_sha"),
        blocker_reason=as_optional_str(row, "blocker_reason"),
    )


def delivered_identities(delivered: DeliveredPullRequest) -> Mapping[str, object]:
    """Return the verified identities a delivered row records."""
    return {
        "pull_request_ref": delivered.html_url,
        "pull_request_number": delivered.number,
        "pull_request_node_id": delivered.node_id,
        "verified_head_sha": delivered.head_sha,
        "branch_ref": delivered.head_branch,
        "base_branch": delivered.base_branch,
    }
