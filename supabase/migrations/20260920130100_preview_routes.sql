-- Preview routes: one committed revision per session origin.
--
-- Contracts: docs/arch-components/preview-front-door.md#vercel-to-sandbox-request-path
--            docs/arch-components/preview-front-door.md#caching-and-routing
--
-- The stable session origin points initially at the base allocation and advances
-- atomically to a candidate only after that candidate passes full readiness. Each
-- request resolves exactly one committed revision; superseded and revoked
-- revisions fail closed. Route teardown precedes runtime destruction, so no
-- revision still points at a sandbox's port URL once the sandbox is gone.

create type public.preview_route_state as enum ('active', 'superseded', 'revoked');
create type public.preview_route_readiness as enum ('ready', 'reconnecting');

create table public.preview_routes (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  interview_session_id uuid not null references public.interview_sessions (id),
  preview_runtime_allocation_id uuid not null
    references public.preview_runtime_allocations (id),
  preview_version_id uuid references public.preview_versions (id),
  public_preview_host text not null,
  route_revision integer not null,
  state public.preview_route_state not null,
  readiness_state public.preview_route_readiness not null,
  upstream_port_url text not null,
  application_port integer not null,
  route_context_signing_secret text not null,
  expires_at timestamptz not null,
  committed_at timestamptz not null default now(),
  superseded_at timestamptz,
  revoked_at timestamptz,
  revocation_reason text,
  revision bigint not null default 0,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (public_preview_host, route_revision),
  constraint preview_routes_revision_positive check (route_revision >= 1),
  constraint preview_routes_port_range check (application_port between 1 and 65535)
);

-- A host resolves to at most one active revision; committing the next one must
-- supersede the current one in the same transaction.
create unique index preview_routes_one_active_per_host
  on public.preview_routes (public_preview_host)
  where state = 'active';

create index preview_routes_session_idx
  on public.preview_routes (interview_session_id, state);

-- The one-time front-door handoff codes, so a replay loses durably rather than
-- within one process. The code itself is never stored: only its digest.
create table public.preview_handoff_codes (
  code_digest text primary key,
  client_id uuid not null references public.clients (id),
  interview_session_id uuid not null references public.interview_sessions (id),
  preview_route_id uuid not null references public.preview_routes (id),
  public_preview_host text not null,
  issued_at timestamptz not null default now(),
  expires_at timestamptz not null,
  consumed_at timestamptz
);

create index preview_handoff_codes_session_idx
  on public.preview_handoff_codes (interview_session_id);

alter table public.preview_routes enable row level security;
alter table public.preview_handoff_codes enable row level security;
revoke all on table public.preview_routes from anon, authenticated;
revoke all on table public.preview_handoff_codes from anon, authenticated;
