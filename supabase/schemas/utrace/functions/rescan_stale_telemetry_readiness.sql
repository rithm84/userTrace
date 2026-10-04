CREATE OR REPLACE FUNCTION utrace.rescan_stale_telemetry_readiness (
  p_limit integer
)
  RETURNS integer
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
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
$function$;

REVOKE ALL ON FUNCTION "utrace"."rescan_stale_telemetry_readiness"(integer) FROM PUBLIC;
