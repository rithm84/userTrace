SET local check_function_bodies = off;

CREATE OR REPLACE FUNCTION utrace.scan_sandbox_deadlines()
  RETURNS integer
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_row record;
  v_job_id uuid;
  v_count integer := 0;
begin
  for v_row in
    select allocations.client_id
    from public.preview_runtime_allocations as allocations
    where allocations.state <> 'terminated'
      and allocations.runtime_ref is not null
      and allocations.runtime_expires_at > now()
    union
    select workspaces.client_id
    from public.prepared_workspaces as workspaces
    where workspaces.state in ('preparing', 'ready')
      and workspaces.expires_at > now()
  loop
    if exists (
      select 1 from public.jobs
      where job_type = 'sandboxes.extend_deadlines'
        and concurrency_key = 'sandbox_deadlines:' || v_row.client_id::text
        and state in ('pending', 'ready', 'leased')
    ) then
      continue;
    end if;
    v_job_id := gen_random_uuid();
    insert into public.jobs (
      id, client_id, job_type, state, input, deadline_at, timeout_seconds, max_runs,
      resource_budget, retryable_categories, backoff_base_seconds, backoff_max_seconds,
      concurrency_key
    )
    values (
      v_job_id,
      v_row.client_id,
      'sandboxes.extend_deadlines',
      'ready',
      '{}'::jsonb,
      now() + interval '5 minutes',
      120,
      3,
      jsonb_build_object('kind', 'sandbox_deadlines'),
      array['timeout', 'rate_limited', 'provider_unavailable'],
      10,
      60,
      'sandbox_deadlines:' || v_row.client_id::text
    );
    insert into public.outbox (id, client_id, state, job_id, available_at)
    values (v_job_id, v_row.client_id, 'pending', v_job_id, now());
    v_count := v_count + 1;
  end loop;
  return v_count;
end;
$function$;

REVOKE ALL ON FUNCTION "utrace"."scan_sandbox_deadlines"() FROM PUBLIC;

SELECT cron.schedule_in_database('utrace-sandbox-deadline-scan', '* * * * *', 'select utrace.scan_sandbox_deadlines();', 'postgres', NULL, true);
