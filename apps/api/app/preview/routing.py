"""Preview routes: the session origin, its revisions, and their teardown.

Each Interview Session receives one opaque single-level subdomain of the
uTrace-owned preview host. The stable hostname points initially at the base
allocation and advances atomically to a candidate only after that candidate
passes full readiness. Every request resolves exactly one committed revision; a
superseded or revoked revision fails closed, and every revision pointing at a
runtime is revoked before that runtime is destroyed.

The route-context signing secret of a revision is the secret injected into the
runtime that revision points at. A context minted for one revision therefore
fails the next revision's runtime outright, which is what makes an advance a
real cut-over rather than a label change.
"""

from __future__ import annotations

import base64
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.db.pool import Database
from app.db.rows import (
    as_datetime,
    as_int,
    as_optional_str,
    as_optional_uuid,
    as_str,
    as_uuid,
)
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.ids import uuid7
from app.logging import get_logger
from app.preview.records import (
    PreviewRouteRecord,
    RouteReadinessState,
    RouteUpstream,
)

__all__ = [
    "BASE_PREVIEW_VERSION_ID",
    "advance_route",
    "commit_route",
    "draft_route_record",
    "mint_origin_host",
    "revoke_routes",
    "route_for_host",
    "route_for_session",
    "set_route_readiness",
]

_log = get_logger(__name__)

_ORIGIN_ENTROPY_BYTES = 16

# A base preview runs the pinned template revision rather than a candidate, but
# the front-door contract requires a Preview Version on every route record. The
# nil UUID is that "no candidate" identity: it is a valid UUID on the wire and it
# can never collide with a real Preview Version.
BASE_PREVIEW_VERSION_ID = UUID(int=0)

_ROUTE_COLUMNS = """
    routes.id, routes.client_id, routes.interview_session_id,
    routes.product_signal_id, routes.preview_runtime_allocation_id,
    routes.preview_version_id, routes.public_preview_host, routes.route_revision,
    routes.readiness_state, routes.upstream_port_url, routes.application_port,
    routes.route_context_signing_secret, routes.expires_at,
    allocations.preview_entry_token
"""

# The entry token belongs to the runtime, so it is joined rather than copied onto
# each revision: an activation that lands after a revision was committed is
# visible to the very next resolution.
_ROUTE_SOURCE = """
    public.preview_routes as routes
    join public.preview_runtime_allocations as allocations
      on allocations.id = routes.preview_runtime_allocation_id
"""


def mint_origin_host(*, origin_suffix: str) -> str:
    """Return a fresh opaque single-level subdomain of the preview host.

    The label is 128 bits of base32 without padding, so it is unguessable and
    contains only characters a hostname label allows. One level keeps the origin
    a separate site once ``preview.utrace.dev`` is listed in the Public Suffix
    List: a deeper name would share a site with its parent.
    """
    label = (
        base64.b32encode(secrets.token_bytes(_ORIGIN_ENTROPY_BYTES))
        .decode("ascii")
        .rstrip("=")
        .lower()
    )
    return f"{label}.{origin_suffix}"


async def commit_route(
    database: Database,
    *,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
    preview_runtime_allocation_id: UUID,
    preview_version_id: UUID | None,
    public_preview_host: str,
    upstream_port_url: str,
    application_port: int,
    route_context_signing_secret: str,
    ttl_seconds: int,
) -> PreviewRouteRecord:
    """Commit the next route revision for one session origin.

    Superseding the current revision and committing the next one happen in one
    transaction, and the partial unique index refuses a second active revision
    for the host, so no request can ever see two.
    """
    now = datetime.now(tz=UTC)
    expires_at = now + timedelta(seconds=ttl_seconds)
    route_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        # Serialize commits for this origin. Two concurrent advances would
        # otherwise read the same maximum revision and race on the partial unique
        # index, and the loser would surface as a constraint error rather than as
        # an ordered second revision.
        await cur.execute(
            "select pg_advisory_xact_lock(hashtext(%s))", (public_preview_host,)
        )
        await cur.execute(
            """
            select coalesce(max(route_revision), 0) + 1 as next_revision
            from public.preview_routes
            where public_preview_host = %s
            """,
            (public_preview_host,),
        )
        row = await cur.fetchone()
        revision = as_int(row, "next_revision") if row is not None else 1
        await cur.execute(
            """
            update public.preview_routes
            set state = 'superseded', superseded_at = now(), revision = revision + 1,
                updated_at = now()
            where public_preview_host = %s and state = 'active'
            """,
            (public_preview_host,),
        )
        await cur.execute(
            """
            insert into public.preview_routes (
                id, client_id, product_signal_id, interview_session_id,
                preview_runtime_allocation_id, preview_version_id,
                public_preview_host, route_revision, state, readiness_state,
                upstream_port_url, application_port, route_context_signing_secret,
                expires_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, 'active', 'ready', %s, %s, %s, %s)
            returning id
            """,
            (
                route_id,
                client_id,
                product_signal_id,
                interview_session_id,
                preview_runtime_allocation_id,
                preview_version_id,
                public_preview_host,
                revision,
                upstream_port_url,
                application_port,
                route_context_signing_secret,
                expires_at,
            ),
        )
        committed = await cur.fetchone()
        if committed is None:
            raise AggregateNotFoundError(table="preview_routes", row_id=route_id)
        await cur.execute(
            """
            update public.interview_sessions
            set current_preview_route_id = %s, revision = revision + 1,
                updated_at = now()
            where id = %s
            """,
            (route_id, interview_session_id),
        )
        # The origin belongs to the runtime its active revision serves: a document
        # that activates after an advance is the candidate's, not the base's.
        await cur.execute(
            """
            update public.preview_runtime_allocations
            set origin_host = null, revision = revision + 1, updated_at = now()
            where origin_host = %s and id <> %s
            """,
            (public_preview_host, preview_runtime_allocation_id),
        )
        await cur.execute(
            """
            update public.preview_runtime_allocations
            set route_revision = %s, origin_host = %s, revision = revision + 1,
                updated_at = now()
            where id = %s
            """,
            (revision, public_preview_host, preview_runtime_allocation_id),
        )
    _log.info(
        "preview_route_committed",
        client_id=str(client_id),
        interview_session_id=str(interview_session_id),
        public_preview_host=public_preview_host,
        route_revision=revision,
        preview_runtime_allocation_id=str(preview_runtime_allocation_id),
    )
    record = await route_for_session(
        database, interview_session_id=interview_session_id
    )
    if record is None:
        raise AggregateNotFoundError(table="preview_routes", row_id=route_id)
    return record


def draft_route_record(
    *,
    current: PreviewRouteRecord,
    preview_runtime_allocation_id: UUID,
    preview_version_id: UUID,
    upstream_port_url: str,
    application_port: int,
    route_context_signing_secret: str,
    preview_entry_token: str | None,
    expires_at: datetime,
) -> PreviewRouteRecord:
    """Build the next revision's record before it is committed.

    A candidate is probed end to end before the session origin advances to it, so
    the probes need the exact claims the committed revision will carry. Nothing
    is written: the origin keeps serving the current revision until the candidate
    has passed, which is what makes the advance atomic rather than a window in
    which an unproven candidate is public.
    """
    return PreviewRouteRecord(
        preview_route_id=uuid7(),
        client_id=current.client_id,
        interview_session_id=current.interview_session_id,
        product_signal_id=current.product_signal_id,
        preview_version_id=preview_version_id,
        preview_runtime_allocation_id=preview_runtime_allocation_id,
        public_preview_host=current.public_preview_host,
        route_revision=current.route_revision + 1,
        readiness_state=RouteReadinessState.READY,
        upstream=RouteUpstream(port_url=upstream_port_url, application_port=application_port),
        route_context_signing_secret=route_context_signing_secret,
        preview_entry_token=preview_entry_token,
        expires_at=expires_at,
    )


async def advance_route(
    database: Database,
    *,
    interview_session_id: UUID,
    preview_runtime_allocation_id: UUID,
    preview_version_id: UUID,
    upstream_port_url: str,
    application_port: int,
    route_context_signing_secret: str,
    ttl_seconds: int,
) -> PreviewRouteRecord:
    """Point a session's existing origin at a ready candidate runtime.

    The origin does not change, so the participant keeps the address they already
    have. The base allocation is left exactly as it was: advancing a route never
    mutates the runtime it moves away from.
    """
    existing = await route_for_session(
        database, interview_session_id=interview_session_id
    )
    if existing is None:
        raise GuardRejectedError(
            machine="preview_route",
            trigger="advance",
            reason="the Interview Session has no committed route to advance",
        )
    return await commit_route(
        database,
        client_id=existing.client_id,
        product_signal_id=existing.product_signal_id,
        interview_session_id=interview_session_id,
        preview_runtime_allocation_id=preview_runtime_allocation_id,
        preview_version_id=preview_version_id,
        public_preview_host=existing.public_preview_host,
        upstream_port_url=upstream_port_url,
        application_port=application_port,
        route_context_signing_secret=route_context_signing_secret,
        ttl_seconds=ttl_seconds,
    )


async def _active_route(
    database: Database, *, predicate: str, parameter: object
) -> PreviewRouteRecord | None:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            f"select {_ROUTE_COLUMNS} from {_ROUTE_SOURCE} "
            f"where routes.{predicate} and routes.state = 'active'",
            (parameter,),
        )
        row = await cur.fetchone()
    return None if row is None else _as_record(row)


async def route_for_session(
    database: Database, *, interview_session_id: UUID
) -> PreviewRouteRecord | None:
    """Return the Interview Session's one active route revision."""
    return await _active_route(
        database,
        predicate="interview_session_id = %s",
        parameter=interview_session_id,
    )


async def route_for_host(
    database: Database, *, public_preview_host: str
) -> PreviewRouteRecord | None:
    """Return the one active route revision for a session origin."""
    return await _active_route(
        database,
        predicate="public_preview_host = %s",
        parameter=public_preview_host,
    )


async def set_route_readiness(
    database: Database,
    *,
    preview_route_id: UUID,
    readiness_state: RouteReadinessState,
    reason: str,
) -> None:
    """Withdraw or restore a committed route's durable readiness.

    A runtime restart or a new sandbox session withdraws readiness: the front door
    then serves the reconnecting surface instead of routing, and traffic resumes
    only when recovery re-proves the path and commits a new revision.
    """
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_routes
            set readiness_state = %s, revocation_reason = %s, revision = revision + 1,
                updated_at = now()
            where id = %s and state = 'active'
            returning id
            """,
            (readiness_state.value, reason, preview_route_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="preview_routes", row_id=preview_route_id)
    _log.info(
        "preview_route_readiness_set",
        preview_route_id=str(preview_route_id),
        readiness_state=readiness_state.value,
        reason=reason,
    )


async def revoke_routes(
    database: Database, *, preview_runtime_allocation_id: UUID, reason: str
) -> int:
    """Revoke every revision pointing at one allocation and return how many.

    Route teardown precedes runtime destruction, always.
    """
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_routes
            set state = 'revoked', revoked_at = now(), revocation_reason = %s,
                revision = revision + 1, updated_at = now()
            where preview_runtime_allocation_id = %s and state <> 'revoked'
            returning id
            """,
            (reason, preview_runtime_allocation_id),
        )
        revoked = len(await cur.fetchall())
    _log.info(
        "preview_routes_revoked",
        preview_runtime_allocation_id=str(preview_runtime_allocation_id),
        revoked=revoked,
        reason=reason,
    )
    return revoked


def _as_record(row: object) -> PreviewRouteRecord:
    """Narrow one route row into the contract-shaped record."""
    if not isinstance(row, dict):
        raise TypeError("expected a database row mapping")
    version = as_optional_uuid(row, "preview_version_id")
    return PreviewRouteRecord(
        preview_route_id=as_uuid(row, "id"),
        client_id=as_uuid(row, "client_id"),
        interview_session_id=as_uuid(row, "interview_session_id"),
        product_signal_id=as_uuid(row, "product_signal_id"),
        preview_version_id=BASE_PREVIEW_VERSION_ID if version is None else version,
        preview_runtime_allocation_id=as_uuid(row, "preview_runtime_allocation_id"),
        public_preview_host=as_str(row, "public_preview_host"),
        route_revision=as_int(row, "route_revision"),
        readiness_state=RouteReadinessState(as_str(row, "readiness_state")),
        upstream=RouteUpstream(
            port_url=as_str(row, "upstream_port_url"),
            application_port=as_int(row, "application_port"),
        ),
        route_context_signing_secret=as_str(row, "route_context_signing_secret"),
        preview_entry_token=as_optional_str(row, "preview_entry_token"),
        expires_at=as_datetime(row, "expires_at"),
    )
