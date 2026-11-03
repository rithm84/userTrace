"""Accepting an Implementation Attempt's candidate into the session's spare runtime.

This is the preview area's side of `app.implementation.ports.PreviewRuntimePort`.
The handoff carries content-addressed artifact references, never workspace access,
so the candidate's files are read back from the artifact store and refused unless
their bytes match the recorded digest. They are frozen on the Preview Version as
its artifact before anything serves them, and the candidate is then deployed
exactly as `app.preview.candidate` specifies: the session's spare runtime, its own
dataset, and a route advance only after full readiness.

While the workspace runs the candidate's checks, the same files are staged into
the spare and pass the render check there (`stage_candidate`), so an accepted
candidate only has to be proven ready and routed. The deployer keeps the spare and
its secrets to itself and hands the attempt only a reference.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from app.db.pool import Database
from app.envelope import JsonValue
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.implementation.artifact_store import ArtifactStore
from app.implementation.artifacts import CANDIDATE_FILES_ARTIFACT
from app.implementation.ports import (
    CandidateHandoff,
    PreviewRuntimeAcceptance,
    StagedCandidate,
)
from app.logging import get_logger
from app.preview.artifacts import patch_digest_for, record_artifact
from app.preview.candidate import deploy_candidate, prove_candidate, session_spare
from app.preview.jobs import PreviewSession
from app.preview.records import CandidateArtifact, CandidateFile
from app.preview.render_check import RenderCheck, added_targets, pages_for_targets
from app.preview.routing import route_for_session
from app.preview.spare import SpareRuntime, render_spare, serve_staged, stage_files
from app.preview.template import load_current_template, load_recipe

__all__ = ["PreviewCandidateDeployer", "candidate_files"]

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
        self._spares: dict[str, SpareRuntime] = {}

    async def stage_candidate(
        self,
        *,
        client_id: UUID,
        files: Mapping[str, str],
        preview_version_id: UUID | None = None,
    ) -> StagedCandidate:
        """Write files into the session's spare, serve a production build of them, and render them.

        With `preview_version_id`, a spare that renders is also proven ready for that
        version's swap, so a swap soon after needs no probes of its own.
        """
        template = await load_current_template(self._database, client_id=client_id)
        recipe = await load_recipe(self._database, template_id=template.id)
        spare = await session_spare(
            self._database,
            provider=self._session.provider,
            client=self._session.http,
            settings=self._session.settings,
            template=template,
            recipe=recipe,
            interview_session_id=self._interview_session_id,
            activation=self._session.activation,
            runtime_ref=None,
        )
        written = candidate_files(files)
        staged = await stage_files(
            self._database,
            provider=self._session.provider,
            recipe=recipe,
            spare=spare,
            files=written,
        )
        current = await route_for_session(
            self._database, interview_session_id=self._interview_session_id
        )
        if current is None:
            raise GuardRejectedError(
                machine="preview_route",
                trigger="stage_candidate",
                reason="the Interview Session has no committed route",
            )
        schema, build = await serve_staged(
            self._database,
            provider=self._session.provider,
            client=self._session.http,
            recipe=recipe,
            spare=spare,
            staged=staged,
            files=written,
            activation=self._session.activation,
            observation_port=self._session.observation,
            client_id=client_id,
            interview_session_id=self._interview_session_id,
            public_preview_host=current.public_preview_host,
        )
        expected = added_targets(files=files, originals=staged.originals)
        pages = pages_for_targets(
            targets=expected,
            annotation_manifest=await _annotation_manifest(
                self._database, interview_session_id=self._interview_session_id
            ),
        )
        async def rendered() -> RenderCheck:
            return await render_spare(
                client=self._session.http,
                recipe=recipe,
                spare=spare,
                current=current,
                pages=pages or None,
                expected_targets=expected,
            )

        async def proved() -> bool:
            if preview_version_id is None:
                return False
            report = await prove_candidate(
                self._database,
                provider=self._session.provider,
                client=self._session.http,
                settings=self._session.settings,
                template=template,
                recipe=recipe,
                spare=spare,
                interview_session_id=self._interview_session_id,
                preview_version_id=preview_version_id,
                observation_port=self._session.observation,
            )
            return report.ready

        if build.passed:
            # The render check and the readiness proof read the same served build, so
            # they run together; a proof whose render failed is never used, since no
            # version is published from files that failed a fast check.
            render, proven = await asyncio.gather(rendered(), proved())
        else:
            render = RenderCheck(
                passed=False,
                pages=(),
                expected_targets=tuple(sorted(expected)),
                missing_targets=(),
                failures=("the production build of these files failed",),
                seconds=0.0,
            )
            proven = False
        proven = proven and render.passed
        self._spares[spare.handle.runtime_ref] = spare
        return StagedCandidate(
            runtime_ref=spare.handle.runtime_ref,
            file_digests=staged.file_digests,
            render_passed=render.passed,
            render={
                **render.as_column(),
                "schema": {"mode": schema.mode.value, "digest": schema.digest},
                "proven_for": None if not proven else str(preview_version_id),
            },
            build=build.as_column(),
        )

    async def deploy_candidate(
        self, *, handoff: CandidateHandoff, staged: StagedCandidate | None
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
        spare = None if staged is None else self._spares.pop(staged.runtime_ref, None)
        if spare is None:
            spare = await session_spare(
                self._database,
                provider=self._session.provider,
                client=self._session.http,
                settings=self._session.settings,
                template=template,
                recipe=recipe,
                interview_session_id=self._interview_session_id,
                activation=self._session.activation,
                runtime_ref=None if staged is None else staged.runtime_ref,
            )
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
            spare=spare,
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
        return candidate_files({str(path): str(content) for path, content in contents.items()})


def candidate_files(contents: Mapping[str, str]) -> tuple[CandidateFile, ...]:
    """The candidate's files in path order, each with the digest of its exact bytes."""
    return tuple(
        CandidateFile(
            path=path,
            content=content,
            digest=f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}",
        )
        for path, content in sorted(contents.items())
    )


async def _annotation_manifest(
    database: Database, *, interview_session_id: UUID
) -> Mapping[str, JsonValue]:
    """The annotation manifest the session's case pins, which declares each target's pages."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select releases.policy -> 'annotation_manifest' as manifest
            from public.interview_sessions as sessions
            join public.product_signals as signals on signals.id = sessions.product_signal_id
            join public.configuration_releases as releases
              on releases.id = signals.pinned_configuration_release_id
            where sessions.id = %s
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="interview_sessions", row_id=interview_session_id)
    manifest = row["manifest"]
    return manifest if isinstance(manifest, dict) else {}
