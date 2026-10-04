"""The frozen Preview Version artifact.

A Preview Version is immutable. Its artifact — the files the approved patch
installs, the checked build recipe that produces the candidate, the migrations
that run only against the candidate's own database, and the seed identity it must
install — is written once, before anything is built from it. A correction is a new
gate-approved attempt with its own Preview Version, never an edit of this one.

Freezing the artifact is also what makes an expired candidate rebuildable: the
runtime bytes expire under retention policy, but the artifact and the seed
manifest do not, so the exact same candidate can be reconstructed and validation
evidence that cites it stays admissible.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from uuid import UUID

from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import PreviewVersionState

from app.db.pool import Database
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.preview.records import CandidateArtifact

__all__ = [
    "fail_preview_version",
    "load_artifact",
    "patch_digest_for",
    "publish_preview_version",
    "record_artifact",
    "reopen_for_rebuild",
]


def patch_digest_for(artifact_files: Mapping[str, str]) -> str:
    """Return the digest that identifies one patch by the files it installs."""
    joined = "\n".join(
        f"{path}\n{digest}" for path, digest in sorted(artifact_files.items())
    )
    return f"sha256:{hashlib.sha256(joined.encode('utf-8')).hexdigest()}"


async def record_artifact(database: Database, *, artifact: CandidateArtifact) -> None:
    """Freeze the artifact on its Preview Version before anything is built from it."""
    expected = patch_digest_for(
        {candidate.path: candidate.digest for candidate in artifact.files}
    )
    if expected != artifact.patch_digest:
        raise GuardRejectedError(
            machine="preview_version",
            trigger="record_artifact",
            reason=(
                f"the artifact's patch digest {artifact.patch_digest} does not "
                f"describe the files it carries ({expected})"
            ),
        )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_versions
            set source_manifest = %s, patch_digest = %s, patch_text = %s,
                build_recipe = %s, migrations = %s, artifact_recorded_at = now(),
                seed_manifest_digest = %s, revision = revision + 1, updated_at = now()
            where id = %s and state = %s and artifact_recorded_at is null
            returning id
            """,
            (
                Jsonb(dict(artifact.source_manifest)),
                artifact.patch_digest,
                Jsonb(
                    [candidate.model_dump(mode="json") for candidate in artifact.files]
                ),
                Jsonb(list(artifact.build_recipe)),
                Jsonb(list(artifact.migrations)),
                artifact.seed_manifest_digest,
                artifact.preview_version_id,
                PreviewVersionState.BUILDING.value,
            ),
        )
        row = await cur.fetchone()
    if row is None:
        raise GuardRejectedError(
            machine="preview_version",
            trigger="record_artifact",
            reason=(
                "the Preview Version is not building or already carries a frozen "
                "artifact; a correction is a new Preview Version"
            ),
        )


async def load_artifact(
    database: Database, *, preview_version_id: UUID
) -> CandidateArtifact:
    """Re-read one Preview Version's frozen artifact for an exact rebuild."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, client_id, product_signal_id, implementation_attempt_id,
                   source_commit, source_digest, build_digest, base_revision,
                   patch_digest, patch_text, source_manifest, build_recipe,
                   migrations, seed_manifest_digest
            from public.preview_versions where id = %s
            """,
            (preview_version_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="preview_versions", row_id=preview_version_id)
    patch_text = row["patch_text"]
    if patch_text is None:
        raise GuardRejectedError(
            machine="preview_version",
            trigger="load_artifact",
            reason="the Preview Version carries no frozen artifact",
        )
    if not isinstance(patch_text, str):
        raise TypeError("preview_versions.patch_text is not the JSON text it is stored as")
    return CandidateArtifact.model_validate(
        {
            "preview_version_id": row["id"],
            "client_id": row["client_id"],
            "product_signal_id": row["product_signal_id"],
            "implementation_attempt_id": row["implementation_attempt_id"],
            "source_commit": row["source_commit"],
            "source_digest": row["source_digest"],
            "build_digest": row["build_digest"],
            "base_revision": row["base_revision"],
            "patch_digest": row["patch_digest"],
            "files": json.loads(patch_text),
            "source_manifest": row["source_manifest"],
            "build_recipe": row["build_recipe"],
            "migrations": row["migrations"],
            "seed_manifest_digest": row["seed_manifest_digest"],
        }
    )


async def publish_preview_version(
    database: Database, *, preview_version_id: UUID
) -> None:
    """Mark the Preview Version ready once its candidate carries traffic."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_versions
            set state = %s, ready_at = now(), readiness_recorded_at = now(),
                revision = revision + 1, updated_at = now()
            where id = %s and state = %s
            returning id
            """,
            (
                PreviewVersionState.READY.value,
                preview_version_id,
                PreviewVersionState.BUILDING.value,
            ),
        )
        row = await cur.fetchone()
    if row is None:
        raise GuardRejectedError(
            machine="preview_version",
            trigger="publish",
            reason="the Preview Version is not building, so it cannot become ready",
        )


async def fail_preview_version(
    database: Database, *, preview_version_id: UUID, reason: str
) -> None:
    """Record the Preview Version as failed without erasing what it tried."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_versions
            set state = %s, failure_reason = %s, revision = revision + 1,
                updated_at = now()
            where id = %s and state = %s
            """,
            (
                PreviewVersionState.FAILED.value,
                reason,
                preview_version_id,
                PreviewVersionState.BUILDING.value,
            ),
        )


async def reopen_for_rebuild(
    database: Database, *, preview_version_id: UUID
) -> None:
    """Return a ready Preview Version to building so its candidate can be rebuilt.

    The artifact is not touched: a rebuild reproduces the same bytes from the same
    frozen inputs, so the Preview Version keeps its identity and the validation
    evidence that cites it stays admissible.
    """
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.preview_versions
            set state = %s, ready_at = null, revision = revision + 1, updated_at = now()
            where id = %s and state = %s
            """,
            (
                PreviewVersionState.BUILDING.value,
                preview_version_id,
                PreviewVersionState.READY.value,
            ),
        )
