"""The durable rows the live preview journey needs before it can walk the path.

The journey's own file is the journey. The client release both SDKs are pinned
to, the Preview Version being published, and the frozen candidate artifact are
built here so the test reads as the path it walks. The processes it needs come
from ``conftest.py``.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

import pytest
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    ImplementationAttemptState,
    PreviewVersionState,
)

from app.db.pool import Database
from app.ids import uuid7
from app.preview.artifacts import patch_digest_for
from app.preview.config import TemplateBuildSettings
from app.preview.recipe import TemplateRecipe
from app.preview.records import CandidateArtifact, CandidateFile, RuntimeTemplate
from app.preview.runtime import PreviewRuntimeProvider
from tests.factories import CaseFixture, create_gate_decision
from tests.helpers import policy_digest
from tests.ingestion.keys import SigningKeys
from tests.ingestion.server_sdk_harness import sign_server_manifest

__all__ = [
    "MARKER",
    "ORIGIN_SUFFIX",
    "artifact_for",
    "digest_of",
    "publish_release",
    "require_committed_template",
    "start_preview_version",
]

#: The uTrace-owned preview apex every session origin is a subdomain of.
ORIGIN_SUFFIX = "preview.utrace.dev"

MARKER = "Saved Conversion Views candidate marker"

_MARKER_PAGE = (
    "export default function CandidateMarkerPage() {\n"
    f"  return <main>{MARKER}</main>;\n"
    "}\n"
)

_MARKER_MIGRATION = (
    "-- Candidate-only migration: it runs against the candidate database alone.\n"
    "create table if not exists saved_conversion_views (\n"
    "  id uuid primary key,\n"
    "  name text not null\n"
    ");\n"
)


def digest_of(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


async def require_committed_template(
    *,
    provider: PreviewRuntimeProvider,
    recipe: TemplateRecipe,
    build_settings: TemplateBuildSettings,
    template_snapshot_id: str,
    template_image: str,
) -> None:
    """Fail unless the operator-supplied template pins exist and name this recipe's image.

    The journey pins a template straight at the snapshot and image
    ``scripts/build_preview_template.py`` printed rather than building one, so this
    is what ties the runtime it creates to the recipe the control plane signs route
    contexts and activations for. A snapshot left over from another recipe would
    answer health and then refuse every routed request with no cause attached.
    """
    repository = f"{build_settings.image_namespace}/{recipe.image_repository}@sha256:"
    if not template_image.startswith(repository):
        raise AssertionError(
            f"UTRACE_PREVIEW_TEMPLATE_IMAGE={template_image} is not a digest-pinned image "
            f"of {repository.removesuffix('@sha256:')}; rebuild the template and repin it"
        )
    if not await provider.snapshot_exists(snapshot_id=template_snapshot_id):
        raise AssertionError(
            f"UTRACE_PREVIEW_TEMPLATE_SNAPSHOT_ID={template_snapshot_id} does not exist; "
            f"build recipe {recipe.template_id} revision {recipe.template_revision} and repin it"
        )


def artifact_for(
    *,
    case: CaseFixture,
    preview_version_id: UUID,
    attempt_id: UUID,
    template: RuntimeTemplate,
) -> CandidateArtifact:
    files = (
        CandidateFile(
            path="src/app/candidate-marker/page.tsx",
            content=_MARKER_PAGE,
            digest=digest_of(_MARKER_PAGE),
        ),
        CandidateFile(
            path="db/candidate/0001_saved_conversion_views.sql",
            content=_MARKER_MIGRATION,
            digest=digest_of(_MARKER_MIGRATION),
        ),
    )
    return CandidateArtifact(
        preview_version_id=preview_version_id,
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        implementation_attempt_id=attempt_id,
        source_commit="live-test-candidate",
        source_digest=digest_of(_MARKER_PAGE),
        build_digest=digest_of(_MARKER_PAGE + _MARKER_MIGRATION),
        base_revision="live-test-base",
        patch_digest=patch_digest_for(
            {candidate.path: candidate.digest for candidate in files}
        ),
        files=files,
        source_manifest={"src/app/candidate-marker/page.tsx": digest_of(_MARKER_PAGE)},
        build_recipe=("npm run build",),
        migrations=("db/candidate/0001_saved_conversion_views.sql",),
        seed_manifest_digest=template.seed.manifest_digest,
    )


async def publish_release(
    database: Database,
    *,
    case: CaseFixture,
    build_settings: TemplateBuildSettings,
    keys: SigningKeys,
    authorizing_auth_user_id: UUID,
) -> UUID:
    """Publish the client release both SDKs are pinned to, and pin it.

    Both manifests are the fixture repository's own compiled output, because a
    manifest is the client's declaration of what its release instruments. Only
    the signature is this run's: uTrace holds the release keys, so the journey
    signs with the ephemeral key the control plane was configured to trust.
    """
    compiled = build_settings.fixture_repository_path / "build" / "utrace"
    annotation_path = compiled / "annotation-manifest.json"
    server_path = compiled / "server-manifest.json"
    if not annotation_path.is_file() or not server_path.is_file():
        pytest.skip(
            f"{compiled} holds no compiled manifests; run `npm run utrace:manifest` "
            "in the Shopalytics checkout first"
        )
    annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
    server = json.loads(server_path.read_text(encoding="utf-8"))
    # The compiled manifest names the fixture's own pinned client; this run's
    # client is a fresh row, and the activation refuses a manifest for another.
    server["client_id"] = str(case.client_id)

    release_id = uuid7()
    policy = {
        "live_implementation_allowed": True,
        "annotation_manifest": annotation,
        "server_instrumentation_manifest": sign_server_manifest(server, keys),
    }
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.configuration_releases (
                id, client_id, release_number, policy, digest,
                authorized_by_auth_user_id
            )
            values (%s, %s, 2, %s, %s, %s)
            """,
            (
                release_id,
                case.client_id,
                Jsonb(policy),
                policy_digest(policy),
                authorizing_auth_user_id,
            ),
        )
        await cur.execute(
            "update public.product_signals set pinned_configuration_release_id = %s "
            "where id = %s",
            (release_id, case.product_signal_id),
        )
    return release_id


async def start_preview_version(
    database: Database, *, case: CaseFixture
) -> tuple[UUID, UUID]:
    """Insert one building Preview Version and the attempt it belongs to."""
    gate_id = await create_gate_decision(
        database,
        case=case,
        decision="approved_live",
        classification_revision=1,
        blocker_reason=None,
    )
    attempt_id = uuid7()
    version_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.implementation_attempts (
                id, client_id, product_signal_id, gate_decision_id, state,
                repository_ref, base_revision, consumed_budgets
            )
            values (%s, %s, %s, %s, %s, 'ShopalyticsStore/shopalytics', 'live-test-base', %s)
            """,
            (
                attempt_id,
                case.client_id,
                case.product_signal_id,
                gate_id,
                ImplementationAttemptState.PUBLISHING_CANDIDATE.value,
                Jsonb({}),
            ),
        )
        await cur.execute(
            """
            insert into public.preview_versions (
                id, client_id, product_signal_id, implementation_attempt_id, state,
                source_commit, source_digest, build_digest, base_revision
            )
            values (%s, %s, %s, %s, %s, 'live-test-candidate', %s, %s, 'live-test-base')
            """,
            (
                version_id,
                case.client_id,
                case.product_signal_id,
                attempt_id,
                PreviewVersionState.BUILDING.value,
                digest_of(_MARKER_PAGE),
                digest_of(_MARKER_PAGE + _MARKER_MIGRATION),
            ),
        )
    return version_id, attempt_id
