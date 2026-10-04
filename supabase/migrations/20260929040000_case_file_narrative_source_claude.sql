-- A Case File narrative is drafted by Claude, or derived from the evidence alone.
--
-- The demo moved Case File synthesis from Sol to Claude Opus 5.5, and the
-- drafter records `claude`; the check still allowed only `sol`, so every drafted
-- Case File failed to seal.

alter table public.case_files
  drop constraint case_files_narrative_source_values;

alter table public.case_files
  add constraint case_files_narrative_source_values
  check (narrative_source is null or narrative_source in ('claude', 'evidence_derived'));
