"""FastAPI dependencies that resolve authority before a handler runs.

Every dependency fails closed. Authentication alone grants nothing: staff access
requires an active ``client_members`` row for the requested client, and
participant access requires the client-scoped identity bound to the resource.
"""

from hmac import compare_digest
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Path, Request
from utrace_contracts.domain.lifecycle import ClientMemberRole, MembershipStatus

from app.auth.jwt import SupabaseJwtVerifier, VerifiedIdentity
from app.auth.principals import ParticipantPrincipal, ServicePrincipal, StaffPrincipal
from app.db.pool import Database
from app.db.rows import as_str, as_uuid
from app.errors import AuthorizationError
from app.settings import Settings

__all__ = [
    "AuthenticatedIdentity",
    "CurrentDatabase",
    "FrontDoorService",
    "ParticipantOfPath",
    "StaffOfPath",
    "get_database",
    "get_settings_dependency",
]

_BEARER = "bearer"


def get_database(request: Request) -> Database:
    """Return the process-wide database connector."""
    database = request.app.state.database
    if not isinstance(database, Database):
        raise TypeError("the application was assembled without a database connector")
    return database


def get_settings_dependency(request: Request) -> Settings:
    """Return the process-wide settings."""
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise TypeError("the application was assembled without settings")
    return settings


def _get_verifier(request: Request) -> SupabaseJwtVerifier:
    verifier = request.app.state.jwt_verifier
    if not isinstance(verifier, SupabaseJwtVerifier):
        raise TypeError("the application was assembled without a JWT verifier")
    return verifier


def _bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise AuthorizationError(
            subject="request", reason="the Authorization header is missing"
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != _BEARER or not token:
        raise AuthorizationError(
            subject="request", reason="expected a Bearer access token"
        )
    return token


async def authenticated_identity(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> VerifiedIdentity:
    """Verify the Supabase Auth access token on this request."""
    verifier = _get_verifier(request)
    return await verifier.verify(_bearer_token(authorization))


AuthenticatedIdentity = Annotated[VerifiedIdentity, Depends(authenticated_identity)]
CurrentDatabase = Annotated[Database, Depends(get_database)]
CurrentSettings = Annotated[Settings, Depends(get_settings_dependency)]


async def resolve_staff_membership(
    database: Database, identity: VerifiedIdentity, client_id: UUID
) -> StaffPrincipal:
    """Resolve an active owner or admin membership for one client."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, role, status
            from public.client_members
            where client_id = %s and auth_user_id = %s and status = %s
            """,
            (client_id, identity.auth_user_id, MembershipStatus.ACTIVE.value),
        )
        row = await cur.fetchone()
    if row is None:
        raise AuthorizationError(
            subject=f"client {client_id}",
            reason="no active client_members row grants this identity access",
        )
    return StaffPrincipal(
        auth_user_id=identity.auth_user_id,
        client_id=client_id,
        client_member_id=as_uuid(row, "id"),
        role=ClientMemberRole(as_str(row, "role")),
    )


async def staff_of_path(
    identity: AuthenticatedIdentity,
    database: CurrentDatabase,
    client_id: Annotated[UUID, Path()],
) -> StaffPrincipal:
    """Resolve staff authority for the ``client_id`` named in the path."""
    return await resolve_staff_membership(database, identity, client_id)


StaffOfPath = Annotated[StaffPrincipal, Depends(staff_of_path)]


async def resolve_participant(
    database: Database, identity: VerifiedIdentity, client_id: UUID
) -> ParticipantPrincipal:
    """Resolve the client-scoped participation identity for one client."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select users.id as utrace_user_id, client_users.id as client_user_id
            from public.users
            join public.client_users on client_users.utrace_user_id = users.id
            where users.auth_user_id = %s and client_users.client_id = %s
            """,
            (identity.auth_user_id, client_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise AuthorizationError(
            subject=f"participation in client {client_id}",
            reason="this identity has no client-scoped uTrace User for that client",
        )
    return ParticipantPrincipal(
        auth_user_id=identity.auth_user_id,
        utrace_user_id=as_uuid(row, "utrace_user_id"),
        client_user_id=as_uuid(row, "client_user_id"),
        client_id=client_id,
    )


async def participant_of_session(
    identity: AuthenticatedIdentity,
    database: CurrentDatabase,
    session_id: Annotated[UUID, Path()],
) -> ParticipantPrincipal:
    """Resolve the participant bound to the Interview Session named in the path."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select sessions.client_id,
                   sessions.client_user_id,
                   users.id as utrace_user_id,
                   users.auth_user_id
            from public.interview_sessions as sessions
            join public.client_users on client_users.id = sessions.client_user_id
            join public.users on users.id = client_users.utrace_user_id
            where sessions.id = %s
            """,
            (session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AuthorizationError(
            subject=f"session {session_id}", reason="no such Interview Session"
        )
    if as_uuid(row, "auth_user_id") != identity.auth_user_id:
        raise AuthorizationError(
            subject=f"session {session_id}",
            reason="only the session's own originating user may act on it",
        )
    return ParticipantPrincipal(
        auth_user_id=identity.auth_user_id,
        utrace_user_id=as_uuid(row, "utrace_user_id"),
        client_user_id=as_uuid(row, "client_user_id"),
        client_id=as_uuid(row, "client_id"),
    )


ParticipantOfPath = Annotated[ParticipantPrincipal, Depends(participant_of_session)]


async def front_door_service(
    settings: CurrentSettings,
    authorization: Annotated[str | None, Header()] = None,
) -> ServicePrincipal:
    """Authenticate the preview front door's service token."""
    token = _bearer_token(authorization)
    if not _constant_time_equal(token, settings.front_door_service_token):
        raise AuthorizationError(
            subject="preview front door", reason="the service token does not match"
        )
    return ServicePrincipal(service="preview_front_door")


def _constant_time_equal(left: str, right: str) -> bool:
    """Compare two secrets without leaking their difference by timing."""
    return compare_digest(left.encode("utf-8"), right.encode("utf-8"))


FrontDoorService = Annotated[ServicePrincipal, Depends(front_door_service)]
