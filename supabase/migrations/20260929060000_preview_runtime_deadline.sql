-- Retire warm preview runtimes before their sandbox reaches its execution limit.
--
-- Contract: docs/arch-components/preview-runtime-and-seeding.md#allocation-lifecycle
--
-- A runtime's sandbox stops at a fixed execution limit, but a warm runtime waits
-- for a claim for as long as that takes, so the pool could hand a session a
-- runtime that had already stopped. An allocation now records when its sandbox
-- stops. A warm runtime is fresh while more than the claim margin remains: only a
-- fresh one is claimed and counted toward the pool target, and the teardown scan
-- destroys the rest, so the pool scan provisions their replacements. A claim
-- extends the sandbox so the session starts with the full limit. An allocation
-- with no recorded deadline predates this migration and is never fresh.

alter table public.preview_runtime_allocations
  add column runtime_expires_at timestamptz;

create function utrace.preview_warm_runtime_fresh(p_runtime_expires_at timestamptz)
returns boolean
language sql
stable
set search_path = ''
as $$
  select p_runtime_expires_at is not null
     and p_runtime_expires_at > now() + interval '20 minutes';
$$;
revoke execute on function utrace.preview_warm_runtime_fresh(timestamptz) from public;

create or replace function utrace.preview_warm_pool_shortfall()
returns table (
  preview_runtime_template_id uuid,
  client_id uuid,
  shortfall integer
)
language sql
stable
set search_path = ''
as $$
  select templates.id,
         templates.client_id,
         (templates.warm_pool_target - coalesce(pool.live, 0))::integer
  from public.preview_runtime_templates as templates
  left join lateral (
    select count(*) as live
    from public.preview_runtime_allocations as allocations
    where allocations.preview_runtime_template_id = templates.id
      and allocations.pool_role = 'warm_pool'
      and allocations.state in ('pending', 'seeding', 'ready')
      and utrace.preview_warm_runtime_fresh(allocations.runtime_expires_at)
  ) as pool on true
  where templates.retired_at is null
    and templates.warm_pool_target > coalesce(pool.live, 0);
$$;
revoke execute on function utrace.preview_warm_pool_shortfall() from public;

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
    union all
    select allocations.id, allocations.client_id, 'warm_runtime_expiring' as reason
    from public.preview_runtime_allocations as allocations
    where allocations.pool_role = 'warm_pool'
      and allocations.interview_session_id is null
      and allocations.state <> 'terminated'
      and not utrace.preview_warm_runtime_fresh(allocations.runtime_expires_at)
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
