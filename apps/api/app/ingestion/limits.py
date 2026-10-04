"""Fixed platform ceilings and the negotiated limits an activation hands out.

The activation handshake supplies byte-rate, record-rate, in-flight batch, and
acknowledgement-lag limits beneath these ceilings. They are operational limits,
not client- or interview-level preferences, and a value above a ceiling is an
invalid activation rather than a silently clamped one.

Contract: docs/arch-components/telemetry-and-evidence.md#authentication-quotas-and-payload-limits
"""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.errors import PayloadLimitError

__all__ = [
    "BROWSER_MAX_BATCH_BYTES",
    "BROWSER_MAX_OBSERVATION_BYTES",
    "BrowserLimits",
    "ServerLimits",
    "default_browser_limits",
    "default_server_limits",
    "require_within",
]

#: A browser observation is limited to 256 KiB and an uncompressed batch to 1 MiB.
BROWSER_MAX_OBSERVATION_BYTES: Final[int] = 262_144
BROWSER_MAX_BATCH_BYTES: Final[int] = 1_048_576

#: A server OTLP request is limited to 4 MiB after decompression and 2,048 spans.
SERVER_MAX_REQUEST_BYTES: Final[int] = 4_194_304
SERVER_MAX_SPANS: Final[int] = 2_048

#: Guards applied before allocation or normalization.
MAX_COMPRESSION_RATIO: Final[int] = 100
MAX_ATTRIBUTE_COUNT: Final[int] = 128
MAX_ATTRIBUTE_VALUE_LENGTH: Final[int] = 512
MAX_NESTING_DEPTH: Final[int] = 12
MAX_ARRAY_LENGTH: Final[int] = 256

#: A baseline payload above this size moves to object storage as an artifact
#: instead of being stored inline with the observation row.
BASELINE_ARTIFACT_THRESHOLD_BYTES: Final[int] = 65_536


class BrowserLimits(BaseModel):
    """Negotiated browser limits, exactly the shape the SDK decodes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_observation_bytes: int = Field(ge=1, le=BROWSER_MAX_OBSERVATION_BYTES)
    max_batch_bytes: int = Field(ge=1, le=BROWSER_MAX_BATCH_BYTES)
    max_bytes_per_second: int = Field(ge=1)
    max_records_per_second: int = Field(ge=1)
    max_in_flight_batches: int = Field(ge=1)
    acknowledgement_lag_limit_ms: int = Field(ge=1)


class ServerRetryLimits(BaseModel):
    """Bounded exponential backoff with jitter for the server SDK exporter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_backoff_millis: int = Field(ge=1, le=60_000)
    max_backoff_millis: int = Field(ge=1, le=60_000)
    multiplier: float = Field(ge=1, le=10)
    jitter_ratio: float = Field(ge=0, le=1)
    max_attempts: int = Field(ge=1, le=12)


class ServerLimits(BaseModel):
    """Negotiated server export limits, exactly the shape the SDK decodes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_batch_spans: int = Field(ge=1, le=SERVER_MAX_SPANS)
    max_batch_bytes: int = Field(ge=1_024, le=SERVER_MAX_REQUEST_BYTES)
    max_queue_batches: int = Field(ge=1, le=1_024)
    max_queue_spans: int = Field(ge=1, le=65_536)
    readiness_high_water_ratio: float = Field(gt=0, lt=1)
    max_in_flight_batches: int = Field(ge=1, le=8)
    export_interval_millis: int = Field(ge=10, le=30_000)
    acknowledgement_lag_millis: int = Field(ge=100, le=300_000)
    flush_timeout_millis: int = Field(ge=100, le=30_000)
    retry: ServerRetryLimits


def default_browser_limits() -> BrowserLimits:
    """Return the operational browser limits one activation hands out."""
    return BrowserLimits(
        max_observation_bytes=BROWSER_MAX_OBSERVATION_BYTES,
        max_batch_bytes=BROWSER_MAX_BATCH_BYTES,
        max_bytes_per_second=2_097_152,
        max_records_per_second=2_000,
        max_in_flight_batches=4,
        acknowledgement_lag_limit_ms=15_000,
    )


def default_server_limits() -> ServerLimits:
    """Return the operational server limits one activation hands out."""
    return ServerLimits(
        max_batch_spans=SERVER_MAX_SPANS,
        max_batch_bytes=SERVER_MAX_REQUEST_BYTES,
        max_queue_batches=256,
        max_queue_spans=16_384,
        readiness_high_water_ratio=0.8,
        max_in_flight_batches=4,
        export_interval_millis=1_000,
        acknowledgement_lag_millis=15_000,
        flush_timeout_millis=5_000,
        retry=ServerRetryLimits(
            initial_backoff_millis=250,
            max_backoff_millis=8_000,
            multiplier=2.0,
            jitter_ratio=0.2,
            max_attempts=6,
        ),
    )


def require_within(limit_name: str, observed: int, limit: int) -> None:
    """Refuse an oversized payload before allocation or normalization."""
    if observed > limit:
        raise PayloadLimitError(limit_name=limit_name, observed=observed, limit=limit)
