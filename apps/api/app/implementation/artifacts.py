"""Artifact extraction, before the workspace is destroyed.

The implementation contract names exactly what must become durable: the final
diff, every revision patch, the file and process logs, the check results, the
inspection evidence, the approved-scope checks, the dependency manifest, and the
source and build provenance. Each one is content-addressed and bound to the
attempt and its lease.

Extraction failure blocks cleanup. This module either returns a complete
manifest or raises; a caller that does not hold one must not destroy the
workspace, because the sandbox is never the sole recovery record.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

import structlog
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict
from utrace_contracts.envelope.artifacts import ArtifactReference

from app.db.pool import Database
from app.gate.scope import ApprovedScope
from app.ids import uuid7
from app.implementation.artifact_store import ArtifactStore, digest_of
from app.implementation.checks import CheckRun
from app.implementation.scope_check import ScopeCheckResult
from app.implementation.specification import AttemptSpecification
from app.implementation.tools import ToolJournalEntry, journal_payload

__all__ = [
    "CANDIDATE_FILES_ARTIFACT",
    "ArtifactManifest",
    "AttemptEvidence",
    "CycleRecord",
    "extract_attempt_artifacts",
    "record_cycle_evidence",
    "scope_summary",
]

_logger = structlog.get_logger(__name__)

PRODUCER = "implementation_controller"
CANDIDATE_FILES_ARTIFACT = "implementation_candidate_files"
"""The artifact type holding each changed path's final content, keyed by path."""
_JSON = "application/json"
_PATCH = "text/x-patch; charset=utf-8"
_TEXT = "text/plain; charset=utf-8"


class ArtifactModel(BaseModel):
    """Base for the extraction records: immutable and closed to extra fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class CycleRecord(ArtifactModel):
    """One edit cycle: its patch, its scope check, and the checks that followed."""

    cycle_index: int
    patch: str
    scope_check: ScopeCheckResult
    check_runs: tuple[CheckRun, ...]


class AttemptEvidence(ArtifactModel):
    """Everything one attempt run produced, before any of it is stored."""

    final_diff: str
    cycles: tuple[CycleRecord, ...]
    baseline_checks: tuple[CheckRun, ...]
    tool_journal: tuple[ToolJournalEntry, ...]
    check_logs: Mapping[str, str]
    """Captured combined output, keyed by the check run's repository-relative log path."""
    inspection_evidence: tuple[Mapping[str, object], ...]
    """Files the run read at the pinned revision, with their paths and digests."""
    dependency_manifest: str
    """The candidate's dependency manifest, exactly as the workspace left it."""
    dependency_lock_digest: str
    """SHA-256 of the candidate's lockfile: the dependency identity the build pins."""
    source_provenance: Mapping[str, object]
    build_provenance: Mapping[str, object]
    candidate_files: Mapping[str, str]
    """Each changed path's final content, which a candidate runtime installs verbatim."""


class ArtifactManifest(ArtifactModel):
    """The stored, content-addressed result of one extraction."""

    manifest_id: UUID
    implementation_attempt_id: UUID
    environment_lease_id: UUID
    extracted_at: datetime
    final_diff: ArtifactReference
    revision_patches: tuple[ArtifactReference, ...]
    tool_journal: ArtifactReference
    check_results: ArtifactReference
    check_logs: tuple[ArtifactReference, ...]
    inspection_evidence: ArtifactReference
    scope_checks: ArtifactReference
    dependency_manifest: ArtifactReference
    source_provenance: ArtifactReference
    build_provenance: ArtifactReference
    candidate_files: ArtifactReference

    def references(self) -> tuple[ArtifactReference, ...]:
        """Every reference this manifest binds, in a stable order."""
        return (
            self.final_diff,
            *self.revision_patches,
            self.tool_journal,
            self.check_results,
            *self.check_logs,
            self.inspection_evidence,
            self.scope_checks,
            self.dependency_manifest,
            self.source_provenance,
            self.build_provenance,
            self.candidate_files,
        )

    def digests(self) -> tuple[str, ...]:
        """Every artifact digest, for the Preview Version's verification step."""
        return tuple(reference.digest.value for reference in self.references())


async def extract_attempt_artifacts(
    store: ArtifactStore,
    *,
    specification: AttemptSpecification,
    evidence: AttemptEvidence,
    extracted_at: datetime,
) -> ArtifactManifest:
    """Store every required artifact at once and return the manifest that binds them.

    The uploads run together. Two artifacts with the same type and body are one
    stored artifact, so they share a single upload rather than racing to create it.
    """
    stored: dict[tuple[str, str], asyncio.Task[ArtifactReference]] = {}

    async with asyncio.TaskGroup() as uploads:

        def put(
            *, artifact_type: str, media_type: str, body: bytes
        ) -> asyncio.Task[ArtifactReference]:
            key = (artifact_type, digest_of(body))
            if key not in stored:
                stored[key] = uploads.create_task(
                    store.put(
                        client_id=specification.client_id,
                        artifact_type=artifact_type,
                        media_type=media_type,
                        retention_class="client_evidence",
                        producer=PRODUCER,
                        body=body,
                        source_revision=specification.checkout.base_revision,
                        implementation_attempt_id=specification.implementation_attempt_id,
                        environment_lease_id=specification.lease.lease_id,
                        product_signal_id=specification.product_signal_id,
                    )
                )
            return stored[key]

        final_diff = put(
            artifact_type="implementation_final_diff",
            media_type=_PATCH,
            body=evidence.final_diff.encode("utf-8"),
        )
        revision_patches = [
            put(
                artifact_type="implementation_revision_patch",
                media_type=_PATCH,
                body=cycle.patch.encode("utf-8"),
            )
            for cycle in evidence.cycles
        ]
        tool_journal = put(
            artifact_type="implementation_tool_journal",
            media_type=_JSON,
            body=_json_bytes(journal_payload(evidence.tool_journal)),
        )
        check_results = put(
            artifact_type="implementation_check_results",
            media_type=_JSON,
            body=_json_bytes(
                {
                    "baseline": [run.model_dump(mode="json") for run in evidence.baseline_checks],
                    "candidate": [
                        {
                            "cycle_index": cycle.cycle_index,
                            "runs": [run.model_dump(mode="json") for run in cycle.check_runs],
                        }
                        for cycle in evidence.cycles
                    ],
                }
            ),
        )
        check_logs = [
            put(
                artifact_type="implementation_check_log",
                media_type=_TEXT,
                body=body.encode("utf-8"),
            )
            for _, body in sorted(evidence.check_logs.items())
        ]
        inspection_evidence = put(
            artifact_type="implementation_inspection_evidence",
            media_type=_JSON,
            body=_json_bytes(list(evidence.inspection_evidence)),
        )
        scope_checks = put(
            artifact_type="implementation_scope_checks",
            media_type=_JSON,
            body=_json_bytes(
                [
                    {
                        "cycle_index": cycle.cycle_index,
                        "result": cycle.scope_check.model_dump(mode="json"),
                    }
                    for cycle in evidence.cycles
                ]
            ),
        )
        dependency_manifest = put(
            artifact_type="implementation_dependency_manifest",
            media_type=_JSON,
            body=evidence.dependency_manifest.encode("utf-8"),
        )
        source_provenance = put(
            artifact_type="implementation_source_provenance",
            media_type=_JSON,
            body=_json_bytes(dict(evidence.source_provenance)),
        )
        build_provenance = put(
            artifact_type="implementation_build_provenance",
            media_type=_JSON,
            body=_json_bytes(dict(evidence.build_provenance)),
        )
        candidate_files = put(
            artifact_type=CANDIDATE_FILES_ARTIFACT,
            media_type=_JSON,
            body=_json_bytes(dict(evidence.candidate_files)),
        )

    manifest = ArtifactManifest(
        manifest_id=uuid7(),
        implementation_attempt_id=specification.implementation_attempt_id,
        environment_lease_id=specification.lease.lease_id,
        extracted_at=extracted_at,
        final_diff=final_diff.result(),
        revision_patches=tuple(task.result() for task in revision_patches),
        tool_journal=tool_journal.result(),
        check_results=check_results.result(),
        check_logs=tuple(task.result() for task in check_logs),
        inspection_evidence=inspection_evidence.result(),
        scope_checks=scope_checks.result(),
        dependency_manifest=dependency_manifest.result(),
        source_provenance=source_provenance.result(),
        build_provenance=build_provenance.result(),
        candidate_files=candidate_files.result(),
    )
    _logger.info(
        "implementation.artifacts.extracted",
        attempt_id=str(specification.implementation_attempt_id),
        lease_id=str(specification.lease.lease_id),
        artifact_count=len(manifest.references()),
    )
    return manifest


async def record_cycle_evidence(
    database: Database,
    *,
    specification: AttemptSpecification,
    cycles: Sequence[CycleRecord],
    baseline: Sequence[CheckRun],
) -> None:
    """Write the per-cycle scope checks and check runs to their durable tables."""
    async with database.transaction() as conn, conn.cursor() as cur:
        for run in baseline:
            await cur.execute(_CHECK_RUN_INSERT, _check_run_row(specification, run, 0))
        for cycle in cycles:
            await cur.execute(
                """
                insert into public.implementation_scope_checks (
                    id, client_id, implementation_attempt_id, cycle_index, passed,
                    scope_digest, changed_paths, violations
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid7(),
                    specification.client_id,
                    specification.implementation_attempt_id,
                    cycle.cycle_index,
                    cycle.scope_check.passed,
                    cycle.scope_check.scope_digest,
                    list(cycle.scope_check.changed_paths),
                    Jsonb(
                        [item.model_dump(mode="json") for item in cycle.scope_check.violations]
                    ),
                ),
            )
            for run in cycle.check_runs:
                await cur.execute(
                    _CHECK_RUN_INSERT, _check_run_row(specification, run, cycle.cycle_index)
                )


_CHECK_RUN_INSERT = """
    insert into public.implementation_check_runs (
        id, client_id, implementation_attempt_id, environment_lease_id, phase,
        check_name, command, exit_code, duration_seconds, cycle_index
    )
    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _check_run_row(
    specification: AttemptSpecification, run: CheckRun, cycle_index: int
) -> tuple[object, ...]:
    return (
        uuid7(),
        specification.client_id,
        specification.implementation_attempt_id,
        specification.lease.lease_id,
        run.phase.value,
        run.name,
        run.command,
        run.exit_code,
        run.duration_seconds,
        cycle_index,
    )


def _json_bytes(payload: object) -> bytes:
    """Serialize one artifact body deterministically, so its digest is stable."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def scope_summary(scope: ApprovedScope) -> Mapping[str, object]:
    """The frozen-scope fields the source provenance artifact records."""
    return {
        "scope_digest": scope.digest(),
        "repository_ref": scope.repository_ref,
        "base_revision": scope.base_revision,
        "permitted_paths": list(scope.permitted_paths),
        "blocked_paths": list(scope.blocked_paths),
        "dependency_policy": scope.dependency_policy.value,
    }
