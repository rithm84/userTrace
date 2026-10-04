"""Pure tests for the connector failure contract.

These cover the rules the control plane depends on: an unmapped provider error fails
closed, retry advice follows the category rather than the call site's mood, secrets never
reach a failure, and a failure message names enough to act on.
"""

from __future__ import annotations

import pytest

from app.connectors.failures import (
    ConnectorFailure,
    EffectState,
    FailureDetail,
    RetryAdvice,
    TechnicalCategory,
    default_retry_advice,
    redacted_parameters,
    technical_category_for_status,
)


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (400, TechnicalCategory.PROVIDER_REJECTED),
        (401, TechnicalCategory.AUTHENTICATION),
        (403, TechnicalCategory.AUTHORIZATION),
        (404, TechnicalCategory.NOT_FOUND),
        (408, TechnicalCategory.TIMEOUT),
        (409, TechnicalCategory.CONFLICT),
        (413, TechnicalCategory.PAYLOAD_TOO_LARGE),
        (422, TechnicalCategory.PROVIDER_REJECTED),
        (429, TechnicalCategory.RATE_LIMITED),
        (500, TechnicalCategory.PROVIDER_UNAVAILABLE),
        (503, TechnicalCategory.PROVIDER_UNAVAILABLE),
        (529, TechnicalCategory.PROVIDER_UNAVAILABLE),
    ],
)
def test_status_codes_map_to_their_category(
    status_code: int, expected: TechnicalCategory
) -> None:
    assert technical_category_for_status(status_code) is expected


@pytest.mark.parametrize("status_code", [100, 204, 300, 451, 599, 0])
def test_unmapped_statuses_fail_closed(status_code: int) -> None:
    category = technical_category_for_status(status_code)
    assert category is TechnicalCategory.INTERNAL
    assert default_retry_advice(category) is RetryAdvice.NEVER


def test_every_category_has_retry_advice() -> None:
    """A new category without advice would be a silent hole in the failure contract."""
    for category in TechnicalCategory:
        assert isinstance(default_retry_advice(category), RetryAdvice)


@pytest.mark.parametrize(
    "category",
    [
        TechnicalCategory.RATE_LIMITED,
        TechnicalCategory.PROVIDER_UNAVAILABLE,
        TechnicalCategory.TIMEOUT,
        TechnicalCategory.CAPACITY_EXHAUSTED,
    ],
)
def test_transient_categories_advise_a_delay(category: TechnicalCategory) -> None:
    assert default_retry_advice(category) is RetryAdvice.AFTER_DELAY


@pytest.mark.parametrize(
    "category",
    [
        TechnicalCategory.PROVIDER_REJECTED,
        TechnicalCategory.VALIDATION,
        TechnicalCategory.POLICY_DENIED,
        TechnicalCategory.BUDGET_EXHAUSTED,
        TechnicalCategory.INTERNAL,
    ],
)
def test_permanent_categories_never_advise_a_retry(category: TechnicalCategory) -> None:
    assert default_retry_advice(category) is RetryAdvice.NEVER


@pytest.mark.parametrize(
    "name",
    [
        "api_key",
        "API_KEY",
        "bot_token",
        "private_key",
        "sdp",
        "session_description",
        "client_secret",
        "installation_token",
        "project_key",
        "webhook_secret",
    ],
)
def test_secret_named_parameters_are_redacted(name: str) -> None:
    rendered = redacted_parameters({name: "super-secret-value"})
    assert rendered[name] == "[redacted]"
    assert "super-secret-value" not in rendered[name]


def test_non_secret_parameters_survive_as_strings() -> None:
    rendered = redacted_parameters({"channel_id": 12345, "port": 3000, "draft": True})
    assert rendered == {"channel_id": "12345", "port": "3000", "draft": "True"}


def test_failure_message_names_provider_operation_and_disposition() -> None:
    failure = ConnectorFailure(
        FailureDetail(
            provider="vercel",
            operation="create_from_snapshot",
            category=TechnicalCategory.CONFLICT,
            retry_advice=RetryAdvice.NEVER,
            effect_state=EffectState.ABSENT,
            message="stale fencing token",
            request_parameters=redacted_parameters(
                {"attempt_id": "att_1", "api_key": "sk-live"}
            ),
            status_code=409,
            provider_error_code="conflict",
            response_body="{}",
        )
    )
    text = str(failure)
    assert "vercel.create_from_snapshot failed" in text
    assert "category=conflict" in text
    assert "retry=never" in text
    assert "effect=absent" in text
    assert "status=409" in text
    assert "attempt_id=att_1" in text
    assert "sk-live" not in text
    assert failure.category is TechnicalCategory.CONFLICT
    assert failure.retry_advice is RetryAdvice.NEVER
    assert failure.effect_state is EffectState.ABSENT


def test_failure_detail_defaults_to_no_parameters_or_body() -> None:
    detail = FailureDetail(
        provider="vercel",
        operation="read_file",
        category=TechnicalCategory.NOT_FOUND,
        retry_advice=RetryAdvice.AFTER_CONDITION_CHANGE,
        effect_state=EffectState.ABSENT,
        message="file missing",
    )
    assert detail.request_parameters == {}
    assert detail.response_body is None
    assert detail.status_code is None
