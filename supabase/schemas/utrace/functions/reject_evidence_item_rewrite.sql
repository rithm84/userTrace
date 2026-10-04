CREATE OR REPLACE FUNCTION utrace.reject_evidence_item_rewrite()
  RETURNS TRIGGER
  LANGUAGE plpgsql
  AS $function$
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
$function$;

REVOKE ALL ON FUNCTION "utrace"."reject_evidence_item_rewrite"() FROM PUBLIC;
