"""The invitation claim route.

Claiming binds to the invited identity, opens the Interview Session through the
kernel, and commits the Job that prepares its preview. Another person is rejected
before any session exists.
"""

from typing import Annotated

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict

from app.auth.dependencies import AuthenticatedIdentity, CurrentDatabase
from app.outreach.claim import claim_and_prepare

__all__ = ["InvitationClaimResult", "router"]

router = APIRouter(prefix="/v1", tags=["participant"])


class InvitationClaimResult(BaseModel):
    """The Interview Session a claim opened."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    product_signal_id: str
    already_claimed: bool


@router.post(
    "/invitations/{invitation_token}/claim", response_model=InvitationClaimResult
)
async def claim(
    identity: AuthenticatedIdentity,
    database: CurrentDatabase,
    invitation_token: Annotated[str, Path(min_length=16, max_length=512)],
) -> InvitationClaimResult:
    """Claim a private invitation and open its Interview Session."""
    result = await claim_and_prepare(
        database,
        token=invitation_token,
        auth_user_id=identity.auth_user_id,
    )
    return InvitationClaimResult(
        session_id=str(result.interview_session_id),
        product_signal_id=str(result.product_signal_id),
        already_claimed=result.already_claimed,
    )
