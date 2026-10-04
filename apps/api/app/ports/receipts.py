"""Provider callback receipt port.

An inbound callback first becomes an authenticated receipt, then a normalized
Event or Result. Signature, account, and environment verification belong to the
provider adapter; the control plane decides the lifecycle consequence.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

__all__ = ["CallbackReceipt", "ReceiptVerifierPort"]


@dataclass(frozen=True, slots=True)
class CallbackReceipt:
    """A verified inbound provider callback."""

    provider: str
    provider_event_id: str
    event_type: str
    body: Mapping[str, object]


class ReceiptVerifierPort(Protocol):
    """Implemented by the provider-adapters workstream."""

    async def verify(
        self, provider: str, raw_body: bytes, headers: Mapping[str, str]
    ) -> CallbackReceipt:
        """Verify one callback's authenticity and return its normalized receipt."""
        ...
