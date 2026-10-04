CREATE OR REPLACE FUNCTION utrace.is_participant (
  p_client_user_id uuid
)
  RETURNS boolean
  LANGUAGE sql
  STABLE
  SECURITY DEFINER
  SET search_path TO ''
  AS $function$
  select exists (
    select 1
    from public.client_users cu
    join public.users u on u.id = cu.utrace_user_id
    where cu.id = p_client_user_id
      and u.auth_user_id = (select auth.uid())
  );
$function$;

GRANT EXECUTE ON FUNCTION "utrace"."is_participant"(uuid) TO "authenticated";

REVOKE ALL ON FUNCTION "utrace"."is_participant"(uuid) FROM PUBLIC;
