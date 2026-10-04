-- The preview entry token the front door attaches to proxied requests.
--
-- Contract: docs/arch-components/telemetry-and-evidence.md
--
-- The telemetry-ingestion stream issues it when a runtime's Server SDK
-- activates. It belongs to the runtime rather than to a route revision, so every
-- revision pointing at that runtime resolves the same token and a later
-- activation is visible to the next request without committing a new revision.

alter table public.preview_runtime_allocations
  add column preview_entry_token text;
