CREATE OR REPLACE FUNCTION utrace.is_client_member (
  p_client_id uuid
)
  RETURNS boolean
  LANGUAGE sql
  STABLE
  SECURITY DEFINER
  SET search_path TO ''
  AS $function$
  select exists (
    select 1
    from public.client_members cm
    where cm.client_id = p_client_id
      and cm.auth_user_id = (select auth.uid())
      and cm.status = 'active'
  );
$function$;

GRANT EXECUTE ON FUNCTION "utrace"."is_client_member"(uuid) TO "authenticated";

REVOKE ALL ON FUNCTION "utrace"."is_client_member"(uuid) FROM PUBLIC;
