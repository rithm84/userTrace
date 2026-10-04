"""The typed failure contract and the Result body.

Four things stay separate on purpose, because conflating them is how a system
starts retrying an effect it already applied or turns an outage into a user
rejection:

- the Command's terminal **disposition**,
- the **effect state**, which is side-effect certainty,
- the closed **technical category** describing the cause,
- the **retry advice**.

The category enumeration is closed. Every member is derived from a failure the
architecture already names, and its docstring says where. An error that maps to
none of them fails closed as ``internal`` with retry ``never`` and creates an
engineering-visible finding rather than silently widening the enumeration.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from utrace_contracts.base import JsonObject, SnakeCaseName, StrictModel

__all__ = [
    "AcceptedResult",
    "CommandDisposition",
    "EffectState",
    "Failure",
    "ProgressResult",
    "ProviderError",
    "ProviderName",
    "ResultBody",
    "RetryAdvice",
    "TechnicalCategory",
    "TerminalResult",
    "Vocabulary",
]

type Vocabulary[EnumT: StrEnum] = Annotated[EnumT, Field(strict=False)]
"""A vocabulary field: the wire carries the string, application code holds the member.

``StrictModel`` refuses coercion, and under strict validation an enum field
accepts only an enum instance -- which the wire never carries. Marking these
fields non-strict accepts the documented string and the member, and nothing
else; the JSON form is unchanged.
"""


class TechnicalCategory(StrEnum):
    """Closed technical cause of a failure.

    ``schemas/envelope/failure.v1.schema.json`` documents what each member
    means and is the source of truth for the set. Product outcomes such as no
    implementation, terminal unvalidated completion, cancellation, and
    Sensitive Account escalation are lifecycle results and never appear here.
    """

    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    POLICY_DENIED = "policy_denied"
    VALIDATION = "validation"
    INCOMPATIBLE_VERSION = "incompatible_version"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    INTEGRITY = "integrity"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_REJECTED = "provider_rejected"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CAPACITY_EXHAUSTED = "capacity_exhausted"
    READINESS_INCOMPLETE = "readiness_incomplete"
    CONFIGURATION = "configuration"
    INTERNAL = "internal"


class RetryAdvice(StrEnum):
    """Whether and when a failed operation may be retried."""

    NEVER = "never"
    AFTER_DELAY = "after_delay"
    AFTER_CONDITION_CHANGE = "after_condition_change"


class CommandDisposition(StrEnum):
    """The single terminal disposition of a Command."""

    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELED = "canceled"
    EXPIRED = "expired"


class EffectState(StrEnum):
    """How certain uTrace is about an external side effect."""

    ABSENT = "absent"
    PARTIAL = "partial"
    COMPLETED = "completed"
    INDETERMINATE = "indeterminate"


ProviderName = Literal[
    "aws",
    "discord",
    "github",
    "openai",
    "opentelemetry",
    "stripe",
    "supabase",
    "typesafe",
    "vercel",
]
"""The active providers in the architecture's provider lookup."""


class ProviderError(StrictModel):
    """The provider's own error identity, used for reconciliation by stable identity."""

    provider: ProviderName
    provider_error_code: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = (
        None
    )
    provider_request_id: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = (
        None
    )


class Failure(StrictModel):
    """One reported failure with redacted, actionable detail.

    ``code`` is the stable area-defined reason inside the category, such as
    ``stale_target`` or ``unsupported_participant_environment``. Codes are
    additive; the category enumeration is not.
    """

    category: Vocabulary[TechnicalCategory]
    code: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=96)]
    message: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    retry: Vocabulary[RetryAdvice]
    retry_after_ms: Annotated[int, Field(ge=0, le=86_400_000)] | None = None
    retry_condition: (
        Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=96)] | None
    ) = None
    provider: ProviderError | None = None
    details: JsonObject | None = None

    @model_validator(mode="after")
    def _retry_advice_is_actionable(self) -> Self:
        """Each advice carries exactly the field a caller needs to act on it."""
        if self.retry is RetryAdvice.AFTER_DELAY and self.retry_after_ms is None:
            raise ValueError("retry advice after_delay requires retry_after_ms")
        if self.retry is not RetryAdvice.AFTER_DELAY and self.retry_after_ms is not None:
            raise ValueError(f"retry advice {self.retry} must not carry retry_after_ms")
        if self.retry is RetryAdvice.AFTER_CONDITION_CHANGE and self.retry_condition is None:
            raise ValueError("retry advice after_condition_change requires retry_condition")
        if (
            self.retry is not RetryAdvice.AFTER_CONDITION_CHANGE
            and self.retry_condition is not None
        ):
            raise ValueError(f"retry advice {self.retry} must not carry retry_condition")
        return self


class AcceptedResult(StrictModel):
    """The target admitted the Command. Acceptance proves no external effect."""

    status: Literal["accepted"]


class ProgressResult(StrictModel):
    """A non-terminal observation about an in-flight Command. It never settles it."""

    status: Literal["progress"]
    stage: SnakeCaseName
    detail: JsonObject | None = None


class TerminalResult(StrictModel):
    """The single canonical terminal outcome for a Command."""

    status: Literal["terminal"]
    disposition: Vocabulary[CommandDisposition]
    effect_state: Vocabulary[EffectState]
    failure: Failure | None = None
    output: JsonObject | None = None

    @model_validator(mode="after")
    def _outcome_is_coherent(self) -> Self:
        """Success carries no failure and a completed effect; rejection precedes any effect."""
        if self.disposition is CommandDisposition.SUCCEEDED:
            if self.failure is not None:
                raise ValueError("a succeeded disposition must not carry a failure")
            if self.effect_state is not EffectState.COMPLETED:
                raise ValueError(
                    "a succeeded disposition requires effect_state completed, "
                    f"received {self.effect_state}"
                )
        elif self.failure is None:
            raise ValueError(f"disposition {self.disposition} requires a typed failure")

        if (
            self.disposition is CommandDisposition.REJECTED
            and self.effect_state is not EffectState.ABSENT
        ):
            raise ValueError(
                "a rejected Command is refused before any side effect, so effect_state "
                f"must be absent, received {self.effect_state}"
            )
        return self


ResultBody = Annotated[
    AcceptedResult | ProgressResult | TerminalResult,
    Field(discriminator="status"),
]
