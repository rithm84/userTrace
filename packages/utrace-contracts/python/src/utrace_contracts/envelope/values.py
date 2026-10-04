"""Envelope value objects: scope, producer, authority, lineage, ordering, digest.

These are the parts of the envelope that are not the payload. They are separate
models because area schemas compose them directly, and because keeping tenant
scope, authority references, and stream position as distinct typed objects is
what stops a generic correlation identifier from standing in for a relationship.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from utrace_contracts.base import SnakeCaseName, StrictModel
from utrace_contracts.ids.models import UuidV7

__all__ = [
    "TIMESTAMP_PATTERN",
    "TRACEPARENT_PATTERN",
    "AuthorityKind",
    "AuthorityReference",
    "Causation",
    "ClientScope",
    "ClientTenantScope",
    "PayloadDigest",
    "PlatformScope",
    "Producer",
    "StreamPosition",
    "StreamType",
    "Timestamp",
    "TraceContext",
]

TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$"
"""RFC 3339 instant in UTC with a literal ``Z``. Local offsets are not accepted."""

TRACEPARENT_PATTERN = r"^00-(?!0{32})[0-9a-f]{32}-(?!0{16})[0-9a-f]{16}-[0-9a-f]{2}$"
"""W3C traceparent, version 00, with non-zero trace and parent identifiers."""

Timestamp = Annotated[str, StringConstraints(pattern=TIMESTAMP_PATTERN)]

AuthorityKind = Literal[
    "configuration_release",
    "implementation_gate_decision",
    "async_authorization",
    "environment_lease",
    "invitation",
    "validation_result",
    "outreach_offer_reservation",
]

StreamType = Literal[
    "intake_item",
    "product_signal",
    "outreach_attempt",
    "interview_session",
    "preview_runtime_allocation",
    "environment_lease",
    "implementation_attempt",
    "preview_version",
    "validation_attempt",
    "case_file",
    "delivery",
    "job",
    "job_run",
    "command",
]


class Producer(StrictModel):
    """The software that emitted a message, distinct from the actor it acted for."""

    component: SnakeCaseName
    component_version: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    instance_id: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None


class ClientTenantScope(StrictModel):
    """Scoped to one uTrace Client. One client must never see another client's records."""

    scope: Literal["client"]
    client_id: UuidV7


class PlatformScope(StrictModel):
    """Scoped to the platform itself, carrying no client records."""

    scope: Literal["platform"]


ClientScope = Annotated[ClientTenantScope | PlatformScope, Field(discriminator="scope")]


class AuthorityReference(StrictModel):
    """One pinned record a message relies on for authority.

    The reference is a claim, not a grant: the control plane rechecks the record,
    its revision, and any fencing token before it lets an effect happen.
    """

    kind: AuthorityKind
    reference_id: UuidV7
    revision: Annotated[int, Field(ge=0)] | None = None
    fencing_token: Annotated[int, Field(ge=1)] | None = None


class Causation(StrictModel):
    """Message lineage, preserved through historical replay. It confers no authority."""

    caused_by_message_id: UuidV7 | None = None
    root_message_id: UuidV7 | None = None


class TraceContext(StrictModel):
    """W3C trace context describing technical execution, never product identity."""

    traceparent: Annotated[str, StringConstraints(pattern=TRACEPARENT_PATTERN)]
    tracestate: Annotated[str, StringConstraints(max_length=512)] | None = None


class StreamPosition(StrictModel):
    """Position in one primary lifecycle stream.

    ``expected_revision`` is what a state-sensitive message was built against; a
    mismatch rejects before side effects. ``sequence`` is assigned by the control
    plane after acceptance, so a producer leaves it out.
    """

    stream_type: StreamType
    stream_id: UuidV7
    expected_revision: Annotated[int, Field(ge=0)] | None = None
    sequence: Annotated[int, Field(ge=1)] | None = None


class PayloadDigest(StrictModel):
    """SHA-256 over the RFC 8785 canonical form of the payload."""

    algorithm: Literal["sha256"]
    canonicalization: Literal["jcs"]
    value: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
