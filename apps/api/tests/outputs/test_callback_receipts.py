"""Callback receipt verification for GitHub and Stripe.

The signatures here are computed the way each provider computes them, with the
same HMAC over the same exact bytes, so the verifier is exercised against real
signing rather than against a stand-in that agrees with it by construction. A
tampered body, a wrong secret, the wrong environment, and the wrong installation
each have to be refused.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from app.api.callbacks_verification import ProviderReceiptVerifier
from app.errors import LaneCredentialError

GITHUB_SECRET = "github-webhook-secret"
STRIPE_SECRET = "whsec_stripe_webhook_secret"
INSTALLATION_ID = "153391499"
TOLERANCE_SECONDS = 300


def _verifier() -> ProviderReceiptVerifier:
    return ProviderReceiptVerifier(
        stripe_webhook_secret=STRIPE_SECRET,
        stripe_expected_account="acct_utrace_sandbox",
        stripe_expects_livemode=False,
        github_webhook_secret=GITHUB_SECRET,
        github_installation_id=INSTALLATION_ID,
        signature_tolerance_seconds=TOLERANCE_SECONDS,
    )


def _github_headers(body: bytes, *, secret: str) -> dict[str, str]:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": f"sha256={digest}",
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "6d1f0a00-0000-0000-0000-000000000000",
    }


def _stripe_headers(body: bytes, *, secret: str, timestamp: int) -> dict[str, str]:
    signed = f"{timestamp}.{body.decode('utf-8')}".encode()
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={timestamp},v1={digest}"}


def _stripe_body(*, livemode: bool, account: str | None) -> bytes:
    payload: dict[str, object] = {
        "id": "evt_1UHj000000000000",
        "object": "event",
        "type": "invoice.finalized",
        "livemode": livemode,
        "data": {"object": {"id": "in_1UHj000000000000"}},
    }
    if account is not None:
        payload["account"] = account
    return json.dumps(payload).encode("utf-8")


async def test_a_signed_github_callback_becomes_a_receipt() -> None:
    body = json.dumps(
        {"action": "opened", "installation": {"id": int(INSTALLATION_ID)}}
    ).encode("utf-8")
    receipt = await _verifier().verify(
        "github", body, _github_headers(body, secret=GITHUB_SECRET)
    )
    assert receipt.provider == "github"
    assert receipt.event_type == "pull_request"
    assert receipt.provider_event_id.startswith("6d1f0a00")


async def test_a_tampered_github_body_is_refused() -> None:
    body = json.dumps({"action": "opened"}).encode("utf-8")
    headers = _github_headers(body, secret=GITHUB_SECRET)
    with pytest.raises(LaneCredentialError) as refusal:
        await _verifier().verify("github", body + b" ", headers)
    assert "signature does not match" in str(refusal.value)


async def test_a_github_callback_for_another_installation_is_refused() -> None:
    body = json.dumps({"action": "opened", "installation": {"id": 1}}).encode("utf-8")
    with pytest.raises(LaneCredentialError) as refusal:
        await _verifier().verify(
            "github", body, _github_headers(body, secret=GITHUB_SECRET)
        )
    assert "not the authorized" in str(refusal.value)


async def test_a_signed_stripe_callback_becomes_a_receipt() -> None:
    body = _stripe_body(livemode=False, account="acct_utrace_sandbox")
    receipt = await _verifier().verify(
        "stripe",
        body,
        _stripe_headers(body, secret=STRIPE_SECRET, timestamp=int(time.time())),
    )
    assert receipt.provider == "stripe"
    assert receipt.event_type == "invoice.finalized"
    assert receipt.provider_event_id == "evt_1UHj000000000000"


async def test_a_stripe_callback_from_the_wrong_environment_is_refused() -> None:
    """A sandbox and a live account are never interchangeable."""
    body = _stripe_body(livemode=True, account="acct_utrace_sandbox")
    with pytest.raises(LaneCredentialError) as refusal:
        await _verifier().verify(
            "stripe",
            body,
            _stripe_headers(body, secret=STRIPE_SECRET, timestamp=int(time.time())),
        )
    assert "environment" in str(refusal.value)


async def test_a_stripe_callback_from_another_account_is_refused() -> None:
    body = _stripe_body(livemode=False, account="acct_someone_else")
    with pytest.raises(LaneCredentialError) as refusal:
        await _verifier().verify(
            "stripe",
            body,
            _stripe_headers(body, secret=STRIPE_SECRET, timestamp=int(time.time())),
        )
    assert "another Stripe account" in str(refusal.value)


async def test_a_stale_stripe_signature_is_refused() -> None:
    body = _stripe_body(livemode=False, account="acct_utrace_sandbox")
    stale = int(time.time()) - (TOLERANCE_SECONDS * 4)
    with pytest.raises(LaneCredentialError) as refusal:
        await _verifier().verify(
            "stripe", body, _stripe_headers(body, secret=STRIPE_SECRET, timestamp=stale)
        )
    assert "signature verification failed" in str(refusal.value)


async def test_an_unknown_provider_is_refused() -> None:
    with pytest.raises(LaneCredentialError):
        await _verifier().verify("discord", b"{}", {})
