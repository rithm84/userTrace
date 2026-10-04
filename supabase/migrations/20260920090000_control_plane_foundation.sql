-- Control-plane foundation: private schema, immutability guard,
-- and the fencing-token sequence shared by Environment Leases and Job Runs.
--
-- Contracts: docs/arch-components/data-and-schema.md#tenant-access-and-row-level-security
--            docs/arch-components/identity-and-access.md#client-staff-authentication-with-supabase-auth

create extension if not exists pgmq;
create extension if not exists pg_cron;

create schema if not exists utrace;
revoke all on schema utrace from public;
grant usage on schema utrace to authenticated;

-- Immutable-record guard. Attached to tables whose rows are frozen on insert.
create or replace function utrace.reject_mutation()
returns trigger
language plpgsql
as $$
begin
  raise exception
    'table %.% is append-only; % is rejected',
    tg_table_schema, tg_table_name, tg_op
    using errcode = 'restrict_violation';
end;
$$;

-- Fencing tokens are globally monotonic so a stale worker can never outrank a
-- newer lease, even across aggregates.
create sequence if not exists utrace.fencing_token_seq as bigint start with 1;

create or replace function utrace.next_fencing_token()
returns bigint
language sql
volatile
as $$
  select nextval('utrace.fencing_token_seq');
$$;

-- Least privilege: internal helpers stay uncallable by signed-in roles.
revoke execute on function utrace.reject_mutation() from public;
revoke execute on function utrace.next_fencing_token() from public;
