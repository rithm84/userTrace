-- Tenancy and identity: clients, staff membership, global uTrace Users, their
-- client-scoped identities, B2B customer accounts, and provider references.
--
-- Contract: docs/arch-components/identity-and-access.md#identity-and-tenancy-model

create table public.clients (
  id uuid primary key,
  name text not null,
  product_name text not null,
  revision bigint not null default 0,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.client_members (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  auth_user_id uuid not null references auth.users (id),
  role public.client_member_role not null,
  status public.membership_status not null,
  revision bigint not null default 0,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Exactly one active owner per client; a person holds at most one active row.
create unique index client_members_one_active_owner
  on public.client_members (client_id)
  where status = 'active' and role = 'owner';
create unique index client_members_one_active_identity
  on public.client_members (client_id, auth_user_id)
  where status = 'active';
create index client_members_auth_user_idx
  on public.client_members (auth_user_id, client_id)
  where status = 'active';

-- A uTrace User is global; product history stays client-scoped.
create table public.users (
  id uuid primary key,
  auth_user_id uuid unique references auth.users (id),
  display_name text,
  do_not_contact boolean not null default false,
  participation_count integer not null default 0,
  revision bigint not null default 0,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.client_users (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  utrace_user_id uuid not null references public.users (id),
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (client_id, utrace_user_id)
);
create index client_users_user_idx on public.client_users (utrace_user_id);

create table public.customer_accounts (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  external_account_key text not null,
  name text not null,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (client_id, external_account_key)
);

create table public.customer_memberships (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  customer_account_id uuid not null references public.customer_accounts (id),
  client_user_id uuid not null references public.client_users (id),
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (client_id, customer_account_id, client_user_id)
);
create index customer_memberships_client_user_idx
  on public.customer_memberships (client_user_id);

-- Provider identities never become product identity; they resolve to a uTrace owner.
create table public.provider_refs (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  provider text not null,
  installation_ref text not null,
  resource_type text not null,
  provider_resource_id text not null,
  owner_kind public.aggregate_kind not null,
  owner_id uuid not null,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (client_id, provider, installation_ref, resource_type, provider_resource_id)
);
create index provider_refs_owner_idx on public.provider_refs (owner_kind, owner_id);

-- RLS predicates. Defined here because they read the tenancy tables above.
-- Staff authorization predicate. Authorization comes from an explicit active
-- client_members row, never from a bare `authenticated` role or user metadata.
create or replace function utrace.is_client_member(p_client_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.client_members cm
    where cm.client_id = p_client_id
      and cm.auth_user_id = (select auth.uid())
      and cm.status = 'active'
  );
$$;

-- Staff owner/admin predicate for the few client-writable projections.
create or replace function utrace.is_client_owner(p_client_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.client_members cm
    where cm.client_id = p_client_id
      and cm.auth_user_id = (select auth.uid())
      and cm.status = 'active'
      and cm.role = 'owner'
  );
$$;

-- Participant predicate for participant-visible projections. A participant sees
-- only rows bound to their own client-scoped identity.
create or replace function utrace.is_participant(p_client_user_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.client_users cu
    join public.users u on u.id = cu.utrace_user_id
    where cu.id = p_client_user_id
      and u.auth_user_id = (select auth.uid())
  );
$$;

revoke execute on function utrace.is_client_member(uuid) from public;
revoke execute on function utrace.is_client_owner(uuid) from public;
revoke execute on function utrace.is_participant(uuid) from public;
grant execute on function utrace.is_client_member(uuid) to authenticated;
grant execute on function utrace.is_client_owner(uuid) to authenticated;
grant execute on function utrace.is_participant(uuid) to authenticated;

alter table public.clients enable row level security;
alter table public.client_members enable row level security;
alter table public.users enable row level security;
alter table public.client_users enable row level security;
alter table public.customer_accounts enable row level security;
alter table public.customer_memberships enable row level security;
alter table public.provider_refs enable row level security;

revoke all on table public.clients from anon, authenticated;
revoke all on table public.client_members from anon, authenticated;
revoke all on table public.users from anon, authenticated;
revoke all on table public.client_users from anon, authenticated;
revoke all on table public.customer_accounts from anon, authenticated;
revoke all on table public.customer_memberships from anon, authenticated;
revoke all on table public.provider_refs from anon, authenticated;

grant select on table public.clients to authenticated;
grant update on table public.clients to authenticated;
grant select on table public.client_members to authenticated;
grant select on table public.customer_accounts to authenticated;
grant select, update on table public.users to authenticated;

create policy clients_member_select on public.clients
  for select to authenticated
  using (utrace.is_client_member(id));

-- The only client-writable projection here is the client's own descriptive
-- fields; WITH CHECK keeps the row inside the same tenant.
create policy clients_owner_update on public.clients
  for update to authenticated
  using (utrace.is_client_owner(id))
  with check (utrace.is_client_owner(id));

create policy client_members_member_select on public.client_members
  for select to authenticated
  using (utrace.is_client_member(client_id));

create policy customer_accounts_member_select on public.customer_accounts
  for select to authenticated
  using (utrace.is_client_member(client_id));

-- A participant reads and edits only their own global preferences row.
create policy users_self_select on public.users
  for select to authenticated
  using (auth_user_id = (select auth.uid()));

create policy users_self_update on public.users
  for update to authenticated
  using (auth_user_id = (select auth.uid()))
  with check (auth_user_id = (select auth.uid()));
