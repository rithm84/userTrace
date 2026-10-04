"""Accepting an Implementation Attempt's candidate into a fresh preview runtime.

This is the preview area's side of `app.implementation.ports.PreviewRuntimePort`.
The handoff carries content-addressed artifact references, never workspace access,
so the candidate's files are read back from the artifact store and refused unless
their bytes match the recorded digest. They are frozen on the Preview Version as
its artifact before anything is built from them, and the candidate is then
deployed exactly as `app.preview.candidate` specifies: a fresh runtime from the
pinned template, its own dataset, and a route advance only after full readiness.

While the workspace runs the required checks, the same files can be built into a
candidate runtime ahead of time (`prebuild_candidate`). The deployer keeps that
runtime and its secrets to itself and hands the attempt only a reference; the
deployment adopts it when the artifact's digests match, and it is destroyed when
the checks fail.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from app.db.pool import Database
from app.errors import GuardRejectedError
from app.implementation.artifact_store import ArtifactStore
from app.implementation.artifacts import CANDIDATE_FILES_ARTIFACT
from app.implementation.ports import (
    CandidateHandoff,
    PrebuiltCandidate,
    PreviewRuntimeAcceptance,
)
from app.logging import get_logger
from app.preview.artifacts import patch_digest_for, record_artifact
from app.preview.candidate import (
    BuiltCandidateRuntime,
    build_candidate_runtime,
    deploy_candidate,
)
from app.preview.jobs import PreviewSession
from app.preview.records import CandidateArtifact, CandidateFile
from app.preview.template import load_current_template, load_recipe

__all__ = ["PreviewCandidateDeployer"]

_log = get_logger(__name__)


class PreviewCandidateDeployer:
    """Deploys one accepted candidate for the Interview Session that will validate it."""

    def __init__(
        self,
        *,
        database: Database,
        artifacts: ArtifactStore,
        session: PreviewSession,
        interview_session_id: UUID,
    ) -> None:
        self._database = database
        self._artifacts = artifacts
        self._session = session
        self._interview_session_id = interview_session_id
        self._prebuilt: dict[str, BuiltCandidateRuntime] = {}

    async def prebuild_candidate(
        self, *, client_id: UUID, files: Mapping[str, str], build_command: str
    ) -> PrebuiltCandidate:
        """Build a candidate runtime from staged files while the workspace checks them."""
        template = await load_current_template(self._database, client_id=client_id)
        recipe = await load_recipe(self._database, template_id=template.id)
        built = await build_candidate_runtime(
            provider=self._session.provider,
            template=template,
            recipe=recipe,
            files=_files(files),
            build_commands=(build_command,),
            activation=self._session.activation,
        )
        self._prebuilt[built.handle.runtime_ref] = built
        _log.info("preview_candidate_prebuilt", runtime_ref=built.handle.runtime_ref)
        return PrebuiltCandidate(
            runtime_ref=built.handle.runtime_ref, file_digests=built.file_digests
        )

    async def discard_prebuilt(self, prebuilt: PrebuiltCandidate) -> None:
        """Destroy a prebuilt runtime no deployment will adopt."""
        built = self._prebuilt.pop(prebuilt.runtime_ref)
        await self._session.provider.delete_runtime(handle=built.handle)
        _log.info("preview_candidate_prebuild_discarded", runtime_ref=prebuilt.runtime_ref)

    async def deploy_candidate(
        self, *, handoff: CandidateHandoff, prebuilt: PrebuiltCandidate | None
    ) -> PreviewRuntimeAcceptance:
        """Freeze the candidate's artifact and deploy it; advance the route once ready."""
        template = await load_current_template(self._database, client_id=handoff.client_id)
        recipe = await load_recipe(self._database, template_id=template.id)
        files = await self._candidate_files(handoff)
        digests = {candidate.path: candidate.digest for candidate in files}
        artifact = CandidateArtifact(
            preview_version_id=handoff.preview_version_id,
            client_id=handoff.client_id,
            product_signal_id=handoff.product_signal_id,
            implementation_attempt_id=handoff.implementation_attempt_id,
            source_commit=handoff.source.base_revision,
            source_digest=handoff.source.source_digest,
            build_digest=handoff.build_digest,
            base_revision=handoff.source.base_revision,
            patch_digest=patch_digest_for(digests),
            files=files,
            source_manifest=digests,
            build_recipe=(handoff.build.build_command,),
            migrations=(),
            seed_manifest_digest=template.seed.manifest_digest,
        )
        await record_artifact(self._database, artifact=artifact)
        deployed = await deploy_candidate(
            self._database,
            provider=self._session.provider,
            client=self._session.http,
            settings=self._session.settings,
            template=template,
            recipe=recipe,
            artifact=artifact,
            interview_session_id=self._interview_session_id,
            activation=self._session.activation,
            observation_port=self._session.observation,
            built=None if prebuilt is None else self._prebuilt.pop(prebuilt.runtime_ref),
        )
        _log.info(
            "preview_candidate_accepted",
            preview_version_id=str(handoff.preview_version_id),
            preview_runtime_allocation_id=str(deployed.preview_runtime_allocation_id),
            route_revision=deployed.route.route_revision,
        )
        return PreviewRuntimeAcceptance(
            preview_version_id=handoff.preview_version_id,
            preview_runtime_allocation_id=deployed.preview_runtime_allocation_id,
            accepted_at=datetime.now(tz=UTC),
        )

    async def _candidate_files(self, handoff: CandidateHandoff) -> tuple[CandidateFile, ...]:
        """Read the candidate-files artifact the handoff binds, verified by digest."""
        references = [
            reference
            for reference in handoff.artifacts
            if reference.artifact_type == CANDIDATE_FILES_ARTIFACT
        ]
        if len(references) != 1:
            raise GuardRejectedError(
                machine="preview_version",
                trigger="accept_candidate",
                reason=(
                    f"the handoff binds {len(references)} candidate-files artifacts; "
                    "exactly one is required"
                ),
            )
        body = await self._artifacts.read(client_id=handoff.client_id, reference=references[0])
        contents = json.loads(body)
        if not isinstance(contents, dict):
            raise TypeError("the candidate-files artifact is not a JSON object")
        return _files({str(path): str(content) for path, content in contents.items()})


def _files(contents: Mapping[str, str]) -> tuple[CandidateFile, ...]:
    """The candidate's files in path order, each with the digest of its exact bytes."""
    return tuple(
        CandidateFile(
            path=path,
            content=content,
            digest=f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}",
        )
        for path, content in sorted(contents.items())
    )
