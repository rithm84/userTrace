CREATE OR REPLACE FUNCTION utrace.uuid7()
  RETURNS uuid
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
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
$function$;

REVOKE ALL ON FUNCTION "utrace"."uuid7"() FROM PUBLIC;
