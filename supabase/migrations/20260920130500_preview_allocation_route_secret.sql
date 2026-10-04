-- The route-context signing secret each runtime was created with.
--
-- Contract: docs/arch-components/preview-front-door.md#vercel-to-sandbox-request-path
--
-- The preview runtime verifies `x-utrace-route-context` against the single
-- secret injected into it at sandbox creation, so the secret belongs to the
-- runtime and every route revision pointing at that runtime carries a copy. A
-- context minted for a revision that pointed at a different runtime therefore
-- fails that runtime's signature check outright.

alter table public.preview_runtime_allocations
  add column route_context_signing_secret text;
