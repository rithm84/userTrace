CREATE OR REPLACE FUNCTION utrace.enqueue_ready_outbox (
  p_limit integer
)
  RETURNS integer
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_row public.outbox%rowtype;
  v_count integer := 0;
begin
  for v_row in
    select *
    from public.outbox
    where state = 'pending'
      and available_at <= now()
    order by available_at, created_at
    limit p_limit
    for update skip locked
  loop
    perform pgmq.send(
      'utrace_outbox',
      jsonb_build_object(
        'outbox_id', v_row.id,
        'client_id', v_row.client_id,
        'job_id', v_row.job_id,
        'command_id', v_row.command_id
      )
    );
    update public.outbox
    set state = 'enqueued',
        enqueued_at = now(),
        updated_at = now()
    where id = v_row.id;
    v_count := v_count + 1;
  end loop;
  return v_count;
end;
$function$;

REVOKE ALL ON FUNCTION "utrace"."enqueue_ready_outbox"(integer) FROM PUBLIC;
