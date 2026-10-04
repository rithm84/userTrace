"""Typed provider failures shared by every connector in this package.

Every connector maps a provider error onto one `ConnectorFailure`, which carries a
closed technical category, retry advice, and the observable effect state described by
the [failure contract](../../../../docs/arch-components/cross-component-contracts.md#failure-contract).
An unmapped provider error fails closed: category `internal`, retry advice `never`,
effect state `indeterminate`.

The three enumerations are the canonical ones from `utrace_contracts`; this module
adds only what the contract leaves to the reporting component: the default advice each
category carries, the HTTP status mapping, and redaction.

Failure detail is redacted actionable detail, never a credential. `redacted_parameters`
is the only supported way to attach request parameters to a failure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from utrace_contracts.domain.lifecycle import EffectState, RetryAdvice, TechnicalCategory

__all__ = [
    "SECRET_PARAMETER_NAMES",
    "ConnectorFailure",
    "EffectState",
    "FailureDetail",
    "RetryAdvice",
    "TechnicalCategory",
    "default_retry_advice",
    "redacted_parameters",
    "technical_category_for_status",
]


_DEFAULT_RETRY_ADVICE: Final[Mapping[TechnicalCategory, RetryAdvice]] = {
    TechnicalCategory.AUTHENTICATION: RetryAdvice.AFTER_CONDITION_CHANGE,
    TechnicalCategory.AUTHORIZATION: RetryAdvice.AFTER_CONDITION_CHANGE,
    TechnicalCategory.POLICY_DENIED: RetryAdvice.NEVER,
    TechnicalCategory.VALIDATION: RetryAdvice.NEVER,
    TechnicalCategory.INCOMPATIBLE_VERSION: RetryAdvice.AFTER_CONDITION_CHANGE,
    TechnicalCategory.NOT_FOUND: RetryAdvice.AFTER_CONDITION_CHANGE,
    TechnicalCategory.CONFLICT: RetryAdvice.AFTER_CONDITION_CHANGE,
    TechnicalCategory.INTEGRITY: RetryAdvice.NEVER,
    TechnicalCategory.TIMEOUT: RetryAdvice.AFTER_DELAY,
    TechnicalCategory.RATE_LIMITED: RetryAdvice.AFTER_DELAY,
    TechnicalCategory.PAYLOAD_TOO_LARGE: RetryAdvice.NEVER,
    TechnicalCategory.PROVIDER_UNAVAILABLE: RetryAdvice.AFTER_DELAY,
    TechnicalCategory.PROVIDER_REJECTED: RetryAdvice.NEVER,
    TechnicalCategory.BUDGET_EXHAUSTED: RetryAdvice.NEVER,
    TechnicalCategory.CAPACITY_EXHAUSTED: RetryAdvice.AFTER_DELAY,
    TechnicalCategory.READINESS_INCOMPLETE: RetryAdvice.AFTER_CONDITION_CHANGE,
    TechnicalCategory.CONFIGURATION: RetryAdvice.AFTER_CONDITION_CHANGE,
    TechnicalCategory.INTERNAL: RetryAdvice.NEVER,
}

SECRET_PARAMETER_NAMES: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bot_token",
        "client_secret",
        "credential",
        "jwt",
        "key",
        "password",
        "private_key",
        "project_key",
        "sdp",
        "secret",
        "session_description",
        "token",
    }
)
"""Parameter names whose values never reach a failure, a log, or a Case File.

`sdp` and `session_description` are included because the voice contract excludes session
descriptions and transport credentials from logs and model context.
"""

_REDACTED: Final[str] = "[redacted]"
_MAX_RESPONSE_BODY_CHARS: Final[int] = 2000


def default_retry_advice(category: TechnicalCategory) -> RetryAdvice:
    """Return the advice a category carries unless the connector knows better.

    A connector overrides this only with provider-specific proof, for example a
    rate-limit response that names a retry-after window.
    """
    return _DEFAULT_RETRY_ADVICE[category]


def technical_category_for_status(status_code: int) -> TechnicalCategory:
    """Map an HTTP status onto a technical category.

    Providers that expose richer error codes map those first and use this only as the
    fallback. Any status outside the mapped ranges is `INTERNAL`, which fails closed.
    """
    if status_code == 400:
        return TechnicalCategory.PROVIDER_REJECTED
    if status_code == 401:
        return TechnicalCategory.AUTHENTICATION
    if status_code in (402, 403):
        return TechnicalCategory.AUTHORIZATION
    if status_code == 404:
        return TechnicalCategory.NOT_FOUND
    if status_code in (405, 406, 410, 411, 414, 415, 422):
        return TechnicalCategory.PROVIDER_REJECTED
    if status_code == 413:
        return TechnicalCategory.PAYLOAD_TOO_LARGE
    if status_code in (408, 504):
        return TechnicalCategory.TIMEOUT
    if status_code in (409, 412, 428):
        return TechnicalCategory.CONFLICT
    if status_code == 429:
        return TechnicalCategory.RATE_LIMITED
    if status_code in (500, 502, 503, 529):
        return TechnicalCategory.PROVIDER_UNAVAILABLE
    return TechnicalCategory.INTERNAL


def redacted_parameters(parameters: Mapping[str, object]) -> dict[str, str]:
    """Render request parameters for a failure, replacing secret-named values.

    Values are stringified so a failure never holds a live provider object, and any key
    whose lowercase name is in `SECRET_PARAMETER_NAMES` or ends with one of `_token`,
    `_key`, or `_secret` is replaced.
    """
    rendered: dict[str, str] = {}
    for name, value in parameters.items():
        lowered = name.lower()
        is_secret = lowered in SECRET_PARAMETER_NAMES or lowered.endswith(
            ("_token", "_key", "_secret")
        )
        rendered[name] = _REDACTED if is_secret else str(value)
    return rendered


@dataclass(frozen=True, slots=True)
class FailureDetail:
    """The redacted, actionable description of one failed provider call."""

    provider: str
    """Stable provider identifier, for example `openai` or `vercel`."""
    operation: str
    """Connector operation name, for example `create_live_session`."""
    category: TechnicalCategory
    retry_advice: RetryAdvice
    effect_state: EffectState
    message: str
    """What failed and why, in the connector's own words."""
    request_parameters: Mapping[str, str] = field(default_factory=dict)
    """Already passed through `redacted_parameters`."""
    status_code: int | None = None
    provider_error_code: str | None = None
    response_body: str | None = None
    """Truncated provider body. Callers pass `None` when the body may hold secrets."""


class ConnectorFailure(Exception):
    """The single exception type every connector in this package raises.

    Callers read `detail` rather than parsing the message. The message is built from the
    detail so an uncaught failure still names the provider, operation, and category.
    """

    def __init__(self, detail: FailureDetail) -> None:
        self.detail = detail
        parts = [
            f"{detail.provider}.{detail.operation} failed",
            f"category={detail.category.value}",
            f"retry={detail.retry_advice.value}",
            f"effect={detail.effect_state.value}",
        ]
        if detail.status_code is not None:
            parts.append(f"status={detail.status_code}")
        if detail.provider_error_code is not None:
            parts.append(f"provider_code={detail.provider_error_code}")
        if detail.request_parameters:
            rendered = ", ".join(f"{k}={v}" for k, v in sorted(detail.request_parameters.items()))
            parts.append(f"params=({rendered})")
        if detail.response_body is not None:
            body = detail.response_body[:_MAX_RESPONSE_BODY_CHARS]
            parts.append(f"body={body}")
        super().__init__(f"{detail.message} [{'; '.join(parts)}]")

    @property
    def category(self) -> TechnicalCategory:
        """Shorthand used by control-plane branching and by the tests."""
        return self.detail.category

    @property
    def retry_advice(self) -> RetryAdvice:
        """Shorthand for the advice the control plane must honour."""
        return self.detail.retry_advice

    @property
    def effect_state(self) -> EffectState:
        """Shorthand for what the connector could prove about side effects."""
        return self.detail.effect_state
