-- The private Storage bucket that holds raw telemetry batches, large baselines,
-- and promoted evidence artifacts. Ingestion resolves objects only through
-- short-lived server-authorized paths; the bucket is never public.
--
-- Contracts: docs/arch-components/telemetry-and-evidence.md#persistence-and-integrity

insert into storage.buckets (id, name, public)
values ('utrace-telemetry', 'utrace-telemetry', false)
on conflict (id) do nothing;
