-- Tear down the preview runtimes of cases that have ended.
--
-- Contract: docs/arch-components/preview-runtime-and-seeding.md#failure-cleanup-and-reset
--
-- A case's preview runtimes are no longer needed once its Product Signal is
-- terminal, whichever path ended it: validation, a blocker, no implementation, or
-- a stop. The scan queues one teardown Job for each such runtime that is not
-- terminated and has no teardown in flight; the Job extracts the evidence the
-- Case File needs, revokes the route, and destroys the sandbox.

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
    select allocations.id, allocations.client_id
    from public.preview_runtime_allocations as allocations
    join public.product_signals as signals on signals.id = allocations.product_signal_id
    where allocations.state <> 'terminated'
      and signals.state in (
        'closed_validated', 'closed_no_implementation', 'closed_blocked', 'canceled'
      )
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
      jsonb_build_object('preview_runtime_allocation_id', v_row.id, 'reason', 'case_ended'),
      now() + interval '1 hour',
      900,
      3,
      jsonb_build_object('runtimes', 1),
      array['network', 'unavailable', 'timeout'],
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

select cron.unschedule(jobid) from cron.job where jobname = 'utrace-preview-teardown-scan';

select cron.schedule(
  'utrace-preview-teardown-scan',
  '* * * * *',
  $cron$select utrace.scan_preview_teardowns();$cron$
);
