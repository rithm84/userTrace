-- Advisor findings from the initial schema: pin the search_path on the internal
-- helpers and collapse the two permissive session policies into one predicate.

create or replace function utrace.reject_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception
    'table %.% is append-only; % is rejected',
    tg_table_schema, tg_table_name, tg_op
    using errcode = 'restrict_violation';
end;
$$;

create or replace function utrace.next_fencing_token()
returns bigint
language sql
volatile
set search_path = ''
as $$
  select nextval('utrace.fencing_token_seq');
$$;

-- One permissive policy per role and action: client staff for their tenant, or
-- the participant bound to the session's own client-scoped identity.
drop policy interview_sessions_member_select on public.interview_sessions;
drop policy interview_sessions_participant_select on public.interview_sessions;

create policy interview_sessions_authorized_select on public.interview_sessions
  for select to authenticated
  using (
    utrace.is_client_member(client_id)
    or utrace.is_participant(client_user_id)
  );
