-- A Case File narrative is drafted by a model, or derived from the evidence alone.
--
-- Case File synthesis moved from Claude Opus 5.5 to GPT-6.1 Sol. The source records
-- that a model drafted the prose, not which vendor: the model configuration is
-- recorded with the Job, so the next model change needs no migration.

alter table public.case_files
  drop constraint case_files_narrative_source_values;

update public.case_files
set narrative_source = 'model_drafted'
where narrative_source = 'claude';

alter table public.case_files
  add constraint case_files_narrative_source_values
  check (narrative_source is null or narrative_source in ('model_drafted', 'evidence_derived'));
