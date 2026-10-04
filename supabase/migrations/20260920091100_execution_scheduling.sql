-- Durable dispatch wiring: the pgmq wake-up queue, the pg_cron safety scans, and
-- the reclamation functions that restart reconciliation reuses.
--
-- Queue delivery is at least once. Workers claim durable rows with transactional
-- leases; the queue only wakes them.

do $$
begin
  if not exists (select 1 from pgmq.list_queues() where queue_name = 'utrace_outbox') then
    perform pgmq.create('utrace_outbox');
  end if;
end;
$$;

-- Moves due outbox rows onto the wake-up queue. Safe to run concurrently: rows
-- are claimed with SKIP LOCKED and the queue message is only a hint.
create or replace function utrace.enqueue_ready_outbox(p_limit integer)
returns integer
language plpgsql
volatile
set search_path = ''
as $$
declare
  v_row public.outbox%rowtype;
  v_count integer := 0;
begin
  for v_row in
    select *
    from public.outbox
    where state = 'pending'
      and available_at <= now()
    order by available_at, created_at
    limit p_limit
    for update skip locked
  loop
    perform pgmq.send(
      'utrace_outbox',
      jsonb_build_object(
        'outbox_id', v_row.id,
        'client_id', v_row.client_id,
        'job_id', v_row.job_id,
        'command_id', v_row.command_id
      )
    );
    update public.outbox
    set state = 'enqueued',
        enqueued_at = now(),
        updated_at = now()
    where id = v_row.id;
    v_count := v_count + 1;
  end loop;
  return v_count;
end;
$$;

-- Returns expired outbox enqueues to pending so a lost wake-up cannot strand work.
create or replace function utrace.requeue_stale_outbox(p_stale_after interval)
returns integer
language plpgsql
volatile
set search_path = ''
as $$
declare
  v_count integer;
begin
  with stale as (
    update public.outbox
    set state = 'pending',
        enqueued_at = null,
        attempts = attempts + 1,
        available_at = now(),
        updated_at = now()
    where state = 'enqueued'
      and enqueued_at < now() - p_stale_after
    returning 1
  )
  select count(*) into v_count from stale;
  return v_count;
end;
$$;

-- Abandons Job Runs whose lease expired and returns their Job to the claimable
-- pool with jittered backoff, or fails it when the run budget is exhausted.
create or replace function utrace.reclaim_expired_job_leases()
returns integer
language plpgsql
volatile
set search_path = ''
as $$
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
$$;

-- Expires Jobs past their frozen deadline regardless of lease state.
create or replace function utrace.expire_overdue_jobs()
returns integer
language plpgsql
volatile
set search_path = ''
as $$
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
$$;

revoke execute on function utrace.enqueue_ready_outbox(integer) from public;
revoke execute on function utrace.requeue_stale_outbox(interval) from public;
revoke execute on function utrace.reclaim_expired_job_leases() from public;
revoke execute on function utrace.expire_overdue_jobs() from public;

select cron.unschedule(jobid)
from cron.job
where jobname in ('utrace-outbox-scan', 'utrace-lease-scan');

select cron.schedule(
  'utrace-outbox-scan',
  '* * * * *',
  $cron$select utrace.enqueue_ready_outbox(500), utrace.requeue_stale_outbox(interval '5 minutes');$cron$
);

select cron.schedule(
  'utrace-lease-scan',
  '* * * * *',
  $cron$select utrace.reclaim_expired_job_leases(), utrace.expire_overdue_jobs();$cron$
);
