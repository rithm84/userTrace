-- Prepare a case's implementation workspace while its interview runs.
--
-- Contract: docs/arch-components/implementation.md#prepared-workspace
--
-- Once the participant's setup passes, a Job prepares the workspace the case's
-- Implementation Attempt would use: a sandbox from the pinned template with the
-- authorized repository checked out at the pinned base revision, its dependencies
-- installed, and the required checks run on the clean base. It holds no lease and
-- no case data. An approved attempt claims it under its lease when the pins it was
-- prepared from still match; one that is never claimed is released once its case
-- ends or it expires.

create table public.prepared_workspaces (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null unique references public.product_signals (id),
  state text not null
    check (state in ('preparing', 'ready', 'failed', 'claimed', 'released')),
  sandbox_name text not null unique,
  pins_digest text not null,
  baseline_checks jsonb,
  failure_detail text,
  implementation_attempt_id uuid references public.implementation_attempts (id),
  lease_fencing_token bigint,
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (state <> 'ready' or baseline_checks is not null),
  check (state <> 'claimed' or (implementation_attempt_id is not null and lease_fencing_token is not null))
);

alter table public.prepared_workspaces enable row level security;
revoke all on table public.prepared_workspaces from anon, authenticated;

-- Queue a release for every unclaimed prepared workspace whose case has ended or
-- whose sandbox has outlived its window, unless one is already in flight.
create or replace function utrace.scan_prepared_workspace_releases()
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
$$;
revoke execute on function utrace.scan_prepared_workspace_releases() from public;

select cron.unschedule(jobid) from cron.job where jobname = 'utrace-prepared-workspace-release-scan';

select cron.schedule(
  'utrace-prepared-workspace-release-scan',
  '* * * * *',
  $cron$select utrace.scan_prepared_workspace_releases();$cron$
);
