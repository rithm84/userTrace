CREATE OR REPLACE FUNCTION utrace.scan_prepared_workspace_releases()
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
    select workspaces.id, workspaces.client_id
    from public.prepared_workspaces as workspaces
    join public.product_signals as signals on signals.id = workspaces.product_signal_id
    where workspaces.state in ('preparing', 'ready', 'failed')
      and (
        workspaces.expires_at < now()
        or signals.state in (
          'closed_validated', 'closed_no_implementation', 'closed_blocked', 'canceled'
        )
      )
  loop
    if exists (
      select 1 from public.jobs
      where job_type = 'implementation.release_prepared_workspace'
        and concurrency_key = 'prepared_workspace_release:' || v_row.id::text
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
      'implementation.release_prepared_workspace',
      'ready',
      jsonb_build_object('prepared_workspace_id', v_row.id),
      now() + interval '1 hour',
      300,
      3,
      jsonb_build_object('workspaces', 1),
      array['timeout', 'rate_limited', 'provider_unavailable'],
      30,
      300,
      'prepared_workspace_release:' || v_row.id::text
    );
    insert into public.outbox (id, client_id, state, job_id, available_at)
    values (v_job_id, v_row.client_id, 'pending', v_job_id, now());
    v_count := v_count + 1;
  end loop;
  return v_count;
end;
$function$;

REVOKE ALL ON FUNCTION "utrace"."scan_prepared_workspace_releases"() FROM PUBLIC;
