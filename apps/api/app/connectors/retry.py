"""Bounded retry for provider calls that are proven safe to repeat.

The [failure contract](../../../../docs/arch-components/cross-component-contracts.md#failure-contract)
permits a provider adapter to retry only when idempotency, reconciliation, or proof of no
side effect makes it safe. `retry_safe_call` therefore takes the caller's explicit
justification and refuses to retry a failure whose effect state is not `ABSENT`: a call
that may have half-applied is reconciled by its owner, never repeated blindly.

Every attempt after the first logs a structured warning, and the last failure is raised
unchanged so the caller sees the real provider detail rather than a wrapper.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from app.connectors.failures import ConnectorFailure, EffectState, RetryAdvice

__all__ = ["RetryPolicy", "retry_safe_call"]

_logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How many times a proven-safe call may be repeated, and how long to wait.

    `safety_justification` records why repeating this specific call cannot duplicate an
    effect, for example "read-only" or "provider idempotency key". It is logged with every
    retry so an operator can audit the claim.
    """

    max_attempts: int
    initial_delay_seconds: float
    backoff_multiplier: float
    safety_justification: str


async def retry_safe_call[ResultT](
    *,
    operation: str,
    policy: RetryPolicy,
    call: Callable[[], Awaitable[ResultT]],
) -> ResultT:
    """Run `call`, retrying only failures that advise a delay and left no effect.

    Raises the last `ConnectorFailure` once attempts are exhausted, or immediately for any
    failure whose advice is not `AFTER_DELAY` or whose effect state is not `ABSENT`.
    """
    if policy.max_attempts < 1:
        raise ValueError(f"max_attempts must be at least 1, got {policy.max_attempts}")

    delay = policy.initial_delay_seconds
    attempt = 1
    while True:
        try:
            return await call()
        except ConnectorFailure as failure:
            retryable = (
                failure.retry_advice is RetryAdvice.AFTER_DELAY
                and failure.effect_state is EffectState.ABSENT
            )
            if not retryable or attempt >= policy.max_attempts:
                raise
            _logger.warning(
                "connector.retry",
                operation=operation,
                provider=failure.detail.provider,
                attempt=attempt,
                max_attempts=policy.max_attempts,
                delay_seconds=delay,
                category=failure.category.value,
                status_code=failure.detail.status_code,
                safety_justification=policy.safety_justification,
            )
            await asyncio.sleep(delay)
            delay *= policy.backoff_multiplier
            attempt += 1
