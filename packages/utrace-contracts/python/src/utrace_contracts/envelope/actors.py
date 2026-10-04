"""Actor kinds: who or what a producer acted for.

The kinds stay separate because the authority model does. Customer participants
never inherit client-staff access, client staff cannot edit lifecycle state,
model roles are advisory and never impersonate the person whose evidence
informed their proposal, and provider identities never become product identity.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from utrace_contracts.base import SnakeCaseName, StrictModel
from utrace_contracts.envelope.failures import ProviderName
from utrace_contracts.ids.models import UuidV7

__all__ = [
    "Actor",
    "AgentRole",
    "ClientMemberActor",
    "ClientMemberRole",
    "CustomerParticipantActor",
    "ProviderIdentityActor",
    "ServiceActor",
    "SystemActor",
    "UTraceAgentRoleActor",
]

ClientMemberRole = Literal["owner", "admin"]

AgentRole = Literal["gpt_live", "terra", "sol", "jev"]
"""GPT-Live voice, Terra interactive reasoning and retrieval, Sol deep
investigation and coding, Jev intake decisions and semantic attention."""


class CustomerParticipantActor(StrictModel):
    """The client-scoped participant.

    Validation always requires the original ``client_user_id``; no colleague,
    client staff member, or second account may substitute.
    """

    kind: Literal["customer_participant"]
    client_user_id: UuidV7
    customer_account_id: UuidV7 | None = None
    customer_membership_id: UuidV7 | None = None


class ClientMemberActor(StrictModel):
    """Client staff: exactly one owner and zero or more admins per client."""

    kind: Literal["client_member"]
    client_member_id: UuidV7
    role: ClientMemberRole


class UTraceAgentRoleActor(StrictModel):
    """A cognitive role. Its results are advisory; only the control plane authorizes effects."""

    kind: Literal["utrace_agent_role"]
    role: AgentRole
    model_version: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class ProviderIdentityActor(StrictModel):
    """An external provider identity, kept as a scoped reference."""

    kind: Literal["provider_identity"]
    provider: ProviderName
    external_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    installation_ref: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None
    resource_type: SnakeCaseName | None = None


class ServiceActor(StrictModel):
    """An internal uTrace service acting under its own scoped identity."""

    kind: Literal["service"]
    service: SnakeCaseName


class SystemActor(StrictModel):
    """The platform itself: scheduling, lease scans, reconciliation, and expiry."""

    kind: Literal["system"]


Actor = Annotated[
    CustomerParticipantActor
    | ClientMemberActor
    | UTraceAgentRoleActor
    | ProviderIdentityActor
    | ServiceActor
    | SystemActor,
    Field(discriminator="kind"),
]
