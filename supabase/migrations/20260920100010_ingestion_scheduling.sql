-- Self-driving correlation and readiness.
--
-- The ingress path enqueues its follow-up Job in the same transaction that
-- commits the receipt, so a crash between the two cannot leave a durable record
-- with no derivation scheduled. This function is that single enqueue: the Python
-- edges call it inside their own transaction and the pg_cron scan below calls it
-- with no Python in the loop, so there is one implementation of the dedupe and
-- the outbox row rather than two that can drift.
--
-- Contracts: docs/arch-components/control-plane.md#jobs-commands-and-scheduling
--            docs/arch-components/telemetry-and-evidence.md#completeness-and-readiness

-- UUIDv7 for the rows the scheduler mints with no Python in the loop. Control
-- plane identifiers are minted in Python everywhere else; a pg_cron scan has no
-- application process to mint them in, and the Jobs it creates still have to be
-- time-ordered like every other Job row.
create or replace function utrace.uuid7()
returns uuid
language plpgsql
volatile
set search_path = ''
as $$
declare
  v_millis bigint := (extract(epoch from clock_timestamp()) * 1000)::bigint;
  v_bytes bytea := pg_catalog.uuid_send(pg_catalog.gen_random_uuid());
begin
  v_bytes := pg_catalog.set_byte(v_bytes, 0, ((v_millis >> 40) & 255)::integer);
  v_bytes := pg_catalog.set_byte(v_bytes, 1, ((v_millis >> 32) & 255)::integer);
  v_bytes := pg_catalog.set_byte(v_bytes, 2, ((v_millis >> 24) & 255)::integer);
  v_bytes := pg_catalog.set_byte(v_bytes, 3, ((v_millis >> 16) & 255)::integer);
  v_bytes := pg_catalog.set_byte(v_bytes, 4, ((v_millis >> 8) & 255)::integer);
  v_bytes := pg_catalog.set_byte(v_bytes, 5, (v_millis & 255)::integer);
  v_bytes := pg_catalog.set_byte(
    v_bytes, 6, ((pg_catalog.get_byte(v_bytes, 6) & 15) | 112)
  );
  v_bytes := pg_catalog.set_byte(
    v_bytes, 8, ((pg_catalog.get_byte(v_bytes, 8) & 63) | 128)
  );
  return pg_catalog.encode(v_bytes, 'hex')::uuid;
end;
$$;

-- Creates one ingestion Job and its outbox row, or returns null when an
-- unfinished Job already holds the concurrency key. Correlation is keyed by
-- trace and readiness by Interview Session, so repeated receipts on the same
-- trace coalesce into the one run that has not happened yet, and a run that has
-- already finished never blocks the next one.
create or replace function utrace.enqueue_ingestion_job(
  p_client_id uuid,
  p_job_type text,
  p_input jsonb,
  p_concurrency_key text
)
returns uuid
language plpgsql
volatile
set search_path = ''
as $$
declare
  v_job_id uuid;
begin
  if exists (
    select 1
    from public.jobs
    where client_id = p_client_id
      and concurrency_key = p_concurrency_key
      and state in ('pending', 'ready', 'leased')
  ) then
    return null;
  end if;

  v_job_id := utrace.uuid7();
  insert into public.jobs (
    id, client_id, job_type, state, input, deadline_at, timeout_seconds, max_runs,
    resource_budget, retryable_categories, backoff_base_seconds,
    backoff_max_seconds, concurrency_key, not_before
  )
  values (
    v_job_id, p_client_id, p_job_type, 'ready', p_input,
    now() + interval '1 hour', 60, 5, '{"kind": "ingestion"}'::jsonb,
    array['provider_unavailable', 'timeout', 'internal'], 1, 30,
    p_concurrency_key, now()
  );
  insert into public.outbox (id, client_id, state, job_id, available_at)
  values (v_job_id, p_client_id, 'pending', v_job_id, now());
  return v_job_id;
end;
$$;

-- Re-evaluates readiness for activations whose newest projection expired.
--
-- A readiness projection expires so a stale one can never authorize probing.
-- Without this scan an activation that stops producing would keep its last
-- `ready` projection until something else happened to it; with it, the
-- projection is refreshed and withdraws readiness on its own evidence.
create or replace function utrace.rescan_stale_telemetry_readiness(p_limit integer)
returns integer
language plpgsql
volatile
set search_path = ''
as $$
declare
  v_row record;
  v_count integer := 0;
begin
  for v_row in
    select activations.id, activations.client_id, activations.interview_session_id
    from public.telemetry_activations as activations
    join public.interview_sessions as sessions
      on sessions.id = activations.interview_session_id
    where activations.state = 'active'
      and sessions.state <> 'closed'
      and not exists (
        select 1
        from public.telemetry_readiness_projections as fresh
        where fresh.activation_id = activations.id
          and fresh.expires_at > now()
      )
    order by activations.created_at
    limit p_limit
  loop
    if utrace.enqueue_ingestion_job(
      v_row.client_id,
      'ingestion.evaluate_readiness',
      jsonb_build_object('activation_id', v_row.id),
      'ingestion.evaluate_readiness:' || v_row.interview_session_id::text
    ) is not null then
      v_count := v_count + 1;
    end if;
  end loop;
  return v_count;
end;
$$;

revoke execute on function utrace.uuid7() from public;
revoke execute on function utrace.enqueue_ingestion_job(uuid, text, jsonb, text)
  from public;
revoke execute on function utrace.rescan_stale_telemetry_readiness(integer) from public;

select cron.unschedule(jobid)
from cron.job
where jobname = 'utrace-telemetry-readiness-scan';

select cron.schedule(
  'utrace-telemetry-readiness-scan',
  '* * * * *',
  $cron$select utrace.rescan_stale_telemetry_readiness(200);$cron$
);
