-- The durable preview environment an observation activation binds to.
--
-- A preview environment is the prepared environment one pinned runtime template
-- and configuration release define. Allocations instantiate it: several warm and
-- claimed runtimes of the same template are the same environment, which is why
-- the activation carries `preview_environment_id` and
-- `preview_runtime_allocation_id` as separate identities.
--
-- This replaces the UUIDv5 derivation ingestion used while no table existed.
--
-- Contract: docs/arch-components/server-sdk.md#configuration-identity-and-credentials

create table public.preview_environments (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  preview_runtime_template_id uuid not null
    references public.preview_runtime_templates (id),
  pinned_configuration_release_id uuid not null
    references public.configuration_releases (id),
  created_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  unique (client_id, preview_runtime_template_id)
);
create index preview_environments_release_idx
  on public.preview_environments (pinned_configuration_release_id);

create trigger preview_environments_immutable
  before update or delete on public.preview_environments
  for each row execute function utrace.reject_mutation();

-- Declared NOT VALID: rows written before this migration carry the derived
-- identity and are not rewritten. Every new activation is checked.
alter table public.telemetry_activations
  add constraint telemetry_activations_preview_environment_fk
  foreign key (preview_environment_id) references public.preview_environments (id)
  not valid;

alter table public.preview_environments enable row level security;
revoke all on table public.preview_environments from anon, authenticated;
