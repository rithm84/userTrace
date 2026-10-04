CREATE OR REPLACE FUNCTION utrace.notify_voice_sideband_observations()
  RETURNS TRIGGER
  LANGUAGE plpgsql
  AS $function$
begin
  perform pg_notify('voice_sideband', sessions.interview_session_id::text)
  from (
    select distinct activations.interview_session_id
    from inserted
    join public.telemetry_activations as activations
      on activations.id = inserted.activation_id
    where activations.interview_session_id is not null
  ) as sessions;
  return null;
end;
$function$;
