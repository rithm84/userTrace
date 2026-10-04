-- Implementation Attempts with fenced Environment Leases, Preview Runtime
-- Allocations, and immutable candidate Preview Versions.
--
-- Contracts: docs/arch-components/implementation.md#attempt-protocol
--            docs/arch-components/control-plane.md#preview-runtime-allocation-and-environment-lease

create table public.implementation_attempts (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  gate_decision_id uuid not null references public.gate_decisions (id),
  state public.implementation_attempt_state not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  repository_ref text not null,
  base_revision text not null,
  consumed_budgets jsonb not null,
  checkpoint jsonb,
  outcome_reason text,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- The demo grants one Implementation Attempt per Product Signal.
  unique (product_signal_id)
);

-- Every mutating workspace operation carries the current fencing token.
create table public.environment_leases (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  implementation_attempt_id uuid not null references public.implementation_attempts (id),
  state public.environment_lease_state not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  fencing_token bigint not null,
  workspace_ref text,
  expires_at timestamptz not null,
  heartbeat_at timestamptz not null default now(),
  released_at timestamptz,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (implementation_attempt_id, fencing_token)
);

create unique index environment_leases_one_active
  on public.environment_leases (implementation_attempt_id)
  where state = 'active';

create table public.preview_runtime_allocations (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  allocation_kind public.preview_allocation_kind not null,
  state public.preview_runtime_allocation_state not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  interview_session_id uuid references public.interview_sessions (id),
  preview_version_id uuid,
  seed_manifest_digest text,
  runtime_ref text,
  origin_host text unique,
  released_at timestamptz,
  terminated_at timestamptz,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index preview_runtime_allocations_signal_idx
  on public.preview_runtime_allocations (product_signal_id, state);

create table public.preview_versions (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  implementation_attempt_id uuid not null unique
    references public.implementation_attempts (id),
  state public.preview_version_state not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  source_commit text not null,
  source_digest text not null,
  build_digest text not null,
  base_revision text not null,
  seed_manifest_digest text,
  ready_at timestamptz,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index preview_versions_signal_idx
  on public.preview_versions (product_signal_id, state);

alter table public.preview_runtime_allocations
  add constraint preview_runtime_allocations_version_fk
  foreign key (preview_version_id) references public.preview_versions (id);

alter table public.interview_sessions
  add constraint interview_sessions_allocation_fk
  foreign key (preview_runtime_allocation_id)
  references public.preview_runtime_allocations (id);

alter table public.implementation_attempts enable row level security;
alter table public.environment_leases enable row level security;
alter table public.preview_runtime_allocations enable row level security;
alter table public.preview_versions enable row level security;

revoke all on table public.implementation_attempts from anon, authenticated;
revoke all on table public.environment_leases from anon, authenticated;
revoke all on table public.preview_runtime_allocations from anon, authenticated;
revoke all on table public.preview_versions from anon, authenticated;

grant select on table public.implementation_attempts to authenticated;
grant select on table public.preview_versions to authenticated;

create policy implementation_attempts_member_select on public.implementation_attempts
  for select to authenticated
  using (utrace.is_client_member(client_id));

create policy preview_versions_member_select on public.preview_versions
  for select to authenticated
  using (utrace.is_client_member(client_id));
