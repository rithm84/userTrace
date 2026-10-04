-- Typed readiness results and the warm-pool scan.
--
-- Contracts: docs/arch-components/preview-runtime-and-seeding.md#template-and-readiness
--            docs/arch-components/preview-runtime-and-seeding.md#allocation-lifecycle

create type public.preview_readiness_outcome as enum (
  'passed',
  'failed',
  'blocked',
  'not_applicable'
);

-- Every readiness probe result is a typed durable record, not a log line.
create table public.preview_readiness_results (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  preview_runtime_allocation_id uuid not null
    references public.preview_runtime_allocations (id),
  preview_route_id uuid references public.preview_routes (id),
  preview_version_id uuid references public.preview_versions (id),
  route_revision integer,
  probe text not null,
  outcome public.preview_readiness_outcome not null,
  detail jsonb not null,
  observed_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  created_at timestamptz not null default now()
);

create index preview_readiness_results_allocation_idx
  on public.preview_readiness_results (preview_runtime_allocation_id, observed_at desc);

create trigger preview_readiness_results_immutable
  before update or delete on public.preview_readiness_results
  for each row execute function utrace.reject_mutation();

-- How many warm runtimes each current template is short of its target. The
-- shortfall counts runtimes that are still provisioning, so a scan that runs while
-- the previous one is still building does not double-provision.
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
  ) as pool on true
  where templates.retired_at is null
    and templates.warm_pool_target > coalesce(pool.live, 0);
$$;

-- Queues one idempotent maintenance Job per short template. The concurrency key
-- keeps a second scan from leasing a second run for the same template.
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
      array['network', 'unavailable', 'timeout'],
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

alter table public.preview_readiness_results enable row level security;
revoke all on table public.preview_readiness_results from anon, authenticated;

revoke execute on function utrace.preview_warm_pool_shortfall() from public;
revoke execute on function utrace.scan_preview_warm_pools() from public;

select cron.unschedule(jobid) from cron.job where jobname = 'utrace-preview-pool-scan';

select cron.schedule(
  'utrace-preview-pool-scan',
  '* * * * *',
  $cron$select utrace.scan_preview_warm_pools();$cron$
);
