CREATE OR REPLACE FUNCTION utrace.preview_warm_pool_shortfall()
  RETURNS TABLE (
    preview_runtime_template_id uuid,
    client_id                   uuid,
    shortfall                   integer
  )
  LANGUAGE sql
  STABLE
  SET search_path TO ''
  AS $function$
  select templates.id,
         templates.client_id,
         (templates.warm_pool_target - coalesce(pool.live, 0))::integer
  from public.preview_runtime_templates as templates
  left join lateral (
    select count(*) as live
    from public.preview_runtime_allocations as allocations
    where allocations.preview_runtime_template_id = templates.id
      and allocations.pool_role = 'warm_pool'
      and allocations.state in ('pending', 'seeding', 'ready')
      and utrace.preview_warm_runtime_fresh(allocations.runtime_expires_at)
  ) as pool on true
  where templates.retired_at is null
    and templates.warm_pool_target > coalesce(pool.live, 0);
$function$;

REVOKE ALL ON FUNCTION "utrace"."preview_warm_pool_shortfall"() FROM PUBLIC;
