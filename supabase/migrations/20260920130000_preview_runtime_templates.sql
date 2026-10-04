-- Pinned preview runtime templates and the allocation pins every runtime records.
--
-- Contracts: docs/arch-components/preview-runtime-and-seeding.md#template-and-readiness
--            docs/arch-components/preview-runtime-and-seeding.md#failure-cleanup-and-reset
--
-- A client configuration pins exactly one runtime template: the Vercel Container
-- Registry image built from the fixture repository, pinned by digest, the Vercel
-- Sandbox snapshot taken from it, the seed manifest it carries, the fixture clock,
-- the application port, and the SDK versions installed in it. Every allocation copies those pins onto itself so
-- a Preview Version's evidence can cite the exact runtime it ran on.

create type public.preview_pool_role as enum ('warm_pool', 'session_base', 'candidate');

create table public.preview_runtime_templates (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  pinned_configuration_release_id uuid not null
    references public.configuration_releases (id),
  template_id text not null,
  template_revision integer not null,
  snapshot_id text not null,
  image_digest text not null,
  recipe_digest text not null,
  seed_manifest_digest text not null,
  seed_manifest jsonb not null,
  fixture_clock timestamptz not null,
  application_port integer not null,
  authenticated_identity text not null,
  sdk_versions jsonb not null,
  warm_pool_target integer not null,
  retired_at timestamptz,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (client_id, template_id, template_revision),
  constraint preview_runtime_templates_port_range
    check (application_port between 1 and 65535),
  constraint preview_runtime_templates_pool_target_nonnegative
    check (warm_pool_target >= 0)
);

-- One template is current per client; retiring one is what admits the next.
create unique index preview_runtime_templates_one_current
  on public.preview_runtime_templates (client_id)
  where retired_at is null;

-- A warm-pool runtime exists before any Product Signal claims it, so the case
-- columns start null and are filled by the claim.
alter table public.preview_runtime_allocations
  alter column product_signal_id drop not null;

alter table public.preview_runtime_allocations
  add column preview_runtime_template_id uuid
    references public.preview_runtime_templates (id),
  add column pool_role public.preview_pool_role not null default 'warm_pool',
  add column pinned_configuration_release_id uuid
    references public.configuration_releases (id),
  add column image_digest text,
  add column recipe_digest text,
  add column fixture_clock timestamptz,
  add column authenticated_identity text,
  add column sdk_versions jsonb,
  add column application_port integer,
  add column route_revision integer,
  add column handoff_secret text,
  add column claimed_at timestamptz,
  add column evidence jsonb;

alter table public.preview_runtime_allocations
  alter column pool_role drop default;

-- A claimed base runtime is exclusive to one Interview Session.
create unique index preview_runtime_allocations_one_base_per_session
  on public.preview_runtime_allocations (interview_session_id)
  where allocation_kind = 'base' and interview_session_id is not null;

-- A Preview Version has exactly one live candidate runtime.
create unique index preview_runtime_allocations_one_live_candidate
  on public.preview_runtime_allocations (preview_version_id)
  where allocation_kind = 'candidate'
    and preview_version_id is not null
    and state <> 'terminated';

create index preview_runtime_allocations_pool_idx
  on public.preview_runtime_allocations (preview_runtime_template_id, pool_role, state);

alter table public.preview_runtime_templates enable row level security;
revoke all on table public.preview_runtime_templates from anon, authenticated;
