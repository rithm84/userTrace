"""Staff read routes for gate decisions, attempts, and Preview Versions.

Client staff may read what the Implementation Gate decided, what one attempt
did, and which candidate it produced. They cannot approve, resume, or edit any
of it: the only staff write in the pilot is the audited, deny-only Stop control,
which lives with the case routes.

Every route resolves an active membership for the client in the path before it
reads anything, and every projection is scoped to that client.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict

from app.auth.dependencies import CurrentDatabase, StaffOfPath
from app.db.pool import Database
from app.db.rows import as_bool, as_int, as_optional_str, as_str, as_uuid
from app.errors import AggregateNotFoundError
from app.gate.scope import ApprovedScope

__all__ = ["router"]

router = APIRouter(prefix="/v1/clients", tags=["implementation"])


class ApiModel(BaseModel):
    """Base for every response: closed to extra fields and immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class GateDecisionDetail(ApiModel):
    """One immutable gate decision and the scope its approval froze."""

    gate_decision_id: UUID
    product_signal_id: UUID
    decision: str
    blocker_reason: str | None
    classification_revision: int
    acceptance_criteria_revision: int
    approved_scope: ApprovedScope | None
    scope_digest: str | None


class CheckRunView(ApiModel):
    """One required check run, on the clean base or on the candidate."""

    phase: str
    check_name: str
    command: str
    exit_code: int
    duration_seconds: float
    cycle_index: int


class ScopeCheckView(ApiModel):
    """One diff-versus-approved-scope check."""

    cycle_index: int
    passed: bool
    scope_digest: str
    changed_paths: tuple[str, ...]


class ArtifactView(ApiModel):
    """One extracted artifact, by identity and digest. Bodies are never inlined."""

    artifact_id: UUID
    artifact_type: str
    media_type: str
    digest: str
    byte_size: int
    retention_class: str


class AttemptDetail(ApiModel):
    """One Implementation Attempt with its checks, scope checks, and artifacts."""

    implementation_attempt_id: UUID
    product_signal_id: UUID
    gate_decision_id: UUID
    state: str
    progress: str
    repository_ref: str
    base_revision: str
    outcome_reason: str | None
    check_runs: tuple[CheckRunView, ...]
    scope_checks: tuple[ScopeCheckView, ...]
    artifacts: tuple[ArtifactView, ...]


class PreviewVersionDetail(ApiModel):
    """One immutable Preview Version and the identities it binds."""

    preview_version_id: UUID
    implementation_attempt_id: UUID
    state: str
    source_commit: str
    source_digest: str
    build_digest: str
    artifact_manifest_digest: str | None
    superseded_by_preview_version_id: UUID | None


class CodeFindingView(ApiModel):
    """One exact-revision finding from the read-only pre-gate investigation."""

    finding_id: UUID
    repository_ref: str
    base_revision: str
    path: str
    first_line: int
    last_line: int
    excerpt_digest: str
    note: str


@router.get(
    "/{client_id}/gate-decisions/{gate_decision_id}", response_model=GateDecisionDetail
)
async def get_gate_decision(
    staff: StaffOfPath,
    database: CurrentDatabase,
    gate_decision_id: Annotated[UUID, Path()],
) -> GateDecisionDetail:
    """Return one gate decision and the approved scope it froze."""
    row = await _one(
        database,
        """
        select id, product_signal_id, decision, blocker_reason, classification_revision,
               acceptance_criteria_revision, approved_scope, required_checks, budgets
        from public.gate_decisions where id = %s and client_id = %s
        """,
        (gate_decision_id, staff.client_id),
        table="gate_decisions",
        row_id=gate_decision_id,
    )
    scope = _scope(row)
    return GateDecisionDetail(
        gate_decision_id=as_uuid(row, "id"),
        product_signal_id=as_uuid(row, "product_signal_id"),
        decision=as_str(row, "decision"),
        blocker_reason=as_optional_str(row, "blocker_reason"),
        classification_revision=as_int(row, "classification_revision"),
        acceptance_criteria_revision=as_int(row, "acceptance_criteria_revision"),
        approved_scope=scope,
        scope_digest=scope.digest() if scope is not None else None,
    )


@router.get(
    "/{client_id}/implementation-attempts/{attempt_id}", response_model=AttemptDetail
)
async def get_attempt(
    staff: StaffOfPath,
    database: CurrentDatabase,
    attempt_id: Annotated[UUID, Path()],
) -> AttemptDetail:
    """Return one attempt with its check runs, scope checks, and artifacts."""
    row = await _one(
        database,
        """
        select id, product_signal_id, gate_decision_id, state,
               progress, repository_ref, base_revision, outcome_reason
        from public.implementation_attempts where id = %s and client_id = %s
        """,
        (attempt_id, staff.client_id),
        table="implementation_attempts",
        row_id=attempt_id,
    )
    return AttemptDetail(
        implementation_attempt_id=as_uuid(row, "id"),
        product_signal_id=as_uuid(row, "product_signal_id"),
        gate_decision_id=as_uuid(row, "gate_decision_id"),
        state=as_str(row, "state"),
        progress=as_str(row, "progress"),
        repository_ref=as_str(row, "repository_ref"),
        base_revision=as_str(row, "base_revision"),
        outcome_reason=as_optional_str(row, "outcome_reason"),
        check_runs=await _check_runs(database, attempt_id),
        scope_checks=await _scope_checks(database, attempt_id),
        artifacts=await _artifacts(database, attempt_id),
    )


@router.get(
    "/{client_id}/preview-versions/{preview_version_id}",
    response_model=PreviewVersionDetail,
)
async def get_preview_version(
    staff: StaffOfPath,
    database: CurrentDatabase,
    preview_version_id: Annotated[UUID, Path()],
) -> PreviewVersionDetail:
    """Return one immutable Preview Version."""
    row = await _one(
        database,
        """
        select id, implementation_attempt_id, state, source_commit, source_digest,
               build_digest, artifact_manifest_digest, superseded_by_preview_version_id
        from public.preview_versions where id = %s and client_id = %s
        """,
        (preview_version_id, staff.client_id),
        table="preview_versions",
        row_id=preview_version_id,
    )
    superseded = row["superseded_by_preview_version_id"]
    return PreviewVersionDetail(
        preview_version_id=as_uuid(row, "id"),
        implementation_attempt_id=as_uuid(row, "implementation_attempt_id"),
        state=as_str(row, "state"),
        source_commit=as_str(row, "source_commit"),
        source_digest=as_str(row, "source_digest"),
        build_digest=as_str(row, "build_digest"),
        artifact_manifest_digest=as_optional_str(row, "artifact_manifest_digest"),
        superseded_by_preview_version_id=(
            as_uuid(row, "superseded_by_preview_version_id") if superseded else None
        ),
    )


@router.get(
    "/{client_id}/cases/{product_signal_id}/code-findings",
    response_model=list[CodeFindingView],
)
async def list_code_findings(
    staff: StaffOfPath,
    database: CurrentDatabase,
    product_signal_id: Annotated[UUID, Path()],
) -> list[CodeFindingView]:
    """Return the exact-revision findings the pre-gate investigation recorded."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, repository_ref, base_revision, path, first_line, last_line,
                   excerpt_digest, note
            from public.code_investigation_findings
            where product_signal_id = %s and client_id = %s
            order by path, first_line
            """,
            (product_signal_id, staff.client_id),
        )
        rows = await cur.fetchall()
    return [
        CodeFindingView(
            finding_id=as_uuid(row, "id"),
            repository_ref=as_str(row, "repository_ref"),
            base_revision=as_str(row, "base_revision"),
            path=as_str(row, "path"),
            first_line=as_int(row, "first_line"),
            last_line=as_int(row, "last_line"),
            excerpt_digest=as_str(row, "excerpt_digest"),
            note=as_str(row, "note"),
        )
        for row in rows
    ]


def _scope(row: dict[str, object]) -> ApprovedScope | None:
    """Rebuild the approved scope from its three stored columns, if it has one."""
    approved = row["approved_scope"]
    checks = row["required_checks"]
    budgets = row["budgets"]
    if not isinstance(approved, dict) or not approved:
        return None
    if not isinstance(checks, dict) or not isinstance(budgets, dict):
        return None
    return ApprovedScope.from_columns(
        approved_scope=approved, required_checks=checks, budgets=budgets
    )


async def _one(
    database: Database,
    statement: str,
    params: tuple[object, ...],
    *,
    table: str,
    row_id: UUID,
) -> dict[str, object]:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(statement, params)
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table=table, row_id=row_id)
    return dict(row)


async def _check_runs(database: Database, attempt_id: UUID) -> tuple[CheckRunView, ...]:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select phase, check_name, command, exit_code, duration_seconds, cycle_index
            from public.implementation_check_runs
            where implementation_attempt_id = %s
            order by cycle_index, phase, check_name
            """,
            (attempt_id,),
        )
        rows = await cur.fetchall()
    return tuple(
        CheckRunView(
            phase=as_str(row, "phase"),
            check_name=as_str(row, "check_name"),
            command=as_str(row, "command"),
            exit_code=as_int(row, "exit_code"),
            duration_seconds=float(str(row["duration_seconds"])),
            cycle_index=as_int(row, "cycle_index"),
        )
        for row in rows
    )


async def _scope_checks(database: Database, attempt_id: UUID) -> tuple[ScopeCheckView, ...]:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select cycle_index, passed, scope_digest, changed_paths
            from public.implementation_scope_checks
            where implementation_attempt_id = %s
            order by cycle_index
            """,
            (attempt_id,),
        )
        rows = await cur.fetchall()
    return tuple(
        ScopeCheckView(
            cycle_index=as_int(row, "cycle_index"),
            passed=as_bool(row, "passed"),
            scope_digest=as_str(row, "scope_digest"),
            changed_paths=tuple(str(path) for path in _paths(row["changed_paths"])),
        )
        for row in rows
    )


async def _artifacts(database: Database, attempt_id: UUID) -> tuple[ArtifactView, ...]:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, artifact_type, media_type, digest, byte_size, retention_class
            from public.artifacts
            where implementation_attempt_id = %s
            order by artifact_type, created_at
            """,
            (attempt_id,),
        )
        rows = await cur.fetchall()
    return tuple(
        ArtifactView(
            artifact_id=as_uuid(row, "id"),
            artifact_type=as_str(row, "artifact_type"),
            media_type=as_str(row, "media_type"),
            digest=as_str(row, "digest"),
            byte_size=as_int(row, "byte_size"),
            retention_class=as_str(row, "retention_class"),
        )
        for row in rows
    )


def _paths(value: object) -> list[object]:
    """Narrow a Postgres text[] column."""
    if not isinstance(value, list):
        raise TypeError(f"expected a text array column, got {type(value).__name__}")
    return value
