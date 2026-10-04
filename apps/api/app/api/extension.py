"""Browser extension routes.

Three surfaces: the account-link code the signed-in web page mints, the
installation registry the extension posts its device public key to, and the
authenticated control socket the service worker owns.

Registration is not authenticated by a bearer credential. It is authenticated by
a single-use link code, which only a signed-in person can obtain, together with a
proof of possession of the installation's non-exportable device key. That is the
whole point of the device key: the extension never holds a reusable credential.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Response, WebSocket
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import AuthenticatedIdentity, CurrentDatabase
from app.db.pool import Database
from app.db.rows import as_uuid
from app.errors import AuthorizationError
from app.extension.bindings import PageProofVerifier
from app.extension.installations import (
    InstallationRegistrationRequest,
    ProofOfPossession,
    mint_link_code,
    register_installation,
    revoke_installation,
    validate_extension_redirect_uri,
)
from app.extension.socket import ExtensionControlSocket, SocketDependencies
from app.ingestion.control_hub import require_control_hub
from app.ingestion.wiring import require_signer
from app.interview.settings import get_interview_settings
from app.logging import get_logger
from app.ports import PortRegistry

__all__ = ["router"]

_log = get_logger(__name__)

router = APIRouter(prefix="/v1/extension", tags=["extension"])

CONTROL_SOCKET_PATH = "/v1/extension/control"
RETURN_WINDOW_SECONDS = 72 * 60 * 60


class ApiModel(BaseModel):
    """Base for every model on this router: closed to extra fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class LinkCodeRequest(ApiModel):
    """What the account-link page reports before it redirects."""

    redirect_uri: str = Field(min_length=1, max_length=512)
    extension_version: str = Field(pattern=r"^[0-9]+(\.[0-9]+){0,3}$")
    protocol_version: str = Field(pattern=r"^1\.[0-9]{1,3}$")


class LinkCodeResult(ApiModel):
    """The single-use code the redirect carries back to the extension."""

    link_code: str
    expires_at: str
    redirect_uri: str


class InstallationRegistrationResult(ApiModel):
    """`installation-registration.v1` response."""

    schema_version: int
    kind: str
    installation_id: UUID
    account_link_state: str
    negotiated_protocol_version: str
    control_socket_path: str
    registered_at: str


class InstallationRevocationRequest(ApiModel):
    """A revocation proves possession of the same device key it revokes."""

    schema_version: int
    proof_of_possession: ProofOfPossession


@router.post("/link-codes", response_model=LinkCodeResult)
async def create_link_code(
    identity: AuthenticatedIdentity,
    database: CurrentDatabase,
    body: LinkCodeRequest,
) -> LinkCodeResult:
    """Mint the one-time account-link code for the signed-in person.

    The uTrace web origin serves `GET /extension/link`, reads the extension's
    `redirect_uri`, calls this route as the signed-in person, and redirects to
    the `chromiumapp.org` URI with `link_code` in the query. The extension never
    sees an account credential.
    """
    interview = get_interview_settings()
    utrace_user_id = await _utrace_user(database, auth_user_id=identity.auth_user_id)
    redirect_uri = validate_extension_redirect_uri(body.redirect_uri)
    code = await mint_link_code(
        database,
        utrace_user_id=utrace_user_id,
        redirect_uri=redirect_uri,
        extension_version=body.extension_version,
        protocol_version=body.protocol_version,
        lifetime_seconds=interview.extension_link_code_lifetime_seconds,
    )
    return LinkCodeResult(
        link_code=code.code,
        expires_at=code.expires_at.isoformat().replace("+00:00", "Z"),
        redirect_uri=redirect_uri,
    )


@router.post("/installations", response_model=InstallationRegistrationResult)
async def register(
    database: CurrentDatabase, body: InstallationRegistrationRequest
) -> InstallationRegistrationResult:
    """Register one installation against a link code and a possession proof."""
    interview = get_interview_settings()
    record = await register_installation(
        database,
        request=body,
        audience=interview.extension_api_audience,
        supported_protocol_versions=_supported_protocol_versions(),
        possession_max_age_seconds=interview.possession_proof_lifetime_seconds,
    )
    return InstallationRegistrationResult(
        schema_version=1,
        kind="installation_registration_response",
        installation_id=record.installation_id,
        account_link_state=record.account_link_state,
        negotiated_protocol_version=record.negotiated_protocol_version,
        control_socket_path=CONTROL_SOCKET_PATH,
        registered_at=record.registered_at.isoformat().replace("+00:00", "Z"),
    )


@router.delete("/installations/{installation_id}", status_code=204)
async def revoke(
    database: CurrentDatabase,
    installation_id: Annotated[UUID, Path()],
    body: InstallationRevocationRequest,
) -> Response:
    """Revoke one installation and every binding it currently holds."""
    interview = get_interview_settings()
    await revoke_installation(
        database,
        installation_id=installation_id,
        proof=body.proof_of_possession,
        audience=interview.extension_api_audience,
        possession_max_age_seconds=interview.possession_proof_lifetime_seconds,
    )
    return Response(status_code=204)


@router.websocket("/control")
async def extension_control(websocket: WebSocket) -> None:
    """Carry the extension's authorized command and result protocol."""
    state = websocket.app.state
    database = state.database
    if not isinstance(database, Database):
        raise TypeError("the application was assembled without a database connector")
    interview = get_interview_settings()
    socket = ExtensionControlSocket(
        websocket,
        SocketDependencies(
            database=database,
            verifier=PageProofVerifier(signer=require_signer(websocket.app)),
            observation=_provider_registry(state).observation,
            control_hub=require_control_hub(websocket.app),
            supported_protocol_versions=_supported_protocol_versions(),
            socket_audience=interview.extension_socket_audience,
            possession_max_age_seconds=interview.possession_proof_lifetime_seconds,
            return_window_seconds=RETURN_WINDOW_SECONDS,
            protocol_version=interview.extension_protocol_version,
        ),
    )
    await socket.serve()


def _supported_protocol_versions() -> tuple[str, ...]:
    """The current and previous extension protocol this control plane accepts."""
    interview = get_interview_settings()
    return tuple(
        dict.fromkeys(
            (
                interview.extension_protocol_version,
                interview.extension_previous_protocol_version,
            )
        )
    )


def _provider_registry(state: object) -> PortRegistry:
    """Return the kernel's provider registry from one application's state."""
    registry = getattr(state, "ports", None)
    if not isinstance(registry, PortRegistry):
        raise TypeError("the application was assembled without a port registry")
    return registry


async def _utrace_user(database: Database, *, auth_user_id: UUID) -> UUID:
    """Resolve the global uTrace User for one authenticated identity."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select id from public.users where auth_user_id = %s", (auth_user_id,)
        )
        row = await cur.fetchone()
    if row is None:
        raise AuthorizationError(
            subject="extension account link",
            reason="this identity has no uTrace User record",
        )
    return as_uuid(row, "id")
