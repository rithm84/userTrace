CREATE OR REPLACE FUNCTION utrace.requeue_stale_outbox (
  p_stale_after interval
)
  RETURNS integer
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_count integer;
begin
  with stale as (
    update public.outbox
    set state = 'pending',
        enqueued_at = null,
        attempts = attempts + 1,
        available_at = now(),
        updated_at = now()
    where state = 'enqueued'
      and enqueued_at < now() - p_stale_after
    returning 1
  )
  select count(*) into v_count from stale;
  return v_count;
end;
$function$;

REVOKE ALL ON FUNCTION "utrace"."requeue_stale_outbox"(interval) FROM PUBLIC;
