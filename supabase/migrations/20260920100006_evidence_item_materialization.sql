-- Promoted evidence items are immutable except for the artifact reference that
-- materialization attaches.
--
-- Before eligible source telemetry expires, ingestion materializes every promoted
-- item as the minimum immutable artifact needed to preserve its meaning and
-- provenance. That write sets `artifact_id` once and changes nothing else, so a
-- blanket append-only guard would block the very step that makes a citation
-- survive deletion.
--
-- Contract: docs/arch-components/telemetry-and-evidence.md#evidence-windows-and-promotion

drop trigger telemetry_evidence_items_immutable on public.telemetry_evidence_items;

create or replace function utrace.reject_evidence_item_rewrite()
returns trigger
language plpgsql
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'public.telemetry_evidence_items is append-only; DELETE is rejected'
      using errcode = 'restrict_violation';
  end if;
  if old.artifact_id is not null and new.artifact_id is distinct from old.artifact_id then
    raise exception 'a promoted evidence item already names its immutable artifact'
      using errcode = 'restrict_violation';
  end if;
  if (to_jsonb(new) - 'artifact_id') is distinct from (to_jsonb(old) - 'artifact_id') then
    raise exception
      'public.telemetry_evidence_items only accepts the artifact_id materialization write'
      using errcode = 'restrict_violation';
  end if;
  return new;
end;
$$;

revoke execute on function utrace.reject_evidence_item_rewrite() from public;

create trigger telemetry_evidence_items_materialize_only
  before update or delete on public.telemetry_evidence_items
  for each row execute function utrace.reject_evidence_item_rewrite();
