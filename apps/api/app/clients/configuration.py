"""The typed body of an immutable client configuration release.

A `ClientConfigurationRelease` row stores this model as JSON. Every Product
Signal pins the release it was routed under, so the policy that governed a case
can be reconstructed exactly. Editing produces a new release; nothing here is
mutated in place.

The active release for a client is the one with the highest `release_number`.
Activation is the insert itself, which is why the table carries an immutability
trigger.

`digest` is the release's pinned identity, not a second copy of the policy:
`release_digest` derives it from the canonical policy bytes, and the database
refuses any other shape.

Contract: docs/arch-components/policy-and-permissions.md#autonomy-policy-model
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict, Field
from utrace_contracts import payload_digest

from app.db.pool import Database
from app.db.rows import as_int, as_uuid
from app.envelope import JsonValue
from app.errors import AggregateNotFoundError
from app.gate.policy import ImplementationPolicy

__all__ = [
    "ActiveRelease",
    "ClientConfigurationPolicy",
    "ContactBudget",
    "CustomerSurfaceConfig",
    "DataSeedingContractRef",
    "OutreachOfferPolicy",
    "WatchedChannel",
    "load_active_release",
    "load_active_release_on_connection",
    "release_digest",
]


class _Frozen(BaseModel):
    """Immutable, strictly validated base. Unrelated extra fields are refused."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class WatchedChannel(_Frozen):
    """One configured intake surface inside the client's Customer Surface."""

    channel_ref: str = Field(min_length=1)
    name: str = Field(min_length=1)


class CustomerSurfaceConfig(_Frozen):
    """The one Discord server a client connects, and the channels uTrace watches."""

    provider: Literal["discord"]
    installation_ref: str = Field(min_length=1)
    """The provider installation scope: the Discord guild id for this client."""
    watched_channels: tuple[WatchedChannel, ...] = Field(min_length=1)

    def watches(self, channel_ref: str) -> bool:
        """Return whether one channel is a configured intake surface."""
        return any(channel.channel_ref == channel_ref for channel in self.watched_channels)


class DataSeedingContractRef(_Frozen):
    """The approved Data Seeding Contract this release pins, by identity."""

    manifest_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    fixture_clock: str = Field(min_length=1)
    environment_template_id: str = Field(min_length=1)
    environment_template_revision: int = Field(gt=0)


class OutreachOfferPolicy(_Frozen):
    """The fixed offer this client authorizes, inside the owner's issuance ceiling."""

    enabled: bool
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    fixed_amount_minor_units: int = Field(gt=0)
    authorized_ceiling_minor_units: int = Field(gt=0)
    billing_account_ref: str = Field(min_length=1)
    billing_use: str = Field(min_length=1)
    completion_terms: tuple[str, ...] = Field(min_length=1)


class ContactBudget(_Frozen):
    """The pinned private-contact allowance and the invitation response window."""

    per_person: int = Field(gt=0)
    per_client: int = Field(gt=0)
    invitation_ttl_seconds: int = Field(gt=0)


class ProductOverviewRef(_Frozen):
    """The Product Overview revision this release was verified against."""

    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ClientConfigurationPolicy(_Frozen):
    """Everything an immutable configuration release pins for one client."""

    customer_surface: CustomerSurfaceConfig
    implementation: ImplementationPolicy
    """The Autonomy Policy the Implementation Gate narrows into an approved scope."""
    data_seeding: DataSeedingContractRef
    outreach_offers: OutreachOfferPolicy
    contact_budget: ContactBudget
    product_overview: ProductOverviewRef
    annotation_manifest: Mapping[str, JsonValue]
    """The client release's compiled annotation manifest; activation validates it."""
    server_instrumentation_manifest: Mapping[str, JsonValue]
    """The signed server instrumentation manifest; activation verifies its signature."""
    dashboard_base_url: str = Field(pattern=r"^https://[^/]+$")
    """The authenticated dashboard origin a draft PR links its Case File from."""


class ActiveRelease(_Frozen):
    """One configuration release resolved from the database."""

    id: UUID
    client_id: UUID
    release_number: int
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy: ClientConfigurationPolicy


_SELECT_RELEASE = """
select id, client_id, release_number, digest, policy
from public.configuration_releases
where client_id = %s
order by release_number desc
limit 1
"""


def _to_release(row: DictRow) -> ActiveRelease:
    """Validate one stored release body into its typed model."""
    return ActiveRelease(
        id=as_uuid(row, "id"),
        client_id=as_uuid(row, "client_id"),
        release_number=as_int(row, "release_number"),
        digest=str(row["digest"]),
        policy=ClientConfigurationPolicy.model_validate(row["policy"]),
    )


async def load_active_release_on_connection(
    conn: AsyncConnection[DictRow], *, client_id: UUID
) -> ActiveRelease:
    """Return the client's active release inside a caller-owned transaction."""
    async with conn.cursor() as cur:
        await cur.execute(_SELECT_RELEASE, (client_id,))
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="configuration_releases", row_id=client_id)
    return _to_release(row)


async def load_active_release(database: Database, *, client_id: UUID) -> ActiveRelease:
    """Return the client's active release: the highest `release_number`."""
    async with database.connection() as conn:
        return await load_active_release_on_connection(conn, client_id=client_id)


def release_digest(policy: ClientConfigurationPolicy) -> str:
    """Return the `sha256:<hex>` digest that identifies one release's policy.

    The digest is taken over the RFC 8785 canonical bytes of the policy, the same
    canonical form every other signed or content-addressed uTrace payload uses,
    so two releases pinning identical policy carry identical digests whatever
    order their fields were written in. The policy itself stays in `policy`; the
    digest is the short identity a manifest, a dashboard, or a Case File cites.
    """
    return f"sha256:{payload_digest(policy.model_dump(mode='json'))}"


