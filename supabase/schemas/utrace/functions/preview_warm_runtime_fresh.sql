CREATE OR REPLACE FUNCTION utrace.preview_warm_runtime_fresh (
  p_runtime_expires_at timestamp with time zone
)
  RETURNS boolean
  LANGUAGE sql
  STABLE
  SET search_path TO ''
  AS $function$
  select p_runtime_expires_at is not null
     and p_runtime_expires_at > now() + interval '20 minutes';
$function$;

REVOKE ALL ON FUNCTION "utrace"."preview_warm_runtime_fresh"(timestamp WITH time zone) FROM PUBLIC;
