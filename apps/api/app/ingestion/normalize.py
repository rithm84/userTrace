"""Pure normalization.

Normalization preserves source facts and adds trusted ones beside them. It never
guesses a missing identity, actor, target, route, parentage, or business meaning,
and it never rewrites a source timestamp. Every function here is pure: it reads
its inputs and returns a canonical record.

Contract: docs/arch-components/telemetry-and-evidence.md#validation-and-normalization
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from uuid import UUID

import rfc8785

from app.envelope import JsonValue
from app.ingestion.errors import ClockQualityError, SourceValidationError
from app.ingestion.frames import ObservationEnvelope

__all__ = [
    "CANONICAL_SCHEMA_VERSION",
    "CanonicalObservation",
    "ObservationGap",
    "canonical_observation",
    "content_digest",
    "observation_gap",
]

#: The canonical internal schema every accepted source major decodes into.
CANONICAL_SCHEMA_VERSION = "1"

#: A browser clock more than this far from the receipt clock cannot be used to
#: order records against server spans.
_MAX_CLOCK_SKEW_SECONDS = 900


@dataclass(frozen=True, slots=True)
class CanonicalObservation:
    """One normalized browser observation, ready to persist."""

    observation_id: UUID
    observation_type: str
    runtime_id: UUID
    document_attachment_id: UUID
    frame_id: str
    source_sequence: int
    export_sequence: int
    occurred_at: datetime
    monotonic_offset: float
    route_revision: int
    visibility_state: str
    target_id: str | None
    target_revision: int | None
    trace_id: str | None
    span_id: str | None
    browser_request_id: str | None
    payload: Mapping[str, JsonValue]
    content_sha256: str


@dataclass(frozen=True, slots=True)
class ObservationGap:
    """An explicit dropped range named by its source sequences."""

    gap_runtime_id: UUID
    first_source_sequence: int
    last_source_sequence: int
    record_count: int
    observation_class: str
    reason: str
    baseline_follows: bool


def content_digest(payload: Mapping[str, JsonValue]) -> str:
    """Return the RFC 8785 canonical SHA-256 digest of one payload.

    Canonicalizing first means a producer that re-serializes the same record with
    different key ordering still deduplicates as an exact duplicate instead of
    looking like an identity reused with different content.
    """
    return sha256(rfc8785.dumps(payload)).hexdigest()


def _browser_request_id(
    observation_type: str, payload: Mapping[str, JsonValue]
) -> str | None:
    """Return the paired browser request identity, where the record defines one."""
    if observation_type not in {"request.started", "request.completed"}:
        return None
    value = payload.get("browser_request_id")
    if not isinstance(value, str) or len(value) == 0:
        raise SourceValidationError(
            field=f"{observation_type}.browser_request_id",
            reason="a paired request record must name its browser request",
        )
    return value


def canonical_observation(
    envelope: ObservationEnvelope, *, received_at: datetime
) -> CanonicalObservation:
    """Normalize one observation envelope into its canonical record.

    The source time is kept exactly as the runtime reported it. The receipt time
    is recorded beside it, and an implausible distance between the two is a typed
    clock-quality failure rather than a silent adjustment.
    """
    skew = abs((received_at - envelope.occurred_at).total_seconds())
    if skew > _MAX_CLOCK_SKEW_SECONDS:
        raise ClockQualityError(
            measurement="browser_wall_clock_offset",
            reason=(
                f"the record's occurred_at is {int(skew)} s from the receipt clock, "
                f"beyond the {_MAX_CLOCK_SKEW_SECONDS} s uncertainty bound"
            ),
        )
    payload: Mapping[str, JsonValue] = _as_json_payload(envelope.payload)
    return CanonicalObservation(
        observation_id=envelope.observation_id,
        observation_type=envelope.observation_type,
        runtime_id=envelope.runtime_id,
        document_attachment_id=envelope.document_attachment_id,
        frame_id=envelope.frame_id,
        source_sequence=envelope.source_sequence,
        export_sequence=envelope.export_sequence,
        occurred_at=envelope.occurred_at,
        monotonic_offset=envelope.monotonic_offset,
        route_revision=envelope.route_revision,
        visibility_state=envelope.visibility_state,
        target_id=envelope.target_id,
        target_revision=envelope.target_revision,
        trace_id=envelope.trace_id,
        span_id=envelope.span_id,
        browser_request_id=_browser_request_id(envelope.observation_type, payload),
        payload=payload,
        content_sha256=content_digest(payload),
    )


def observation_gap(
    payload: Mapping[str, JsonValue],
) -> ObservationGap:
    """Read one gap record, which names a dropped ``source_sequence`` range."""
    try:
        runtime = UUID(str(payload["gap_runtime_id"]))
        first = int(str(payload["gap_first_source_sequence"]))
        last = int(str(payload["gap_last_source_sequence"]))
        count = int(str(payload["count"]))
        observation_class = str(payload["observation_class"])
        reason = str(payload["reason"])
        baseline_follows = bool(payload["baseline_follows"])
    except (KeyError, ValueError) as error:
        raise SourceValidationError(
            field="observation.gap", reason=f"is incomplete: {error}"
        ) from None
    if last < first:
        raise SourceValidationError(
            field="observation.gap", reason="the dropped range ends before it starts"
        )
    return ObservationGap(
        gap_runtime_id=runtime,
        first_source_sequence=first,
        last_source_sequence=last,
        record_count=count,
        observation_class=observation_class,
        reason=reason,
        baseline_follows=baseline_follows,
    )


def contiguous_through(accepted_through: int, arrived: frozenset[int]) -> int:
    """Return the highest contiguous sequence after adding the arrived sequences.

    A missing or rejected sequence leaves the later records durable but does not
    pretend the stream is continuous across the hole.
    """
    watermark = accepted_through
    while watermark + 1 in arrived:
        watermark += 1
    return watermark


def _as_json_payload(payload: Mapping[str, object]) -> Mapping[str, JsonValue]:
    """Narrow a decoded payload to the JSON value type without copying meaning."""
    narrowed: dict[str, JsonValue] = {}
    for key, value in payload.items():
        narrowed[key] = _narrow(value, key)
    return narrowed


def _narrow(value: object, location: str) -> JsonValue:
    """Narrow one decoded JSON value, refusing anything JSON cannot carry."""
    match value:
        case None | bool() | int() | float() | str():
            return value
        case dict():
            return {str(key): _narrow(item, f"{location}.{key}") for key, item in value.items()}
        case list():
            return [_narrow(item, f"{location}[{index}]") for index, item in enumerate(value)]
        case _:
            raise SourceValidationError(
                field=location, reason=f"holds a {type(value).__name__}, which is not JSON"
            )
