"""Typed ingestion refusals.

Every refusal names the stage it reached, a stable code, and its retry
classification. None of them carries a rejected product value: a failure record
is safe to store, log, and forward to the control plane.
"""

from uuid import UUID

from app.errors import ControlPlaneError

__all__ = [
    "ArtifactIntegrityError",
    "BackpressureError",
    "ClockQualityError",
    "IdentityReuseError",
    "IngestionError",
    "IntegrityViolationError",
    "NeverCollectViolationError",
    "PayloadLimitError",
    "QueryAuthorizationError",
    "SourceValidationError",
    "TelemetryAuthenticationError",
    "WindowFrozenError",
]


class IngestionError(ControlPlaneError):
    """Base class for ingestion refusals.

    ``code`` is the stable identifier the control plane and the producer SDKs
    match on; ``stage`` is the exact journal stage the record reached.
    """

    code = "ingestion_failed"
    stage = "received"
    retry_classification = "never"

    def __init__(self, reason: str) -> None:
        super().__init__(f"{self.code}: {reason}")
        self.reason = reason


class TelemetryAuthenticationError(IngestionError):
    """The presented credential is absent, unknown, expired, or revoked."""

    code = "credential_rejected"
    stage = "received"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)


class PayloadLimitError(IngestionError):
    """A payload broke a fixed platform ceiling or a negotiated limit."""

    code = "payload_limit_violation"
    stage = "authenticated"

    def __init__(self, limit_name: str, observed: int, limit: int) -> None:
        super().__init__(
            f"{limit_name} is {limit}, the request presented {observed}"
        )
        self.limit_name = limit_name
        self.observed = observed
        self.limit = limit


class SourceValidationError(IngestionError):
    """The envelope, schema version, or typed payload failed validation."""

    code = "schema_incompatible"
    stage = "authenticated"

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"field '{field}' rejected: {reason}")
        self.field = field


class NeverCollectViolationError(IngestionError):
    """The payload carried material no client policy can re-enable.

    Nothing about the offending value survives: the payload is not written to the
    journal, object storage, logs, quarantine, or a content hash.
    """

    code = "unsafe_data_detected"
    stage = "authenticated"

    def __init__(self, rule_id: str, location: str) -> None:
        super().__init__(f"platform exclusion '{rule_id}' matched at {location}")
        self.rule_id = rule_id
        self.location = location


class IntegrityViolationError(IngestionError):
    """A source identity was reused with different content, or claimed another scope."""

    code = "duplicate_conflict"
    stage = "source_validated"

    def __init__(self, identity_kind: str, identity_value: str, reason: str) -> None:
        super().__init__(f"{identity_kind} '{identity_value}': {reason}")
        self.identity_kind = identity_kind
        self.identity_value = identity_value


class IdentityReuseError(IntegrityViolationError):
    """One source identity arrived twice with different content.

    Both versions stay as non-authoritative diagnostic facts, the source stream
    fails, and ingestion does not choose the later arrival. The hashes travel on
    the error so the conflict record can be written after the rejected
    transaction has rolled back.
    """

    def __init__(
        self,
        identity_kind: str,
        identity_value: str,
        first_content_sha256: str,
        conflicting_content_sha256: str,
        first_ingestion_receipt_id: UUID | None,
    ) -> None:
        super().__init__(
            identity_kind=identity_kind,
            identity_value=identity_value,
            reason="was reused with different content",
        )
        self.first_content_sha256 = first_content_sha256
        self.conflicting_content_sha256 = conflicting_content_sha256
        self.first_ingestion_receipt_id = first_ingestion_receipt_id


class ClockQualityError(IngestionError):
    """A time relationship is impossible and cannot be repaired by changing time."""

    code = "clock_quality_failure"
    stage = "normalized"

    def __init__(self, measurement: str, reason: str) -> None:
        super().__init__(f"{measurement}: {reason}")
        self.measurement = measurement


class BackpressureError(IngestionError):
    """A transient quota or capacity limit applies explicit backpressure."""

    code = "quota_backpressure"
    stage = "authenticated"
    retry_classification = "after_delay"

    def __init__(self, limit_name: str, retry_after_ms: int) -> None:
        super().__init__(f"{limit_name} exhausted; retry after {retry_after_ms} ms")
        self.limit_name = limit_name
        self.retry_after_ms = retry_after_ms


class ArtifactIntegrityError(IngestionError):
    """A resolved artifact did not match its expected scope, type, size, or hash."""

    code = "artifact_integrity_failure"
    stage = "indexed"

    def __init__(self, artifact_id: UUID, reason: str) -> None:
        super().__init__(f"artifact {artifact_id}: {reason}")
        self.artifact_id = artifact_id


class WindowFrozenError(IngestionError):
    """A frozen evidence window cannot be advanced, extended, or added to."""

    code = "evidence_window_frozen"
    stage = "available"

    def __init__(self, evidence_window_id: UUID) -> None:
        super().__init__(
            f"evidence window {evidence_window_id} is frozen; later data needs a new window"
        )
        self.evidence_window_id = evidence_window_id


class QueryAuthorizationError(IngestionError):
    """A read-only query named a scope, purpose, or source class it may not read."""

    code = "unauthorized_query"
    stage = "available"

    def __init__(self, subject: str, reason: str) -> None:
        super().__init__(f"query on {subject} refused: {reason}")
        self.subject = subject
