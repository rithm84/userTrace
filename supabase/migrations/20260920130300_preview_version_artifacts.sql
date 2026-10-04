-- Candidate artifact identity on a Preview Version, and the session's current route.
--
-- Contracts: docs/arch-components/preview-build.md#build-once-run-the-same-bytes
--            docs/arch-components/preview-build.md#migrations
--
-- The implementation stream publishes a source manifest, a patch, a build recipe,
-- and any candidate-only migrations. Candidate deployment reads exactly those
-- pinned fields and nothing else, so an idle candidate can be rebuilt from the
-- artifact and the seed manifest alone.

alter table public.preview_versions
  add column source_manifest jsonb,
  add column patch_digest text,
  add column patch_text text,
  add column if not exists build_recipe jsonb,
  add column migrations jsonb,
  add column artifact_recorded_at timestamptz,
  add column readiness_recorded_at timestamptz,
  add column failure_reason text;

-- The route the front door currently resolves for this session. Advancing it is
-- one transaction: supersede the old revision, commit the new one, point here.
alter table public.interview_sessions
  add column current_preview_route_id uuid references public.preview_routes (id);
