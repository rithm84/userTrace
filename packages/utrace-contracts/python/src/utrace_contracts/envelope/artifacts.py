"""The immutable artifact reference.

Large evidence never travels inside a domain message. It becomes an opaque
reference that carries identity, exact type, digest, size, creation time, tenant
scope, and retention class, and nothing else. There is deliberately no bucket
name, filesystem path, permanent URL, or credential: resolution goes through the
control plane, which rechecks tenant and object linkage and verifies type, size,
and digest before returning anything. ``extra="forbid"`` is what enforces that
here, so an attempt to smuggle a path in is a validation error.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from utrace_contracts.base import SnakeCaseName, StrictModel
from utrace_contracts.envelope.values import ClientScope, Timestamp
from utrace_contracts.ids.models import UuidV7

__all__ = ["ArtifactDigest", "ArtifactReference", "RetentionClass"]

MEDIA_TYPE_PATTERN = (
    r"^[a-z][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}"
    r"(; *[a-z0-9!#$&^_.+-]+=[^;]+)*$"
)

RetentionClass = Literal[
    "participant_personal",
    "client_evidence",
    "operational",
    "audit_minimized",
]
"""Which retention, export, and deletion rules govern the artifact.

``participant_personal`` is a participant's own audio and transcripts, included
in participant export and removed by participant deletion. ``client_evidence``
is client-scoped product evidence and output, immutable after acceptance and
excluded from participant export. ``operational`` is build, check, and journal
material on the shortest retention. ``audit_minimized`` is the minimized audit
fact deletion preserves with references tombstoned. The class is fixed at
creation; hold and deletion state live on the durable row, not in this reference.
"""


class ArtifactDigest(StrictModel):
    """SHA-256 over the artifact's exact stored bytes."""

    algorithm: Literal["sha256"]
    value: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ArtifactReference(StrictModel):
    """An opaque, immutable reference to one stored artifact."""

    artifact_id: UuidV7
    artifact_type: SnakeCaseName
    schema_version: Annotated[int, Field(ge=1)]
    media_type: Annotated[str, StringConstraints(pattern=MEDIA_TYPE_PATTERN, max_length=255)]
    digest: ArtifactDigest
    byte_size: Annotated[int, Field(ge=0)]
    created_at: Timestamp
    client_scope: ClientScope
    retention_class: RetentionClass
