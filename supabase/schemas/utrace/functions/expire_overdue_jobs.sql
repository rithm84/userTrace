CREATE OR REPLACE FUNCTION utrace.expire_overdue_jobs()
  RETURNS integer
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_count integer;
begin
  with overdue as (
    update public.jobs
    set state = 'expired',
        last_retry_advice = 'never',
        revision = revision + 1,
        updated_at = now()
    where state in ('pending', 'ready')
      and deadline_at <= now()
    returning 1
  )
  select count(*) into v_count from overdue;
  return v_count;
end;
$function$;

REVOKE ALL ON FUNCTION "utrace"."expire_overdue_jobs"() FROM PUBLIC;
