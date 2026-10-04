"""The durable preview environment an observation activation binds to.

A preview environment is the prepared environment one pinned runtime template
and configuration release define. Several warm and claimed runtime allocations
of the same template are the same environment, which is why an activation
carries ``preview_environment_id`` and ``preview_runtime_allocation_id`` as
separate identities.

The record is created on first use rather than during onboarding: the template
row is what defines the environment, and the first activation behind it is the
first moment ingestion needs its identity. Creation is idempotent under the
unique key, so two concurrent activations resolve to one row.

Contract: docs/arch-components/server-sdk.md#configuration-identity-and-credentials
"""

from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.db.rows import as_uuid
from app.ids import uuid7
from app.ingestion.errors import SourceValidationError

__all__ = ["resolve_preview_environment_id"]


async def resolve_preview_environment_id(
    conn: AsyncConnection[DictRow], *, preview_runtime_allocation_id: UUID
) -> UUID:
    """Return the environment identity of one allocation's template.

    An allocation that carries no pinned template or configuration release has
    not been prepared, so it has no environment to name: that refuses here rather
    than being papered over with a derived identity.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select client_id, preview_runtime_template_id,
                   pinned_configuration_release_id
            from public.preview_runtime_allocations
            where id = %s
            """,
            (preview_runtime_allocation_id,),
        )
        allocation = await cur.fetchone()
    if allocation is None:
        raise SourceValidationError(
            field="preview_runtime_allocation_id",
            reason="names no preview runtime allocation",
        )
    if (
        allocation["preview_runtime_template_id"] is None
        or allocation["pinned_configuration_release_id"] is None
    ):
        raise SourceValidationError(
            field="preview_runtime_allocations.preview_runtime_template_id",
            reason=(
                "the allocation pins no runtime template and configuration release, "
                "so it names no prepared preview environment"
            ),
        )
    client_id = as_uuid(allocation, "client_id")
    template_id = as_uuid(allocation, "preview_runtime_template_id")
    release_id = as_uuid(allocation, "pinned_configuration_release_id")

    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.preview_environments (
                id, client_id, preview_runtime_template_id,
                pinned_configuration_release_id
            )
            values (%s, %s, %s, %s)
            on conflict (client_id, preview_runtime_template_id) do nothing
            """,
            (uuid7(), client_id, template_id, release_id),
        )
        await cur.execute(
            """
            select id from public.preview_environments
            where client_id = %s and preview_runtime_template_id = %s
            """,
            (client_id, template_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise SourceValidationError(
            field="preview_environments",
            reason="the preview environment record could not be resolved",
        )
    return as_uuid(row, "id")
