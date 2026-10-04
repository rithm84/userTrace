"""The warm pool and the claim.

uTrace prepares a small pool of unclaimed full preview runtimes from the pinned
template, so a user who accepts usually finds a runtime waiting. When none is
ready, session preparation creates one from the template while greeting and
problem discussion continue.

A claimed base allocation is exclusive to one Interview Session. It is never
returned to the pool: when it is no longer needed the teardown path releases and
then terminates it, and fresh provisioning replaces it.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    PreviewRuntimeAllocationState,
)

from app.db.kernel import (
    AggregateRef,
    ColumnValue,
    TransitionRequest,
    apply_transition_on_connection,
)
from app.db.pool import Database
from app.db.rows import (
    as_datetime,
    as_int,
    as_optional_str,
    as_optional_uuid,
    as_str,
    as_uuid,
)
from app.envelope import Actor, AuditIntent, EventAppend, StreamRef
from app.errors import (
    AggregateNotFoundError,
    GuardRejectedError,
    LeaseLostError,
)
from app.ids import uuid7
from app.lifecycle.machine import resolve
from app.logging import get_logger
from app.preview.machines import (
    PREVIEW_RUNTIME_ALLOCATION_MACHINE,
    PreviewRuntimeAllocationFacts,
)
from app.preview.records import AllocationPins, PoolRole, RuntimeTemplate

__all__ = [
    "PREVIEW_ACTOR",
    "acquire_allocation",
    "claim_base_allocation",
    "commit_allocation_transition",
    "insert_allocation",
    "load_pins",
    "load_runtime_secret",
    "mint_secret",
    "require_current_fencing_token",
    "session_allocation_id",
]

_log = get_logger(__name__)

PREVIEW_ACTOR = Actor(kind=ActorKind.SYSTEM, ref="preview.runtime_controller")

_SECRET_BYTES = 48


def mint_secret() -> str:
    """Return one URL-safe secret for a runtime handoff or route-context key."""
    return secrets.token_urlsafe(_SECRET_BYTES)


async def insert_allocation(
    database: Database,
    *,
    template: RuntimeTemplate,
    pool_role: PoolRole,
    allocation_kind: str,
    product_signal_id: UUID | None,
    preview_version_id: UUID | None,
    runtime_ref: str,
    origin_host: str | None,
    handoff_secret: str,
    runtime_expires_at: datetime,
) -> UUID:
    """Insert one provisioning allocation with the template's pins copied onto it.

    `runtime_expires_at` is when the runtime's sandbox reaches its execution limit.
    """
    allocation_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.preview_runtime_allocations (
                id, client_id, product_signal_id, allocation_kind, state,
                preview_version_id, seed_manifest_digest, runtime_ref, origin_host,
                preview_runtime_template_id, pool_role, pinned_configuration_release_id,
                image_digest, recipe_digest, fixture_clock, authenticated_identity,
                sdk_versions, application_port, handoff_secret, runtime_expires_at,
                fencing_token
            )
            values (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, utrace.next_fencing_token()
            )
            """,
            (
                allocation_id,
                template.client_id,
                product_signal_id,
                allocation_kind,
                PreviewRuntimeAllocationState.PENDING.value,
                preview_version_id,
                template.seed.manifest_digest,
                runtime_ref,
                origin_host,
                template.id,
                pool_role.value,
                template.pinned_configuration_release_id,
                template.image_digest,
                template.recipe_digest,
                template.seed.fixture_clock,
                template.seed.authenticated_identity,
                Jsonb(dict(template.sdk_versions)),
                template.application_port,
                handoff_secret,
                runtime_expires_at,
            ),
        )
    _log.info(
        "preview_allocation_inserted",
        client_id=str(template.client_id),
        preview_runtime_allocation_id=str(allocation_id),
        pool_role=pool_role.value,
        runtime_ref=runtime_ref,
    )
    return allocation_id


async def load_pins(database: Database, *, allocation_id: UUID) -> AllocationPins:
    """Return one allocation's recorded pins."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(_PINS_QUERY, (allocation_id,))
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="preview_runtime_allocations", row_id=allocation_id
        )
    return _as_pins(row)


_PINS_QUERY = """
    select id, client_id, preview_runtime_template_id, pinned_configuration_release_id,
           pool_role, image_digest, recipe_digest, seed_manifest_digest, fixture_clock,
           authenticated_identity, sdk_versions, application_port, runtime_ref,
           fencing_token, route_revision
    from public.preview_runtime_allocations
    where id = %s
"""


def _as_pins(row: Mapping[str, object]) -> AllocationPins:
    versions = row["sdk_versions"]
    if not isinstance(versions, dict):
        raise TypeError("preview_runtime_allocations.sdk_versions is not an object")
    return AllocationPins(
        preview_runtime_allocation_id=as_uuid(row, "id"),
        client_id=as_uuid(row, "client_id"),
        preview_runtime_template_id=as_uuid(row, "preview_runtime_template_id"),
        pinned_configuration_release_id=as_uuid(row, "pinned_configuration_release_id"),
        pool_role=PoolRole(as_str(row, "pool_role")),
        image_digest=as_str(row, "image_digest"),
        recipe_digest=as_str(row, "recipe_digest"),
        seed_manifest_digest=as_str(row, "seed_manifest_digest"),
        fixture_clock=as_datetime(row, "fixture_clock"),
        authenticated_identity=as_str(row, "authenticated_identity"),
        sdk_versions={str(key): str(value) for key, value in versions.items()},
        application_port=as_int(row, "application_port"),
        runtime_ref=as_str(row, "runtime_ref"),
        fencing_token=as_int(row, "fencing_token"),
        route_revision=(
            None if row["route_revision"] is None else as_int(row, "route_revision")
        ),
    )


async def acquire_allocation(
    database: Database, *, allocation_id: UUID
) -> tuple[AllocationPins, int]:
    """Take over one allocation for this worker and return its new fencing token.

    Acquiring mints a fresh token, so any worker still holding the previous one
    is fenced out of every mutating step from this moment on.
    """
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "select utrace.acquire_preview_allocation(%s) as token", (allocation_id,)
        )
        minted = await cur.fetchone()
        if minted is None:
            raise AggregateNotFoundError(
                table="preview_runtime_allocations", row_id=allocation_id
            )
        token = as_int(minted, "token")
        await cur.execute(_PINS_QUERY, (allocation_id,))
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="preview_runtime_allocations", row_id=allocation_id
        )
    return _as_pins(row), token


async def require_current_fencing_token(
    database: Database, *, allocation_id: UUID, fencing_token: int
) -> None:
    """Refuse a mutating step whose token is no longer the allocation's current one."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select fencing_token from public.preview_runtime_allocations where id = %s",
            (allocation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="preview_runtime_allocations", row_id=allocation_id
        )
    if as_int(row, "fencing_token") != fencing_token:
        raise LeaseLostError(
            lease_kind="preview_runtime_allocation",
            lease_id=allocation_id,
            fencing_token=fencing_token,
        )


async def commit_allocation_transition(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    allocation_id: UUID,
    trigger: str,
    facts: PreviewRuntimeAllocationFacts,
    mutation: Mapping[str, ColumnValue],
    event_type: str,
    payload: Mapping[str, str | int | bool | None],
    reason_code: str | None,
    correlation_id: UUID | None,
    additional_locks: Sequence[AggregateRef],
) -> str:
    """Resolve and commit one allocation transition inside a caller's transaction."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select state, revision from public.preview_runtime_allocations where id = %s",
            (allocation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="preview_runtime_allocations", row_id=allocation_id
        )
    plan = resolve(
        PREVIEW_RUNTIME_ALLOCATION_MACHINE, as_str(row, "state"), trigger, facts
    )
    request = TransitionRequest(
        client_id=client_id,
        aggregate=AggregateRef(
            kind=AggregateKind.PREVIEW_RUNTIME_ALLOCATION,
            row_id=allocation_id,
            expected_revision=as_int(row, "revision"),
        ),
        machine=plan.machine,
        machine_version=plan.machine_version,
        state_column="state",
        from_states=plan.sources,
        to_state=plan.target,
        mutation=dict(mutation),
        event=EventAppend(
            message_id=uuid7(),
            stream=StreamRef(
                kind=AggregateKind.PREVIEW_RUNTIME_ALLOCATION, id=allocation_id
            ),
            event_type=event_type,
            payload=dict(payload),
            actor=PREVIEW_ACTOR,
            causation_id=None,
            correlation_id=correlation_id,
            occurred_at=datetime.now(tz=UTC),
        ),
        jobs=(),
        commands=(),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=StreamRef(
                kind=AggregateKind.PREVIEW_RUNTIME_ALLOCATION, id=allocation_id
            ),
            action=f"preview_allocation_{trigger}",
            outcome=plan.target,
            reason_code=reason_code,
            actor=PREVIEW_ACTOR,
            pinned_configuration_release_id=None,
            detail=dict(payload),
        ),
        additional_locks=tuple(additional_locks),
    )
    outcome = await apply_transition_on_connection(conn, request)
    return outcome.to_state


async def claim_base_allocation(
    database: Database,
    *,
    interview_session_id: UUID,
    product_signal_id: UUID,
    client_id: UUID,
    template_id: UUID,
) -> AllocationPins | None:
    """Claim one ready warm runtime for an Interview Session, or `None` if none is ready.

    The claim binds the runtime to the session in the same transaction that
    locks it, so a runtime cannot be handed to two sessions.
    """
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.preview_runtime_allocations
            where preview_runtime_template_id = %s
              and pool_role = 'warm_pool'
              and state = %s
              and interview_session_id is null
              and utrace.preview_warm_runtime_fresh(runtime_expires_at)
            order by created_at
            limit 1
            for update skip locked
            """,
            (template_id, PreviewRuntimeAllocationState.READY.value),
        )
        candidate = await cur.fetchone()
        if candidate is None:
            return None
        allocation_id = as_uuid(candidate, "id")

        await commit_allocation_transition(
            conn,
            client_id=client_id,
            allocation_id=allocation_id,
            trigger="claim",
            facts=PreviewRuntimeAllocationFacts(
                runtime_reachable=True,
                seed_manifest_matches=True,
                readiness_admits_traffic=True,
                routes_revoked=False,
                evidence_extracted=False,
            ),
            mutation={
                "interview_session_id": interview_session_id,
                "product_signal_id": product_signal_id,
                "pool_role": PoolRole.SESSION_BASE.value,
                "claimed_at": datetime.now(tz=UTC),
            },
            event_type="preview_runtime_allocation_claimed",
            payload={
                "interview_session_id": str(interview_session_id),
                "product_signal_id": str(product_signal_id),
            },
            reason_code="interview_session_claim",
            correlation_id=product_signal_id,
            additional_locks=(),
        )
        await cur.execute(
            """
            update public.interview_sessions
            set preview_runtime_allocation_id = %s, revision = revision + 1,
                updated_at = now()
            where id = %s
            """,
            (allocation_id, interview_session_id),
        )
        await cur.execute(_PINS_QUERY, (allocation_id,))
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="preview_runtime_allocations", row_id=allocation_id
        )
    _log.info(
        "preview_allocation_claimed",
        client_id=str(client_id),
        interview_session_id=str(interview_session_id),
        preview_runtime_allocation_id=str(allocation_id),
    )
    return _as_pins(row)


async def load_runtime_secret(
    database: Database, *, allocation_id: UUID, column: str
) -> str:
    """Return one secret injected into a runtime at creation.

    Only ``handoff_secret`` is readable this way: the route-context secret belongs
    to a route revision, not to the allocation, and is read with its route.
    """
    if column != "handoff_secret":
        raise ValueError(f"'{column}' is not a readable allocation secret")
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select handoff_secret from public.preview_runtime_allocations where id = %s",
            (allocation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="preview_runtime_allocations", row_id=allocation_id
        )
    secret = as_optional_str(row, "handoff_secret")
    if secret is None:
        raise GuardRejectedError(
            machine="preview_runtime_allocation",
            trigger="read_handoff_secret",
            reason="the allocation carries no handoff secret",
        )
    return secret


async def session_allocation_id(
    database: Database, *, interview_session_id: UUID
) -> UUID | None:
    """Return the base allocation bound to one Interview Session, if any."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select preview_runtime_allocation_id
            from public.interview_sessions where id = %s
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="interview_sessions", row_id=interview_session_id
        )
    return as_optional_uuid(row, "preview_runtime_allocation_id")
