CREATE OR REPLACE FUNCTION utrace.notify_extension_control()
  RETURNS TRIGGER
  LANGUAGE plpgsql
  AS $function$
begin
  perform pg_notify('extension_control', to_jsonb(new) ->> tg_argv[0]);
  return null;
end;
$function$;
