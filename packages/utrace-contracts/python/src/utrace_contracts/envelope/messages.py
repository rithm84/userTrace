"""The common envelope and the three message families.

Internal domain communication uses exactly three families. An Event is an
immutable past-tense fact, a Command is one immutable single-purpose request
whose ``command_id`` is its idempotency boundary, and a Result is accepted,
progress, or one canonical terminal outcome for a Command.

The families are separate models rather than one model with a mode flag,
because their required relationships differ: a Command and a Result must carry
the Command identity they are bound to, and a Result's payload is the Result
body rather than an area payload. The envelope is generic in its payload so a
family can state the payload type it carries without widening the base.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from utrace_contracts.base import JsonObject, StrictModel
from utrace_contracts.canonical import payload_digest
from utrace_contracts.envelope.actors import Actor
from utrace_contracts.envelope.compatibility import PayloadType
from utrace_contracts.envelope.failures import ResultBody
from utrace_contracts.envelope.values import (
    AuthorityReference,
    Causation,
    ClientScope,
    PayloadDigest,
    Producer,
    StreamPosition,
    Timestamp,
    TraceContext,
)
from utrace_contracts.errors import DigestMismatchError
from utrace_contracts.ids.models import DomainIds, UuidV7

__all__ = ["Command", "Envelope", "Event", "MessageFamily", "Result"]

MessageFamily = Literal["event", "command", "result"]


class Envelope[PayloadT](StrictModel):
    """The common envelope every uTrace domain message carries.

    ``recorded_at`` and ``stream.sequence`` are assigned by the control plane on
    acceptance, so a producer omits both. Producer timestamps and Realtime
    arrival order never decide workflow order; the sequence does.
    """

    message_id: UuidV7
    envelope_version: Annotated[int, Field(ge=1)]
    family: MessageFamily
    payload_type: PayloadType
    payload_version: Annotated[int, Field(ge=1)]
    producer: Producer
    actor: Actor | None = None
    client_scope: ClientScope
    domain_ids: DomainIds
    authority: Annotated[list[AuthorityReference], Field(max_length=16)] | None = None
    causation: Causation | None = None
    occurred_at: Timestamp
    recorded_at: Timestamp | None = None
    trace_context: TraceContext | None = None
    stream: StreamPosition
    payload: PayloadT
    digest: PayloadDigest

    def payload_json(self) -> JsonObject:
        """The payload in its wire form, which is what the digest is computed over."""
        dumped: dict[str, object] = self.model_dump(mode="json", exclude_none=True)
        payload = dumped["payload"]
        if not isinstance(payload, dict):
            raise TypeError(
                "an envelope payload serializes to a JSON object, received "
                f"{type(payload).__name__} for message_id={self.message_id}"
            )
        return payload

    def verify_digest(self) -> None:
        """Recompute the payload digest and raise if it disagrees with the envelope.

        Raises:
            DigestMismatchError: when the canonical payload does not hash to the
                declared value. The message is not trustworthy and must not be
                applied.
        """
        computed = payload_digest(self.payload_json())
        if computed != self.digest.value:
            raise DigestMismatchError(self.message_id, self.digest.value, computed)


class Event(Envelope[JsonObject]):
    """An immutable past-tense fact. Events deduplicate by ``message_id``."""

    family: Literal["event"]


class Command(Envelope[JsonObject]):
    """One immutable, single-purpose request.

    ``domain_ids.command_id`` is the idempotency boundary and the provider
    idempotency key. A repeated Command replays the canonical terminal Result
    rather than executing again.
    """

    family: Literal["command"]

    @model_validator(mode="after")
    def _carries_its_command_identity(self) -> Self:
        if self.domain_ids.command_id is None:
            raise ValueError(
                "a Command carries domain_ids.command_id: it is the idempotency boundary "
                "and the provider idempotency key"
            )
        return self


class Result(Envelope[ResultBody]):
    """An authenticated typed answer to one Command.

    Only an accepted Result advances product state. A provider acknowledgement,
    Realtime notification, transcript fragment, or model claim never does.
    """

    family: Literal["result"]

    @model_validator(mode="after")
    def _carries_its_command_identity(self) -> Self:
        if self.domain_ids.command_id is None:
            raise ValueError("a Result carries the domain_ids.command_id it answers")
        return self
