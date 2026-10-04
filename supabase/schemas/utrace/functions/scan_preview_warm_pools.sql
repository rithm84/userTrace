CREATE OR REPLACE FUNCTION utrace.scan_preview_warm_pools()
  RETURNS integer
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_row record;
  v_job_id uuid;
  v_count integer := 0;
begin
  for v_row in select * from utrace.preview_warm_pool_shortfall()
  loop
    if exists (
      select 1 from public.jobs
      where job_type = 'preview.maintain_warm_pool'
        and client_id = v_row.client_id
        and concurrency_key = 'preview_warm_pool:' || v_row.preview_runtime_template_id::text
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
      'preview.maintain_warm_pool',
      'ready',
      jsonb_build_object(
        'preview_runtime_template_id', v_row.preview_runtime_template_id,
        'shortfall', v_row.shortfall
      ),
      now() + interval '1 hour',
      1800,
      3,
      jsonb_build_object('runtimes', v_row.shortfall),
      array['timeout', 'rate_limited', 'provider_unavailable'],
      30,
      300,
      'preview_warm_pool:' || v_row.preview_runtime_template_id::text
    );
    insert into public.outbox (id, client_id, state, job_id, available_at)
    values (v_job_id, v_row.client_id, 'pending', v_job_id, now());
    v_count := v_count + 1;
  end loop;
  return v_count;
end;
$function$;

REVOKE ALL ON FUNCTION "utrace"."scan_preview_warm_pools"() FROM PUBLIC;
