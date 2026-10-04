CREATE OR REPLACE FUNCTION utrace.reject_mutation()
  RETURNS TRIGGER
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
begin
  raise exception
    'table %.% is append-only; % is rejected',
    tg_table_schema, tg_table_name, tg_op
    using errcode = 'restrict_violation';
end;
$function$;

REVOKE ALL ON FUNCTION "utrace"."reject_mutation"() FROM PUBLIC;
