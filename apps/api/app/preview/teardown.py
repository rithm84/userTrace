"""Releasing and terminating a preview runtime.

Teardown is two committed steps, in this order and never merged.

**Release** revokes every route revision pointing at the allocation, tells the
runtime's Server SDK to end its stream, then extracts the minimized evidence the
Case File and the readiness record need. Route teardown precedes runtime
destruction, so no revision still points at the runtime when the sandbox
disappears, and the terminal stream report is flushed before the sandbox that
holds it is gone.

**Terminate** deletes the Vercel sandbox outright. A data-bearing runtime is
never reset and returned to the warm pool; fresh provisioning replaces it.

Extraction is minimized on purpose: the durable evidence is the dataset identity
the runtime held, its health answer, and its recorded readiness, never customer
field values and never the secrets injected into it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import PreviewRuntimeAllocationState

from app.connectors.failures import ConnectorFailure, TechnicalCategory
from app.db.pool import Database
from app.db.rows import as_optional_str, as_str
from app.errors import AggregateNotFoundError
from app.logging import get_logger
from app.preview.activation import (
    STREAM_END_SECONDS,
    ServerActivationContext,
    await_stream_end,
    terminate_server_activation,
)
from app.preview.machines import PreviewRuntimeAllocationFacts
from app.preview.pool import commit_allocation_transition
from app.preview.routing import revoke_routes
from app.preview.runtime import PreviewRuntimeHandle, PreviewRuntimeProvider

__all__ = ["extract_evidence", "release_allocation", "tear_down_allocation"]

_log = get_logger(__name__)




async def extract_evidence(
    database: Database, *, allocation_id: UUID
) -> dict[str, str | int | bool]:
    """Extract the minimized evidence a released runtime has to leave behind.

    Only counts and identities: how many readiness results were recorded, how
    many route revisions existed, and which dataset identity the runtime held.
    The probe records themselves are already durable, so nothing is copied twice
    and no customer field value is ever read.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select allocations.runtime_ref,
                   allocations.seed_manifest_digest,
                   allocations.image_digest,
                   allocations.recipe_digest,
                   allocations.origin_host,
                   (
                     select count(*) from public.preview_readiness_results results
                     where results.preview_runtime_allocation_id = allocations.id
                   ) as readiness_results,
                   (
                     select count(*) from public.preview_routes routes
                     where routes.preview_runtime_allocation_id = allocations.id
                   ) as route_revisions
            from public.preview_runtime_allocations as allocations
            where allocations.id = %s
            """,
            (allocation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="preview_runtime_allocations", row_id=allocation_id
        )
    evidence: dict[str, str | int | bool] = {
        "runtime_ref": as_str(row, "runtime_ref"),
        "seed_manifest_digest": as_str(row, "seed_manifest_digest"),
        "image_digest": as_str(row, "image_digest"),
        "recipe_digest": as_str(row, "recipe_digest"),
        "readiness_results": int(row["readiness_results"]),
        "route_revisions": int(row["route_revisions"]),
        "extracted_at": datetime.now(tz=UTC).isoformat(),
    }
    origin = as_optional_str(row, "origin_host")
    if origin is not None:
        evidence["public_preview_host"] = origin
    return evidence


async def _attach_if_present(
    *, provider: PreviewRuntimeProvider, runtime_ref: str, application_port: int
) -> PreviewRuntimeHandle | None:
    """Reach the sandbox, or report that the provider no longer has it.

    A sandbox the provider has already removed is the end state teardown is
    driving towards, so its absence must not strand the allocation short of
    ``terminated``. Every other provider failure still raises: only a
    definitive not-found is reconciled this way.
    """
    try:
        return await provider.attach(
            runtime_ref=runtime_ref, application_port=application_port
        )
    except ConnectorFailure as failure:
        if failure.detail.category is not TechnicalCategory.NOT_FOUND:
            raise
        _log.info("preview_runtime_already_absent", runtime_ref=runtime_ref)
        return None


async def release_allocation(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    activation: ServerActivationContext,
    allocation_id: UUID,
    reason: str,
) -> dict[str, str | int | bool]:
    """Revoke routes, end the server stream, extract evidence, and commit."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select client_id, runtime_ref, application_port, state
            from public.preview_runtime_allocations where id = %s
            """,
            (allocation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="preview_runtime_allocations", row_id=allocation_id
        )
    client_id = row["client_id"]
    if not isinstance(client_id, UUID):
        raise TypeError("preview_runtime_allocations.client_id is not a UUID")

    revoked = await revoke_routes(
        database, preview_runtime_allocation_id=allocation_id, reason=reason
    )
    handle = await _attach_if_present(
        provider=provider,
        runtime_ref=as_str(row, "runtime_ref"),
        application_port=int(row["application_port"]),
    )

    # The Server SDK flushes and reports a terminal stream state on terminate.
    # It has to happen while the sandbox still runs, and the outcome is recorded
    # either way: an undelivered terminate is a possible evidence gap, not a
    # reason to leave a data-bearing runtime alive.
    termination = await terminate_server_activation(
        database,
        commands=activation.commands,
        client_id=client_id,
        preview_runtime_allocation_id=allocation_id,
        reason="session_ended",
    )

    stream_ended = (
        termination.activation_id is not None
        and termination.delivered
        and await await_stream_end(database, activation_id=termination.activation_id)
    )

    evidence = await extract_evidence(database, allocation_id=allocation_id)
    evidence["runtime_present_at_release"] = handle is not None
    evidence["server_stream_terminated"] = stream_ended
    if termination.activation_id is not None:
        evidence["server_activation_id"] = str(termination.activation_id)
    if not termination.delivered:
        evidence["server_termination_detail"] = termination.detail
    elif not stream_ended:
        evidence["server_termination_detail"] = (
            "terminate was delivered but no terminal stream report arrived within "
            f"{int(STREAM_END_SECONDS)} seconds"
        )
    async with database.transaction() as conn:
        await commit_allocation_transition(
            conn,
            client_id=client_id,
            allocation_id=allocation_id,
            trigger="release",
            facts=PreviewRuntimeAllocationFacts(
                runtime_reachable=True,
                seed_manifest_matches=True,
                readiness_admits_traffic=False,
                routes_revoked=True,
                evidence_extracted=True,
            ),
            mutation={
                "released_at": datetime.now(tz=UTC),
                "evidence": Jsonb(evidence),
            },
            event_type="preview_runtime_allocation_released",
            payload={
                "reason": reason,
                "revoked_routes": revoked,
                "server_stream_terminated": stream_ended,
            },
            reason_code=reason,
            correlation_id=None,
            additional_locks=(),
        )
    _log.info(
        "preview_allocation_released",
        preview_runtime_allocation_id=str(allocation_id),
        revoked_routes=revoked,
        server_stream_terminated=stream_ended,
        reason=reason,
    )
    return evidence



async def tear_down_allocation(
    database: Database,
    *,
    provider: PreviewRuntimeProvider,
    activation: ServerActivationContext,
    allocation_id: UUID,
    reason: str,
) -> None:
    """Release the allocation if it is releasable, then destroy its sandbox.

    A runtime that never reached the pool is abandoned instead of released: it
    has no routes to revoke and no evidence worth extracting, and leaving it
    running would hold the client's sandbox quota against a broken template.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select client_id, state, runtime_ref, application_port
            from public.preview_runtime_allocations where id = %s
            """,
            (allocation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="preview_runtime_allocations", row_id=allocation_id
        )
    client_id = row["client_id"]
    if not isinstance(client_id, UUID):
        raise TypeError("preview_runtime_allocations.client_id is not a UUID")
    state = as_str(row, "state")
    if state == PreviewRuntimeAllocationState.TERMINATED.value:
        return

    releasable = {
        PreviewRuntimeAllocationState.READY.value,
        PreviewRuntimeAllocationState.BOUND.value,
    }
    if state in releasable:
        await release_allocation(
            database,
            provider=provider,
            activation=activation,
            allocation_id=allocation_id,
            reason=reason,
        )
        trigger = "terminate"
    elif state == PreviewRuntimeAllocationState.RELEASED.value:
        trigger = "terminate"
    else:
        trigger = "abandon"

    handle = await _attach_if_present(
        provider=provider,
        runtime_ref=as_str(row, "runtime_ref"),
        application_port=int(row["application_port"]),
    )
    if handle is not None:
        await provider.delete_runtime(handle=handle)
    async with database.transaction() as conn:
        await commit_allocation_transition(
            conn,
            client_id=client_id,
            allocation_id=allocation_id,
            trigger=trigger,
            facts=PreviewRuntimeAllocationFacts(
                runtime_reachable=False,
                seed_manifest_matches=False,
                readiness_admits_traffic=False,
                routes_revoked=True,
                evidence_extracted=True,
            ),
            mutation={"terminated_at": datetime.now(tz=UTC)},
            event_type="preview_runtime_allocation_terminated",
            payload={"reason": reason, "trigger": trigger},
            reason_code=reason,
            correlation_id=None,
            additional_locks=(),
        )
    _log.info(
        "preview_allocation_terminated",
        preview_runtime_allocation_id=str(allocation_id),
        trigger=trigger,
        reason=reason,
    )
