"""Control-plane intents, built from the shared envelope contract.

The wire envelope, its actors, and the typed failure are defined once in
`utrace_contracts.envelope` and re-exported here. What stays local is the
commit-time *intent*: what a lifecycle controller hands the kernel to write in
one transaction. An intent is not a message; the kernel turns it into durable
rows and, for dispatch, into the shared envelope.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from utrace_contracts.base import JsonValue
from utrace_contracts.domain.lifecycle import ActorKind, AggregateKind, TechnicalCategory
from utrace_contracts.envelope import Failure

__all__ = [
    "TRANSIENT_CATEGORIES",
    "Actor",
    "AuditIntent",
    "CommandIntent",
    "EventAppend",
    "Failure",
    "JobIntent",
    "JsonValue",
    "StreamRef",
]


_TECHNICAL_CATEGORIES: frozenset[str] = frozenset(category.value for category in TechnicalCategory)

TRANSIENT_CATEGORIES: tuple[str, ...] = (
    TechnicalCategory.TIMEOUT.value,
    TechnicalCategory.RATE_LIMITED.value,
    TechnicalCategory.PROVIDER_UNAVAILABLE.value,
)
"""The failures a later run of the same Job can overcome without anything changing."""


@dataclass(frozen=True, slots=True)
class Actor:
    """Who a fact is attributed to. ``ref`` is an opaque scoped identifier."""

    kind: ActorKind
    ref: str | None


@dataclass(frozen=True, slots=True)
class StreamRef:
    """One primary lifecycle stream. Events are sequenced within it."""

    kind: AggregateKind
    id: UUID


@dataclass(frozen=True, slots=True)
class EventAppend:
    """An immutable past-tense fact accepted by a lifecycle controller."""

    message_id: UUID
    stream: StreamRef
    event_type: str
    payload: Mapping[str, JsonValue]
    actor: Actor
    causation_id: UUID | None
    correlation_id: UUID | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class JobIntent:
    """One durable responsibility, frozen at commit time."""

    job_id: UUID
    job_type: str
    input: Mapping[str, JsonValue]
    deadline_at: datetime
    timeout_seconds: int
    max_runs: int
    resource_budget: Mapping[str, JsonValue]
    retryable_categories: Sequence[str]
    """Failure categories that permit another run, as ``TechnicalCategory`` values.

    A run the lease-expiry sweep abandons is retried on the run budget alone, so
    its ``lease_expired`` category never needs listing here.
    """
    backoff_base_seconds: int
    backoff_max_seconds: int
    concurrency_key: str | None
    cancellation_scope_id: UUID | None
    not_before: datetime

    def __post_init__(self) -> None:
        unknown = sorted(set(self.retryable_categories) - _TECHNICAL_CATEGORIES)
        if unknown:
            raise ValueError(
                f"job {self.job_type} lists retryable categories that no failure can "
                f"carry: {unknown}; use TechnicalCategory values"
            )


@dataclass(frozen=True, slots=True)
class CommandIntent:
    """One immutable, single-purpose request. ``command_id`` is its idempotency key."""

    command_id: UUID
    command_type: str
    target: StreamRef
    payload: Mapping[str, JsonValue]
    declared_revisions: Mapping[str, JsonValue]
    cancellation_scope_id: UUID | None


@dataclass(frozen=True, slots=True)
class AuditIntent:
    """The append-only decision record written with every transition."""

    audit_id: UUID
    subject: StreamRef
    action: str
    outcome: str
    reason_code: str | None
    actor: Actor
    pinned_configuration_release_id: UUID | None
    detail: Mapping[str, JsonValue]
