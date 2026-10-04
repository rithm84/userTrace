CREATE OR REPLACE FUNCTION utrace.enqueue_ingestion_job (
  p_client_id       uuid,
  p_job_type        text,
  p_input           jsonb,
  p_concurrency_key text
)
  RETURNS uuid
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
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
$function$;

REVOKE ALL ON FUNCTION "utrace"."enqueue_ingestion_job"(uuid, text, jsonb, text) FROM PUBLIC;
