"""The one-time bootstrap credential the prepared preview injects.

The preview stream mints this at sandbox creation and injects it as
``UTRACE_SERVER_BOOTSTRAP_CREDENTIAL``. Its presence is the single signal that
takes the packaged SDK out of dormancy, so it is never stored in the client
repository or in client configuration.

It is a signed, short-lived lane credential on the ``server_sdk`` lane, bound to
the exact preview runtime allocation and its public preview origin. Signing it
rather than storing it means the control plane can mint it before any activation
row exists, and revocation stays durable: the lane check refuses the credential
once the bound Interview Session closes.

The credential authenticates the control *socket*, not the activation. A dropped
control connection reconnects with the same credential and rotates; ``activate``
is the one-time exchange, and the unique index on ``server_stream_id`` is what
makes it one-time.

Contract: docs/arch-components/server-sdk.md#configuration-identity-and-credentials
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.auth.lane import (
    Lane,
    check_lane_revocation,
    mint_lane_credential,
    verify_lane_credential,
)
from app.db.pool import Database
from app.errors import LaneCredentialError
from app.ingestion.errors import TelemetryAuthenticationError
from app.logging import get_logger

__all__ = [
    "BOOTSTRAP_CREDENTIAL_LIFETIME_SECONDS",
    "ServerBootstrapCredential",
    "ServerBootstrapScope",
    "mint_server_bootstrap_credential",
    "verify_server_bootstrap_credential",
]

_log = get_logger(__name__)

#: A prepared runtime activates within its readiness window, and the SDK
#: reconnects with the same credential inside the case's live session. Twelve
#: hours covers a long interview and still expires well inside a day.
BOOTSTRAP_CREDENTIAL_LIFETIME_SECONDS = 43_200


@dataclass(frozen=True, slots=True)
class ServerBootstrapCredential:
    """A minted bootstrap credential and when it stops opening sockets."""

    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ServerBootstrapScope:
    """The allocation one presented bootstrap credential resolves to."""

    client_id: UUID
    interview_session_id: UUID
    preview_runtime_allocation_id: UUID
    public_preview_host: str


def mint_server_bootstrap_credential(
    *,
    signing_key: str,
    client_id: UUID,
    interview_session_id: UUID,
    preview_runtime_allocation_id: UUID,
    public_preview_host: str,
) -> ServerBootstrapCredential:
    """Mint the credential the preview stream injects into one sandbox.

    The allocation is the subject and the public preview origin is the binding,
    so a credential minted for one runtime cannot activate a service behind
    another origin even within the same client.
    """
    credential = mint_lane_credential(
        signing_key=signing_key,
        lane=Lane.SERVER_SDK,
        subject_id=preview_runtime_allocation_id,
        client_id=client_id,
        interview_session_id=interview_session_id,
        binding=public_preview_host,
        lifetime_seconds=BOOTSTRAP_CREDENTIAL_LIFETIME_SECONDS,
    )
    _log.info(
        "server_bootstrap_credential_minted",
        client_id=str(client_id),
        interview_session_id=str(interview_session_id),
        preview_runtime_allocation_id=str(preview_runtime_allocation_id),
        expires_at=credential.expires_at.isoformat(),
    )
    return ServerBootstrapCredential(
        token=credential.token, expires_at=credential.expires_at
    )


async def verify_server_bootstrap_credential(
    database: Database, *, signing_key: str, token: str
) -> ServerBootstrapScope:
    """Resolve a presented bootstrap credential, or refuse with the exact reason.

    Expiry, the lane audience, and the durable session revocation are all checked
    here. A credential that carries no Interview Session or no origin binding is
    refused rather than resolved against whatever the allocation happens to say.
    """
    try:
        claims = verify_lane_credential(
            signing_key=signing_key, token=token, lane=Lane.SERVER_SDK
        )
    except LaneCredentialError as error:
        raise TelemetryAuthenticationError(
            "the presented bootstrap credential is not a valid server SDK credential"
        ) from error
    if claims.interview_session_id is None or claims.binding is None:
        raise TelemetryAuthenticationError(
            "the bootstrap credential carries no Interview Session or preview origin"
        )
    try:
        await check_lane_revocation(database, claims)
    except LaneCredentialError as error:
        raise TelemetryAuthenticationError(
            "the Interview Session bound to that bootstrap credential is closed"
        ) from error
    return ServerBootstrapScope(
        client_id=claims.client_id,
        interview_session_id=claims.interview_session_id,
        preview_runtime_allocation_id=claims.subject_id,
        public_preview_host=claims.binding,
    )
