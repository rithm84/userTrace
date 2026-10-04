-- Retry the scheduled preview Jobs on failures a worker can actually report.
--
-- The warm-pool and teardown scans listed `network` and `unavailable` as
-- retryable, which are not failure categories, so a provider outage failed those
-- Jobs for good. They now list the transient `TechnicalCategory` values the
-- application's Jobs use (`TRANSIENT_CATEGORIES` in app/envelope.py).

create or replace function utrace.scan_preview_warm_pools()
returns integer
language plpgsql
volatile
set search_path = ''
as $$
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
$$;
revoke execute on function utrace.scan_preview_warm_pools() from public;

create or replace function utrace.scan_preview_teardowns()
returns integer
language plpgsql
volatile
set search_path = ''
as $$
declare
  v_row record;
  v_job_id uuid;
  v_count integer := 0;
begin
  for v_row in
    select allocations.id, allocations.client_id, 'case_ended' as reason
    from public.preview_runtime_allocations as allocations
    join public.product_signals as signals on signals.id = allocations.product_signal_id
    where allocations.state <> 'terminated'
      and signals.state in (
        'closed_validated', 'closed_no_implementation', 'closed_blocked', 'canceled'
      )
    union all
    select allocations.id, allocations.client_id, 'template_retired' as reason
    from public.preview_runtime_allocations as allocations
    join public.preview_runtime_templates as templates
      on templates.id = allocations.preview_runtime_template_id
    where allocations.pool_role = 'warm_pool'
      and allocations.interview_session_id is null
      and allocations.state <> 'terminated'
      and templates.retired_at is not null
  loop
    if exists (
      select 1 from public.jobs
      where job_type = 'preview.tear_down_allocation'
        and concurrency_key = 'preview_teardown:' || v_row.id::text
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
      'preview.tear_down_allocation',
      'ready',
      jsonb_build_object('preview_runtime_allocation_id', v_row.id, 'reason', v_row.reason),
      now() + interval '1 hour',
      900,
      3,
      jsonb_build_object('runtimes', 1),
      array['timeout', 'rate_limited', 'provider_unavailable'],
      30,
      300,
      'preview_teardown:' || v_row.id::text
    );
    insert into public.outbox (id, client_id, state, job_id, available_at)
    values (v_job_id, v_row.client_id, 'pending', v_job_id, now());
    v_count := v_count + 1;
  end loop;
  return v_count;
end;
$$;
revoke execute on function utrace.scan_preview_teardowns() from public;
