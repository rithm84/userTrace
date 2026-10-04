CREATE OR REPLACE FUNCTION utrace.acquire_preview_allocation (
  p_allocation_id uuid
)
  RETURNS bigint
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_token bigint;
begin
  select utrace.next_fencing_token() into v_token;
  update public.preview_runtime_allocations
  set fencing_token = v_token,
      updated_at = now()
  where id = p_allocation_id;
  if not found then
    raise exception 'preview runtime allocation % does not exist', p_allocation_id
      using errcode = 'no_data_found';
  end if;
  return v_token;
end;
$function$;

REVOKE ALL ON FUNCTION "utrace"."acquire_preview_allocation"(uuid) FROM PUBLIC;
