-- The private Storage buckets, which are rows in storage.buckets and so cannot be
-- declared in supabase/schemas. Neither is ever public.
--
-- utrace-telemetry holds raw telemetry batches, large baselines, and promoted
-- evidence; ingestion resolves objects only through short-lived server-authorized
-- paths. utrace-artifacts holds every implementation artifact body, addressed by
-- digest, while Postgres holds the metadata.
--
-- Contracts: docs/arch-components/telemetry-and-evidence.md#persistence-and-integrity
--            docs/arch-components/data-and-schema.md#evidence-and-artifacts

insert into storage.buckets (id, name, public)
values
  ('utrace-telemetry', 'utrace-telemetry', false),
  ('utrace-artifacts', 'utrace-artifacts', false)
on conflict (id) do nothing;
