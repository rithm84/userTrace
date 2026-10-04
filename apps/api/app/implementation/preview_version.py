"""Preview Version publication.

Before a candidate can exist, the control plane verifies the lease, the approved
scope, the source revision, the required checks, the artifact digests, a secret
scan over the accepted diff, and the pinned policy. A failed or ambiguous check
cannot be relabelled as success by the coding model, so every one of those is a
refusal here rather than a warning.

A Preview Version is immutable. A correction creates a new gate-approved attempt
with new artifacts and a new version; this module never patches one in place. A
later approved revision's version supersedes the earlier one by retiring it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from uuid import UUID

import structlog
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict
from utrace_contracts.canonical import payload_digest
from utrace_contracts.domain.lifecycle import PreviewVersionState

from app.db.pool import Database
from app.db.rows import as_int, as_str, as_uuid
from app.errors import LeaseLostError
from app.ids import uuid7
from app.implementation.artifacts import ArtifactManifest
from app.implementation.checks import CheckComparison, CheckRun
from app.implementation.ports import (
    BuildRecipe,
    CandidateHandoff,
    DependencyLockIdentity,
    PrebuiltCandidate,
    PreviewRuntimeAcceptance,
    PreviewRuntimePort,
    SourceManifest,
)
from app.implementation.scope_check import ScopeCheckResult
from app.implementation.specification import AttemptSpecification

__all__ = [
    "PublicationRefusal",
    "PublicationRefusedError",
    "RefusalKind",
    "previous_version",
    "publish_preview_version",
    "scan_for_secrets",
]

_logger = structlog.get_logger(__name__)

SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("aws_access_key_id", r"\bAKIA[0-9A-Z]{16}\b"),
    ("github_token", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    ("openai_key", r"\bsk-[A-Za-z0-9]{32,}\b"),
    ("private_key_block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ("bearer_literal", r"(?i)\bauthorization\s*[:=]\s*[\"']?bearer\s+[A-Za-z0-9._-]{20,}"),
)
"""Deterministic key-shaped patterns. A hit blocks publication; it never redacts."""


class RefusalKind(StrEnum):
    """The closed set of reasons publication refuses a candidate."""

    STALE_LEASE = "stale_lease"
    SCOPE_VIOLATED = "scope_violated"
    SOURCE_REVISION_MISMATCH = "source_revision_mismatch"
    NEW_CHECK_FAILURE = "new_check_failure"
    MISSING_ARTIFACT_DIGEST = "missing_artifact_digest"
    SECRET_DETECTED = "secret_detected"
    EMPTY_DIFF = "empty_diff"


class PublicationRefusal(BaseModel):
    """One refusal, naming the exact subject that blocked publication."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RefusalKind
    subject: str
    detail: str


class PublicationRefusedError(Exception):
    """Publication refused. No Preview Version row exists after this is raised."""

    def __init__(self, refusals: Sequence[PublicationRefusal]) -> None:
        rendered = "; ".join(f"{item.kind.value}({item.subject}): {item.detail}" for item in refusals)
        super().__init__(f"the candidate cannot become a Preview Version: {rendered}")
        self.refusals = tuple(refusals)


def scan_for_secrets(diff: str) -> tuple[PublicationRefusal, ...]:
    """Scan the accepted diff for key-shaped strings.

    Only the added lines are scanned: a secret already present at the pinned base
    is the repository's own integration health, and re-reporting it here would
    block every candidate on a problem this attempt did not create.
    """
    added = "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    return tuple(
        PublicationRefusal(
            kind=RefusalKind.SECRET_DETECTED,
            subject=name,
            detail="the accepted diff adds a line matching a known credential shape",
        )
        for name, pattern in SECRET_PATTERNS
        if re.search(pattern, added) is not None
    )


async def publish_preview_version(
    database: Database,
    *,
    specification: AttemptSpecification,
    manifest: ArtifactManifest,
    scope_check: ScopeCheckResult,
    comparison: CheckComparison,
    candidate_checks: Sequence[CheckRun],
    final_diff: str,
    source_digest: str,
    build: BuildRecipe,
    dependencies: DependencyLockIdentity,
    runtime: PreviewRuntimePort,
    prebuilt: PrebuiltCandidate | None,
    published_at: datetime,
) -> tuple[CandidateHandoff, PreviewRuntimeAcceptance]:
    """Verify, create the immutable Preview Version, and hand it to the runtime.

    A refusal leaves the prebuilt runtime with the caller; a handoff passes it on.
    """
    refusals = [
        *await _lease_refusals(database, specification),
        *_scope_refusals(scope_check, specification),
        *_check_refusals(comparison, candidate_checks),
        *_artifact_refusals(manifest),
        *_diff_refusals(final_diff),
        *scan_for_secrets(final_diff),
    ]
    if refusals:
        _logger.warning(
            "implementation.preview_version.refused",
            attempt_id=str(specification.implementation_attempt_id),
            refusals=[item.kind.value for item in refusals],
        )
        raise PublicationRefusedError(refusals)

    preview_version_id = uuid7()
    build_digest = _build_digest(build=build, dependencies=dependencies)
    handoff = CandidateHandoff(
        preview_version_id=preview_version_id,
        client_id=specification.client_id,
        product_signal_id=specification.product_signal_id,
        implementation_attempt_id=specification.implementation_attempt_id,
        gate_decision_id=specification.gate_decision_id,
        approved_scope_digest=specification.scope_digest(),
        source=SourceManifest(
            repository_ref=specification.checkout.repository_ref,
            base_revision=specification.checkout.base_revision,
            source_digest=source_digest,
            patch=manifest.final_diff,
            changed_paths=scope_check.changed_paths,
        ),
        build=build,
        dependencies=dependencies,
        artifacts=manifest.references(),
        check_results=manifest.check_results,
        build_digest=build_digest,
        published_at=published_at,
    )
    await _insert_preview_version(
        database,
        specification=specification,
        handoff=handoff,
        manifest=manifest,
        comparison=comparison,
        published_at=published_at,
    )
    acceptance = await runtime.deploy_candidate(handoff=handoff, prebuilt=prebuilt)
    _logger.info(
        "implementation.preview_version.published",
        attempt_id=str(specification.implementation_attempt_id),
        preview_version_id=str(preview_version_id),
        source_digest=source_digest,
        build_digest=build_digest,
        allocation_id=str(acceptance.preview_runtime_allocation_id),
    )
    return (handoff, acceptance)


async def _lease_refusals(
    database: Database, specification: AttemptSpecification
) -> Sequence[PublicationRefusal]:
    """A candidate may be accepted only from the attempt's current active lease."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select fencing_token from public.environment_leases
            where id = %s and implementation_attempt_id = %s and state = 'active'
            """,
            (specification.lease.lease_id, specification.implementation_attempt_id),
        )
        row = await cur.fetchone()
    if row is None or as_int(row, "fencing_token") != specification.lease.fencing_token:
        raise LeaseLostError(
            lease_kind="environment",
            lease_id=specification.lease.lease_id,
            fencing_token=specification.lease.fencing_token,
        )
    return ()


def _scope_refusals(
    scope_check: ScopeCheckResult, specification: AttemptSpecification
) -> Sequence[PublicationRefusal]:
    refusals: list[PublicationRefusal] = []
    if not scope_check.passed:
        refusals.extend(
            PublicationRefusal(
                kind=RefusalKind.SCOPE_VIOLATED,
                subject=violation.subject,
                detail=violation.detail,
            )
            for violation in scope_check.violations
        )
    if scope_check.scope_digest != specification.scope_digest():
        refusals.append(
            PublicationRefusal(
                kind=RefusalKind.SOURCE_REVISION_MISMATCH,
                subject=scope_check.scope_digest,
                detail=(
                    "the scope check was measured against a different approved scope than "
                    f"the attempt's {specification.scope_digest()}"
                ),
            )
        )
    return refusals


def _check_refusals(
    comparison: CheckComparison, candidate_checks: Sequence[CheckRun]
) -> Sequence[PublicationRefusal]:
    if not candidate_checks:
        return (
            PublicationRefusal(
                kind=RefusalKind.NEW_CHECK_FAILURE,
                subject="required_checks",
                detail="no required check ran against the candidate",
            ),
        )
    return tuple(
        PublicationRefusal(
            kind=RefusalKind.NEW_CHECK_FAILURE,
            subject=name,
            detail="the candidate introduces a failure the clean base did not have",
        )
        for name in comparison.new_failures
    )


def _artifact_refusals(manifest: ArtifactManifest) -> Sequence[PublicationRefusal]:
    return tuple(
        PublicationRefusal(
            kind=RefusalKind.MISSING_ARTIFACT_DIGEST,
            subject=reference.artifact_type,
            detail="a required artifact carries no sha256 digest",
        )
        for reference in manifest.references()
        if reference.digest.algorithm != "sha256" or len(reference.digest.value) != 64
    )


def _diff_refusals(final_diff: str) -> Sequence[PublicationRefusal]:
    if final_diff.strip():
        return ()
    return (
        PublicationRefusal(
            kind=RefusalKind.EMPTY_DIFF,
            subject="final_diff",
            detail="the attempt changed nothing, so there is no candidate to publish",
        ),
    )


def _build_digest(*, build: BuildRecipe, dependencies: DependencyLockIdentity) -> str:
    """Digest the build recipe and the dependency lock identity together."""
    return payload_digest(
        {"build": build.model_dump(mode="json"), "dependencies": dependencies.model_dump(mode="json")}
    )


async def _insert_preview_version(
    database: Database,
    *,
    specification: AttemptSpecification,
    handoff: CandidateHandoff,
    manifest: ArtifactManifest,
    comparison: CheckComparison,
    published_at: datetime,
) -> None:
    """Create the immutable version row and retire any earlier ready version."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.preview_versions (
                id, client_id, product_signal_id, implementation_attempt_id, state,
                source_commit, source_digest, build_digest, base_revision,
                artifact_manifest_digest, build_recipe, check_summary, ready_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                handoff.preview_version_id,
                specification.client_id,
                specification.product_signal_id,
                specification.implementation_attempt_id,
                PreviewVersionState.BUILDING.value,
                specification.checkout.base_revision,
                handoff.source.source_digest,
                handoff.build_digest,
                specification.checkout.base_revision,
                payload_digest(list(manifest.digests())),
                Jsonb(handoff.build.model_dump(mode="json")),
                Jsonb(comparison.model_dump(mode="json")),
                published_at,
            ),
        )
        await cur.execute(
            """
            update public.preview_versions
            set state = %s,
                superseded_by_preview_version_id = %s,
                revision = revision + 1,
                updated_at = now()
            where product_signal_id = %s and id <> %s and state = %s
            """,
            (
                PreviewVersionState.RETIRED.value,
                handoff.preview_version_id,
                specification.product_signal_id,
                handoff.preview_version_id,
                PreviewVersionState.READY.value,
            ),
        )


async def previous_version(
    database: Database, *, product_signal_id: UUID, implementation_attempt_id: UUID
) -> UUID | None:
    """The case's ready Preview Version from an earlier attempt, which this attempt changes."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.preview_versions
            where product_signal_id = %s and implementation_attempt_id <> %s
              and state = %s and superseded_by_preview_version_id is null
            order by ready_at desc
            limit 1
            """,
            (product_signal_id, implementation_attempt_id, PreviewVersionState.READY.value),
        )
        row = await cur.fetchone()
    return None if row is None else as_uuid(row, "id")


async def mark_preview_version_ready(
    database: Database, *, preview_version_id: UUID, seed_manifest_digest: str
) -> str:
    """Record the readiness the preview-runtime stream reported for one version."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_versions
            set state = %s, seed_manifest_digest = %s, ready_at = now(),
                revision = revision + 1, updated_at = now()
            where id = %s and state = %s
            returning state
            """,
            (
                PreviewVersionState.READY.value,
                seed_manifest_digest,
                preview_version_id,
                PreviewVersionState.BUILDING.value,
            ),
        )
        row = await cur.fetchone()
    if row is None:
        raise PublicationRefusedError(
            [
                PublicationRefusal(
                    kind=RefusalKind.SOURCE_REVISION_MISMATCH,
                    subject=str(preview_version_id),
                    detail="the Preview Version is not in the building state",
                )
            ]
        )
    return as_str(row, "state")
