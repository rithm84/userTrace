"""Receipt verification for the GitHub and Stripe callbacks.

Implements the control plane's ``ReceiptVerifierPort``. An inbound callback
becomes an authenticated receipt only after its signature, account, and
environment check out; the control plane then deduplicates it and decides the
lifecycle consequence. A callback never carries authority of its own, and a late
or replayed one never causes a second effect.

Stripe verification uses the SDK's own ``construct_event``, which checks the
``Stripe-Signature`` scheme, the HMAC, and the timestamp tolerance together.
GitHub verification is the documented ``X-Hub-Signature-256`` HMAC over the exact
raw body, compared in constant time.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Final

import stripe

from app.errors import LaneCredentialError
from app.logging import get_logger
from app.ports.receipts import CallbackReceipt

__all__ = ["ProviderReceiptVerifier"]

_log = get_logger(__name__)

_GITHUB_SIGNATURE_HEADER: Final[str] = "x-hub-signature-256"
_GITHUB_EVENT_HEADER: Final[str] = "x-github-event"
_GITHUB_DELIVERY_HEADER: Final[str] = "x-github-delivery"
_STRIPE_SIGNATURE_HEADER: Final[str] = "stripe-signature"
_SIGNATURE_PREFIX: Final[str] = "sha256="


class ProviderReceiptVerifier:
    """Verifies one client's GitHub and Stripe callbacks.

    Every value is explicit configuration: the two signing secrets, the Stripe
    account and environment the receipts must belong to, and the one GitHub App
    installation uTrace writes through. A receipt from another account,
    environment, or installation is refused rather than recorded.
    """

    def __init__(
        self,
        *,
        stripe_webhook_secret: str,
        stripe_expected_account: str | None,
        stripe_expects_livemode: bool,
        github_webhook_secret: str,
        github_installation_id: str,
        signature_tolerance_seconds: int,
    ) -> None:
        self._stripe_secret = stripe_webhook_secret
        self._stripe_account = stripe_expected_account
        self._stripe_livemode = stripe_expects_livemode
        self._github_secret = github_webhook_secret
        self._github_installation_id = github_installation_id
        self._tolerance = signature_tolerance_seconds

    async def verify(
        self, provider: str, raw_body: bytes, headers: Mapping[str, str]
    ) -> CallbackReceipt:
        """Verify one callback's authenticity and return its normalized receipt."""
        lowered = {name.lower(): value for name, value in headers.items()}
        if provider == "stripe":
            return self._verify_stripe(raw_body, lowered)
        if provider == "github":
            return self._verify_github(raw_body, lowered)
        raise LaneCredentialError(
            audience=f"callback:{provider}",
            reason="no receipt verifier is configured for that provider",
        )

    def _verify_stripe(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> CallbackReceipt:
        signature = headers.get(_STRIPE_SIGNATURE_HEADER)
        if not signature:
            raise LaneCredentialError(
                audience="callback:stripe", reason="the Stripe-Signature header is missing"
            )
        try:
            event = stripe.Webhook.construct_event(
                raw_body.decode("utf-8"),
                signature,
                self._stripe_secret,
                tolerance=self._tolerance,
            )
        except (ValueError, stripe.SignatureVerificationError) as error:
            raise LaneCredentialError(
                audience="callback:stripe",
                reason=f"signature verification failed: {type(error).__name__}",
            ) from error

        if bool(event.livemode) is not self._stripe_livemode:
            raise LaneCredentialError(
                audience="callback:stripe",
                reason=(
                    "the callback's environment does not match the configured one; "
                    "a sandbox and a live account are never interchangeable"
                ),
            )
        account = getattr(event, "account", None)
        if self._stripe_account is not None and account != self._stripe_account:
            raise LaneCredentialError(
                audience="callback:stripe",
                reason="the callback belongs to another Stripe account",
            )
        _log.info(
            "callback_receipt_verified",
            provider="stripe",
            event_type=event.type,
            provider_event_id=event.id,
            livemode=bool(event.livemode),
        )
        return CallbackReceipt(
            provider="stripe",
            provider_event_id=str(event.id),
            event_type=str(event.type),
            body=_decode(raw_body),
        )

    def _verify_github(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> CallbackReceipt:
        signature = headers.get(_GITHUB_SIGNATURE_HEADER)
        if not signature or not signature.startswith(_SIGNATURE_PREFIX):
            raise LaneCredentialError(
                audience="callback:github",
                reason="the X-Hub-Signature-256 header is missing or malformed",
            )
        expected = hmac.new(
            self._github_secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(
            expected, signature.removeprefix(_SIGNATURE_PREFIX)
        ):
            raise LaneCredentialError(
                audience="callback:github", reason="the payload signature does not match"
            )
        body = _decode(raw_body)
        installation = body.get("installation")
        if isinstance(installation, Mapping):
            delivered_to = str(installation.get("id"))
            if delivered_to != self._github_installation_id:
                raise LaneCredentialError(
                    audience="callback:github",
                    reason=(
                        f"the callback names installation {delivered_to}, not the "
                        f"authorized {self._github_installation_id}"
                    ),
                )
        event_type = headers.get(_GITHUB_EVENT_HEADER)
        delivery = headers.get(_GITHUB_DELIVERY_HEADER)
        if not event_type or not delivery:
            raise LaneCredentialError(
                audience="callback:github",
                reason="the callback carries no event type or delivery identity",
            )
        _log.info(
            "callback_receipt_verified",
            provider="github",
            event_type=event_type,
            provider_event_id=delivery,
        )
        return CallbackReceipt(
            provider="github",
            provider_event_id=delivery,
            event_type=event_type,
            body=body,
        )


def _decode(raw_body: bytes) -> Mapping[str, object]:
    """Parse the callback body, refusing anything but a JSON object."""
    try:
        parsed = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LaneCredentialError(
            audience="callback:body", reason="the callback body is not JSON"
        ) from error
    if not isinstance(parsed, dict):
        raise LaneCredentialError(
            audience="callback:body", reason="the callback body is not a JSON object"
        )
    return parsed
