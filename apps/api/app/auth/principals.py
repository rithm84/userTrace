"""Resolved principals.

Authentication proves an identity; these records carry the authority that
explicit uTrace rows grant it. Client staff and customer participants are
separate: a participant never inherits staff access.
"""

from dataclasses import dataclass
from uuid import UUID

from utrace_contracts.domain.lifecycle import ClientMemberRole

__all__ = ["ParticipantPrincipal", "ServicePrincipal", "StaffPrincipal"]


@dataclass(frozen=True, slots=True)
class StaffPrincipal:
    """An active owner or admin of one client."""

    auth_user_id: UUID
    client_id: UUID
    client_member_id: UUID
    role: ClientMemberRole


@dataclass(frozen=True, slots=True)
class ParticipantPrincipal:
    """A uTrace User's client-scoped participation identity."""

    auth_user_id: UUID
    utrace_user_id: UUID
    client_user_id: UUID
    client_id: UUID


@dataclass(frozen=True, slots=True)
class ServicePrincipal:
    """An internal service identity, such as the preview front door."""

    service: str
