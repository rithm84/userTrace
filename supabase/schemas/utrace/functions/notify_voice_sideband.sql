CREATE OR REPLACE FUNCTION utrace.notify_voice_sideband()
  RETURNS TRIGGER
  LANGUAGE plpgsql
  AS $function$
begin
  perform pg_notify('voice_sideband', to_jsonb(new) ->> tg_argv[0]);
  return null;
end;
$function$;
