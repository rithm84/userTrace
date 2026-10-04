-- Immutable client configuration releases. A Product Signal pins one release for
-- its whole lifecycle; later releases govern new Product Signals only.
--
-- Contract: docs/arch-components/policy-and-permissions.md#client-policy-model

create table public.configuration_releases (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  release_number bigint not null,
  policy jsonb not null,
  digest text not null,
  authorized_by_auth_user_id uuid not null references auth.users (id),
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (client_id, release_number)
);
create index configuration_releases_client_idx
  on public.configuration_releases (client_id, release_number desc);

create trigger configuration_releases_immutable
  before update or delete on public.configuration_releases
  for each row execute function utrace.reject_mutation();

alter table public.configuration_releases enable row level security;
revoke all on table public.configuration_releases from anon, authenticated;
grant select on table public.configuration_releases to authenticated;

create policy configuration_releases_member_select on public.configuration_releases
  for select to authenticated
  using (utrace.is_client_member(client_id));
