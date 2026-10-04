"""The implementation section of a pinned client configuration release.

A Product Signal pins one configuration release for its whole lifecycle. The
gate reads the implementation section of that release and narrows it into the
approved scope; it can never widen it. Every field is required: a release whose
implementation section is missing or incomplete fails the gate with the release
named, rather than letting a repository, a blocked surface, or a budget default
to something permissive.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.db.pool import Database
from app.errors import AggregateNotFoundError
from app.gate.scope import (
    DependencyPolicy,
    PermittedDependency,
    RequiredCheck,
    ResourceBudgets,
)
from app.implementation.ports import BuildRecipe

__all__ = [
    "ImplementationPolicy",
    "PolicyUnreadableError",
    "load_implementation_policy",
    "read_implementation_policy",
]

POLICY_SECTION = "implementation"


class PolicyUnreadableError(Exception):
    """The pinned configuration release carries no usable implementation policy."""

    def __init__(self, configuration_release_id: UUID, reason: str) -> None:
        super().__init__(
            f"configuration release {configuration_release_id} has no usable "
            f"'{POLICY_SECTION}' policy: {reason}"
        )
        self.configuration_release_id = configuration_release_id
        self.reason = reason


class ImplementationPolicy(BaseModel):
    """What the client authorized before any Product Signal reached the gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    live_implementation_allowed: bool
    """The Autonomy Policy's implementation path: live work, or none at all."""
    repository_ref: str
    """The one authorized repository, `owner/name`."""
    clone_url: str
    """HTTPS clone URL. The token is added at egress and never stored."""
    base_revision: str
    """The repository commit the client authorized work against; the preview template's source."""
    workspace_snapshot_id: str
    """The pinned uTrace-owned workspace snapshot every attempt starts from."""
    callback_hosts: tuple[str, ...]
    """uTrace endpoints the workspace network wall permits, beyond the registries."""
    build_recipe: BuildRecipe
    """The client's pinned preview build and startup profile, replayed unchanged."""
    permitted_paths: tuple[str, ...]
    blocked_paths: tuple[str, ...]
    required_checks: tuple[RequiredCheck, ...]
    dependency_policy: DependencyPolicy
    permitted_dependencies: tuple[PermittedDependency, ...]
    dependency_manifest_paths: tuple[str, ...]
    budgets: ResourceBudgets
    live_window_seconds: Annotated[int, Field(ge=1)]
    """The longest live implementation the pinned policy allows inside a session."""
    implementation_limit: Annotated[int, Field(ge=1)]
    """How many Implementation Attempts one Product Signal may use: the first, then one
    for each change the originating user asks for while validating a new version."""
    forbidden_path_patterns: tuple[str, ...]
    """Deterministic security-screen patterns: auth, secrets, billing, infrastructure."""

    @field_validator("required_checks")
    @classmethod
    def _checks_run_after_earlier_checks(
        cls, checks: tuple[RequiredCheck, ...]
    ) -> tuple[RequiredCheck, ...]:
        """Refuse a check that waits for one not listed before it, or for itself."""
        earlier: set[str] = set()
        for check in checks:
            unknown = [name for name in check.after if name not in earlier]
            if unknown:
                raise ValueError(
                    f"required check {check.name!r} runs after {unknown}, which no earlier "
                    "check is named"
                )
            earlier.add(check.name)
        return checks


def read_implementation_policy(
    *, configuration_release_id: UUID, policy: Mapping[str, object]
) -> ImplementationPolicy:
    """Parse the implementation section of one pinned release, or refuse."""
    section = policy.get(POLICY_SECTION)
    if section is None:
        raise PolicyUnreadableError(
            configuration_release_id, f"the release policy has no '{POLICY_SECTION}' key"
        )
    try:
        return ImplementationPolicy.model_validate(section)
    except ValidationError as error:
        fields = sorted({str(item["loc"][0]) for item in error.errors() if item["loc"]})
        raise PolicyUnreadableError(
            configuration_release_id,
            f"these fields are missing or invalid: {', '.join(fields)}",
        ) from None


async def load_implementation_policy(
    database: Database, *, configuration_release_id: UUID
) -> ImplementationPolicy:
    """Read and parse the implementation policy one pinned release carries."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select policy from public.configuration_releases where id = %s",
            (configuration_release_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="configuration_releases", row_id=configuration_release_id)
    document = row["policy"]
    if not isinstance(document, dict):
        raise TypeError("configuration_releases.policy is not a JSON object")
    return read_implementation_policy(configuration_release_id=configuration_release_id, policy=document)
