"""The Product Overview Version.

Each client has one active immutable Product Overview Version covering product
purpose, terminology, primary users, workflows, known capabilities, entities,
integrations, constraints, and sources. Routing pins the version it used, and
interview delegations carry it whole: at a few thousand characters it travels
with every bundle that needs product knowledge rather than through a search
index.

`ProductOverview` is validated, and activation supersedes the previous version
and publishes the new one in one transaction. Activating the same content again
returns the version that already holds it. If there is no valid overview, intake
is preserved and routing waits rather than routing without product knowledge.

Contract: docs/arch-components/context-assembly.md#product-overview-lifecycle
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.db.pool import Database
from app.db.rows import as_int, as_str, as_uuid
from app.errors import AggregateNotFoundError
from app.ids import uuid7
from app.logging import get_logger

__all__ = [
    "ActiveProductOverview",
    "OverviewSection",
    "OverviewSource",
    "ProductOverview",
    "activate_overview",
    "load_active_overview",
    "render_overview",
]

_log = get_logger(__name__)

_STATE_ACTIVE = "active"
_STATE_SUPERSEDED = "superseded"


class _Frozen(BaseModel):
    """Immutable, strictly validated base."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class OverviewSource(_Frozen):
    """One source the overview was synthesized from, with its exact revision."""

    source_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    """What the source is: `repository_file`, `configuration`, `sdk_annotation`."""
    location: str = Field(min_length=1)
    revision: str = Field(min_length=1)


class OverviewSection(_Frozen):
    """One titled block of product knowledge and the sources that support it.

    `inferred` marks a statement uTrace derived rather than read directly, which
    the contract requires to stay marked as inference.
    """

    key: str = Field(pattern=r"^[a-z0-9_]+$")
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    source_ids: tuple[str, ...] = Field(min_length=1)
    inferred: bool

    def content_digest(self) -> str:
        """Return the content hash recorded as this section's source revision."""
        payload = f"{self.key}\n{self.title}\n{self.body}\n{','.join(self.source_ids)}"
        return f"sha256:{sha256(payload.encode('utf-8')).hexdigest()}"


class ProductOverview(_Frozen):
    """The synthesized product knowledge one Product Overview Version publishes."""

    product_name: str = Field(min_length=1)
    purpose: OverviewSection
    terminology: OverviewSection
    primary_users: OverviewSection
    workflows: OverviewSection
    capabilities: OverviewSection
    entities: OverviewSection
    integrations: OverviewSection
    constraints: OverviewSection
    sources: tuple[OverviewSource, ...] = Field(min_length=1)

    def sections(self) -> tuple[OverviewSection, ...]:
        """Return every section in a stable order."""
        return (
            self.purpose,
            self.terminology,
            self.primary_users,
            self.workflows,
            self.capabilities,
            self.entities,
            self.integrations,
            self.constraints,
        )

    def check_source_references(self) -> None:
        """Refuse an overview whose sections cite a source it does not declare."""
        declared = {source.source_id for source in self.sources}
        for section in self.sections():
            unknown = sorted(set(section.source_ids) - declared)
            if unknown:
                raise ValueError(
                    f"overview section '{section.key}' cites undeclared sources: {unknown}"
                )

    def content_digest(self) -> str:
        """Return the digest that identifies this exact overview content."""
        joined = "\n".join(section.content_digest() for section in self.sections())
        sources = "\n".join(
            f"{source.source_id}@{source.revision}" for source in self.sources
        )
        payload = f"{self.product_name}\n{joined}\n{sources}"
        return f"sha256:{sha256(payload.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True, slots=True)
class ActiveProductOverview:
    """The active Product Overview Version a routing batch pins."""

    id: UUID
    client_id: UUID
    version_number: int
    content_digest: str
    overview: ProductOverview


async def activate_overview(
    database: Database,
    *,
    client_id: UUID,
    overview: ProductOverview,
) -> ActiveProductOverview:
    """Publish one Product Overview Version and supersede the previous active one.

    Re-running with the same content returns the existing version rather than
    publishing a second one, so preparation is safe to repeat.
    """
    overview.check_source_references()
    digest = overview.content_digest()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, version_number, content_digest, overview
            from public.product_overview_versions
            where client_id = %s and content_digest = %s
            """,
            (client_id, digest),
        )
        existing = await cur.fetchone()
        if existing is not None:
            return ActiveProductOverview(
                id=as_uuid(existing, "id"),
                client_id=client_id,
                version_number=as_int(existing, "version_number"),
                content_digest=as_str(existing, "content_digest"),
                overview=ProductOverview.model_validate(existing["overview"]),
            )

        await cur.execute(
            """
            update public.product_overview_versions
            set state = %s, superseded_at = now(), revision = revision + 1,
                updated_at = now()
            where client_id = %s and state = %s
            """,
            (_STATE_SUPERSEDED, client_id, _STATE_ACTIVE),
        )
        await cur.execute(
            """
            select coalesce(max(version_number), 0) + 1 as next_number
            from public.product_overview_versions where client_id = %s
            """,
            (client_id,),
        )
        numbering = await cur.fetchone()
        version_number = as_int(numbering, "next_number") if numbering is not None else 1
        version_id = uuid7()
        await cur.execute(
            """
            insert into public.product_overview_versions (
                id, client_id, version_number, state, content_digest, overview,
                sources, activated_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, now())
            """,
            (
                version_id,
                client_id,
                version_number,
                _STATE_ACTIVE,
                digest,
                Jsonb(overview.model_dump(mode="json")),
                Jsonb([source.model_dump(mode="json") for source in overview.sources]),
            ),
        )
    _log.info(
        "product_overview_activated",
        client_id=str(client_id),
        product_overview_version_id=str(version_id),
        version_number=version_number,
        content_digest=digest,
    )
    return ActiveProductOverview(
        id=version_id,
        client_id=client_id,
        version_number=version_number,
        content_digest=digest,
        overview=overview,
    )


async def load_active_overview(
    database: Database, *, client_id: UUID
) -> ActiveProductOverview:
    """Return the client's active Product Overview Version.

    Routing waits when none exists: an overview is a required routing input, and
    an absent one is a blocked analysis rather than a reason to route blind.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, version_number, content_digest, overview
            from public.product_overview_versions
            where client_id = %s and state = %s
            """,
            (client_id, _STATE_ACTIVE),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="product_overview_versions", row_id=client_id
        )
    return ActiveProductOverview(
        id=as_uuid(row, "id"),
        client_id=client_id,
        version_number=as_int(row, "version_number"),
        content_digest=as_str(row, "content_digest"),
        overview=ProductOverview.model_validate(row["overview"]),
    )


def render_overview(overview: ProductOverview, *, sections: Sequence[str]) -> str:
    """Return the named sections, in overview order, as the titled text a model reads."""
    wanted = frozenset(sections)
    chosen = [section for section in overview.sections() if section.key in wanted]
    if not chosen:
        raise ValueError(f"no overview section matches {sorted(wanted)}")
    return "\n\n".join(f"{section.title}: {section.body}" for section in chosen)
