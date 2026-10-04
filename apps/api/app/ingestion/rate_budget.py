"""The per-connection rate budget that produces explicit backpressure.

A transient quota or capacity limit applies explicit backpressure rather than
sampling, truncating, or silently dropping eligible telemetry. The browser
receives flow control without its acknowledgement advancing; the server receives
the corresponding retryable status and ``Retry-After``.

The budget is a pure token bucket over the activation's negotiated byte and
record rates. It holds no durable truth, so a reconnect on another instance
starts a fresh bucket against the same durable stream cursor.
"""

from datetime import datetime

from app.ingestion.errors import BackpressureError

__all__ = ["RateBudget"]


class RateBudget:
    """Two token buckets: one over bytes per second, one over records per second."""

    def __init__(self, *, max_bytes_per_second: int, max_records_per_second: int) -> None:
        if max_bytes_per_second < 1 or max_records_per_second < 1:
            raise ValueError("a rate budget needs positive byte and record rates")
        self._max_bytes = max_bytes_per_second
        self._max_records = max_records_per_second
        self._bytes = float(max_bytes_per_second)
        self._records = float(max_records_per_second)
        self._last_refill: datetime | None = None

    def _refill(self, now: datetime) -> None:
        last = self._last_refill
        self._last_refill = now
        if last is None:
            return
        elapsed = max((now - last).total_seconds(), 0.0)
        self._bytes = min(float(self._max_bytes), self._bytes + elapsed * self._max_bytes)
        self._records = min(
            float(self._max_records), self._records + elapsed * self._max_records
        )

    def consume(
        self, *, byte_count: int, record_count: int, now: datetime, retry_after_ms: int
    ) -> None:
        """Spend one batch's budget, or raise the typed backpressure refusal."""
        self._refill(now)
        if byte_count > self._bytes:
            raise BackpressureError(
                limit_name="max_bytes_per_second", retry_after_ms=retry_after_ms
            )
        if record_count > self._records:
            raise BackpressureError(
                limit_name="max_records_per_second", retry_after_ms=retry_after_ms
            )
        self._bytes -= byte_count
        self._records -= record_count
