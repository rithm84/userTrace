"""Provider callback receipts.

An inbound callback first becomes an authenticated receipt, then a normalized
Event or Result. Verification is the provider adapter's responsibility; the
control plane deduplicates the receipt and decides the lifecycle consequence.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict

from app.auth.dependencies import CurrentDatabase
from app.execution.commands import record_inbound
from app.ids import uuid7
from app.ports import PortRegistry

__all__ = ["router"]

router = APIRouter(prefix="/v1/callbacks", tags=["callbacks"])


class ReceiptAccepted(BaseModel):
    """What the control plane recorded for one inbound callback."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: UUID
    provider: str
    provider_event_id: str
    deduplicated: bool


def _ports(request: Request) -> PortRegistry:
    registry = request.app.state.ports
    if not isinstance(registry, PortRegistry):
        raise TypeError("the application was assembled without a port registry")
    return registry


async def _accept(
    request: Request, database: CurrentDatabase, provider: str, client_id: UUID
) -> ReceiptAccepted:
    verifier = _ports(request).require_receipts()
    raw = await request.body()
    receipt = await verifier.verify(provider, raw, dict(request.headers))
    message_id = uuid7()
    fresh = await record_inbound(
        database,
        message_id=message_id,
        client_id=client_id,
        source=f"{provider}:{receipt.event_type}",
        message_kind="provider_receipt",
        command_id=None,
        payload={"provider_event_id": receipt.provider_event_id},
    )
    return ReceiptAccepted(
        message_id=message_id,
        provider=provider,
        provider_event_id=receipt.provider_event_id,
        deduplicated=not fresh,
    )


@router.post("/stripe", response_model=ReceiptAccepted, response_model_exclude_none=True)
async def stripe_callback(
    request: Request,
    database: CurrentDatabase,
    x_utrace_client_id: Annotated[UUID, Header()],
) -> ReceiptAccepted:
    """Record one verified Stripe callback receipt."""
    return await _accept(request, database, "stripe", x_utrace_client_id)


@router.post("/github", response_model=ReceiptAccepted, response_model_exclude_none=True)
async def github_callback(
    request: Request,
    database: CurrentDatabase,
    x_utrace_client_id: Annotated[UUID, Header()],
) -> ReceiptAccepted:
    """Record one verified GitHub callback receipt."""
    return await _accept(request, database, "github", x_utrace_client_id)
