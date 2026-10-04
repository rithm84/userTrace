-- Fencing on preview runtime allocations.
--
-- Contract: docs/arch-components/implementation.md#enforcement-model
--
-- Every preview Job after the creating one reaches its sandbox again through the
-- durable ``runtime_ref``. Re-acquiring an allocation mints a new token from the
-- globally monotonic sequence, so a worker that lost its lease and later wakes up
-- can still read and still log, but cannot seed, route, or destroy.

alter table public.preview_runtime_allocations
  add column fencing_token bigint not null default 0;

alter table public.preview_runtime_allocations
  alter column fencing_token drop default;

-- Re-acquires one allocation for a worker and returns the token it must present.
create or replace function utrace.acquire_preview_allocation(p_allocation_id uuid)
returns bigint
language plpgsql
volatile
set search_path = ''
as $$
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
$$;

revoke execute on function utrace.acquire_preview_allocation(uuid) from public;
