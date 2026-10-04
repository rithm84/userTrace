CREATE OR REPLACE FUNCTION utrace.next_fencing_token()
  RETURNS bigint
  LANGUAGE sql
  SET search_path TO ''
  AS $function$
  select nextval('utrace.fencing_token_seq');
$function$;

REVOKE ALL ON FUNCTION "utrace"."next_fencing_token"() FROM PUBLIC;
