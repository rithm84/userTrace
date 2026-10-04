CREATE OR REPLACE FUNCTION utrace.reclaim_expired_job_leases()
  RETURNS integer
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_run public.job_runs%rowtype;
  v_job public.jobs%rowtype;
  v_backoff integer;
  v_count integer := 0;
begin
  for v_run in
    select *
    from public.job_runs
    where state = 'leased'
      and lease_expires_at < now()
    order by lease_expires_at
    for update skip locked
  loop
    update public.job_runs
    set state = 'abandoned',
        finished_at = now(),
        failure_category = 'lease_expired',
        retry_advice = 'after_delay',
        revision = revision + 1,
        updated_at = now()
    where id = v_run.id;

    select * into v_job from public.jobs where id = v_run.job_id for update;

    v_backoff := least(
      v_job.backoff_max_seconds,
      v_job.backoff_base_seconds * power(2, greatest(v_job.run_count - 1, 0))::integer
    );

    if v_job.run_count >= v_job.max_runs or v_job.deadline_at <= now() then
      update public.jobs
      set state = 'failed',
          last_failure_category = 'lease_expired',
          last_retry_advice = 'never',
          revision = revision + 1,
          updated_at = now()
      where id = v_job.id;
    else
      update public.jobs
      set state = 'ready',
          not_before = now() + make_interval(secs => v_backoff),
          last_failure_category = 'lease_expired',
          last_retry_advice = 'after_delay',
          revision = revision + 1,
          updated_at = now()
      where id = v_job.id;
    end if;

    v_count := v_count + 1;
  end loop;
  return v_count;
end;
$function$;

REVOKE ALL ON FUNCTION "utrace"."reclaim_expired_job_leases"() FROM PUBLIC;
