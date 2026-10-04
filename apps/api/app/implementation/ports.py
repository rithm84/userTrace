"""The narrow port between this workstream and the preview-runtime workstream.

This stream builds; the preview-runtime stream deploys. The whole contract
between them is `CandidateHandoff`: an accepted, immutable Preview Version and
everything needed to run exactly those bytes — the source manifest at the pinned
base plus its patch, the build recipe, the check results, the dependency lock
identity, and the content-addressed artifact references.

The handoff deliberately contains no credential, no workspace identity, and no
sandbox reference. The candidate runtime is created fresh from the pinned client
template by the owning stream; nothing here grants access to the workspace that
produced the artifacts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from utrace_contracts.envelope.artifacts import ArtifactReference

__all__ = [
    "BuildRecipe",
    "CandidateHandoff",
    "DependencyLockIdentity",
    "PrebuiltCandidate",
    "PreviewRuntimeAcceptance",
    "PreviewRuntimePort",
    "SourceManifest",
]


class HandoffModel(BaseModel):
    """Base for the handoff parts: immutable and closed to extra fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceManifest(HandoffModel):
    """The exact source the candidate is: a pinned base plus one patch."""

    repository_ref: str
    base_revision: str
    """The commit the workspace checked out and the gate approved against."""
    source_digest: str
    """SHA-256 over the candidate tree's own file digests, computed in the workspace."""
    patch: ArtifactReference
    """The final diff. Applying it to `base_revision` reproduces the candidate."""
    changed_paths: tuple[str, ...]


class BuildRecipe(HandoffModel):
    """How to turn the source into a running candidate, replayed verbatim."""

    node_version: str
    package_manager: str
    install_command: str
    build_command: str
    start_command: str
    application_port: Annotated[int, Field(ge=1, le=65535)]
    environment_profile: str
    """The client's pinned preview build/startup profile, unchanged by the attempt."""


class DependencyLockIdentity(HandoffModel):
    """Which lockfile the candidate installed from, and its exact digest."""

    lockfile_path: str
    lockfile_digest: str
    manifest_digest: str


class CandidateHandoff(HandoffModel):
    """Everything the preview-runtime stream needs to deploy one Preview Version."""

    preview_version_id: UUID
    client_id: UUID
    product_signal_id: UUID
    implementation_attempt_id: UUID
    gate_decision_id: UUID
    approved_scope_digest: str
    source: SourceManifest
    build: BuildRecipe
    dependencies: DependencyLockIdentity
    artifacts: tuple[ArtifactReference, ...]
    """Every extracted artifact, content-addressed, including the patch and the logs."""
    check_results: ArtifactReference
    """The baseline and candidate check evidence the control plane already verified."""
    build_digest: str
    """SHA-256 over the build recipe and the dependency lock identity together."""
    published_at: datetime


class PreviewRuntimeAcceptance(HandoffModel):
    """What the preview-runtime stream returns when it accepts a handoff."""

    preview_version_id: UUID
    preview_runtime_allocation_id: UUID
    accepted_at: datetime


@dataclass(frozen=True, slots=True)
class PrebuiltCandidate:
    """A candidate runtime built from files the checks had not passed yet.

    Nothing is started in it or routed to it. Only a deployment whose artifact
    carries exactly these file digests adopts it.
    """

    runtime_ref: str
    file_digests: Mapping[str, str]


class PreviewRuntimePort(Protocol):
    """The operations this stream calls on the preview-runtime stream."""

    async def prebuild_candidate(
        self, *, client_id: UUID, files: Mapping[str, str], build_command: str
    ) -> PrebuiltCandidate:
        """Build a candidate runtime from staged files while the workspace checks them."""
        ...

    async def discard_prebuilt(self, prebuilt: PrebuiltCandidate) -> None:
        """Destroy a prebuilt runtime no deployment will adopt."""
        ...

    async def deploy_candidate(
        self, *, handoff: CandidateHandoff, prebuilt: PrebuiltCandidate | None
    ) -> PreviewRuntimeAcceptance:
        """Start a fresh candidate runtime from the pinned template for this version.

        The implementation of this port owns seeding, service health, the
        observation path, the front-door route, and readiness. It adopts the
        prebuilt runtime only when its files match the handoff's artifact exactly,
        and builds afresh otherwise. It must never mutate the base allocation and
        must never rebuild from an unpinned branch.
        """
        ...


def artifact_digests(references: Sequence[ArtifactReference]) -> tuple[str, ...]:
    """Every digest in a handoff's artifact set, in the order it was given."""
    return tuple(reference.digest.value for reference in references)
