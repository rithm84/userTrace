-- Correlation projections, telemetry evidence windows and their promoted items,
-- and the readiness projections ingestion reports to the control plane.
--
-- There is no generic correlation_id: a projection is keyed by its exact client,
-- activation, and trace, and it lists what it required, what it observed, and the
-- watermarks it saw.
--
-- Contracts: docs/arch-components/telemetry-and-evidence.md#correlation-model
--            docs/arch-components/telemetry-and-evidence.md#evidence-windows-and-promotion
--            docs/arch-components/telemetry-and-evidence.md#completeness-and-readiness

create table public.correlation_projections (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  activation_id uuid not null references public.telemetry_activations (id),
  server_activation_id uuid references public.telemetry_activations (id),
  interview_session_id uuid not null references public.interview_sessions (id),
  trace_id text not null,
  status public.correlation_status not null,
  revision bigint not null default 1,
  browser_stream_id uuid references public.browser_streams (id),
  browser_request_id text,
  required_components jsonb not null,
  observed_components jsonb not null,
  gaps jsonb not null,
  conflicts jsonb not null,
  first_watermark_at timestamptz,
  last_watermark_at timestamptz,
  browser_accepted_through bigint not null default 0,
  server_accepted_through bigint not null default 0,
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  constraint correlation_projections_trace_id_format check (trace_id ~ '^[0-9a-f]{32}$')
);

create unique index correlation_projections_identity
  on public.correlation_projections (client_id, activation_id, trace_id);
create index correlation_projections_session_idx
  on public.correlation_projections (interview_session_id, status);

-- An evidence window records one bounded selection of observations. A rolling
-- window may advance within the active session; freezing copies its bounds and
-- makes it immune to later data.
create table public.telemetry_evidence_windows (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  interview_session_id uuid not null references public.interview_sessions (id),
  product_signal_id uuid not null references public.product_signals (id),
  activation_id uuid references public.telemetry_activations (id),
  mode public.evidence_window_mode not null,
  purpose text not null,
  authorized_caller text not null,
  domain_identities jsonb not null,
  query_contract jsonb not null,
  allowed_source_classes text[] not null,
  starts_at timestamptz not null,
  accepted_through timestamptz not null,
  browser_accepted_through bigint not null,
  server_accepted_through bigint not null,
  completeness jsonb not null,
  correlation_revision bigint not null,
  revision bigint not null default 0,
  frozen_at timestamptz,
  -- Set when a control-plane decision cites this telemetry window through the
  -- kernel's own immutable evidence_windows row.
  control_plane_evidence_window_id uuid references public.evidence_windows (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  constraint telemetry_evidence_windows_frozen_has_time
    check ((mode = 'frozen') = (frozen_at is not null)),
  constraint telemetry_evidence_windows_ordered check (accepted_through >= starts_at),
  constraint telemetry_evidence_windows_source_classes_present
    check (array_length(allowed_source_classes, 1) >= 1)
);
create index telemetry_evidence_windows_signal_idx
  on public.telemetry_evidence_windows (product_signal_id, mode, created_at desc);

-- Every promoted item keeps its canonical record or artifact reference, its exact
-- source record IDs, scope, versions, revisions, watermarks, and the claim it
-- supports or contradicts.
create table public.telemetry_evidence_items (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  evidence_window_id uuid not null references public.telemetry_evidence_windows (id),
  source_class public.telemetry_source_class not null,
  source_record_id uuid not null,
  source_observation_id uuid,
  trace_id text,
  span_id text,
  target_id text,
  artifact_id uuid references public.telemetry_artifacts (id),
  claim_ref text not null,
  supports boolean not null,
  policy_version text not null,
  source_schema_version text not null,
  correlation_revision bigint not null,
  watermarks jsonb not null,
  promoted_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  unique (evidence_window_id, source_class, source_record_id, claim_ref)
);
create index telemetry_evidence_items_window_idx
  on public.telemetry_evidence_items (evidence_window_id, source_class);

create trigger telemetry_evidence_items_immutable
  before update or delete on public.telemetry_evidence_items
  for each row execute function utrace.reject_mutation();

-- A readiness projection is append-only: each revision is a new row, so the
-- revision sequence is monotonic by construction and its history is auditable.
-- Replacing a stream or resource invalidates the previous projection.
create table public.telemetry_readiness_projections (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  activation_id uuid not null references public.telemetry_activations (id),
  interview_session_id uuid not null references public.interview_sessions (id),
  kind public.telemetry_readiness_kind not null,
  revision bigint not null,
  ready boolean not null,
  contributing_resource_ids jsonb not null,
  accepted_watermarks jsonb not null,
  probe_id uuid,
  failures jsonb not null,
  checked_at timestamptz not null default now(),
  expires_at timestamptz not null,
  schema_version smallint not null default 1,
  constraint telemetry_readiness_revision_positive check (revision >= 1),
  unique (client_id, activation_id, kind, revision)
);
create index telemetry_readiness_current_idx
  on public.telemetry_readiness_projections (activation_id, kind, revision desc);
create index telemetry_readiness_session_idx
  on public.telemetry_readiness_projections (interview_session_id, kind, revision desc);

create trigger telemetry_readiness_projections_immutable
  before update or delete on public.telemetry_readiness_projections
  for each row execute function utrace.reject_mutation();

alter table public.correlation_projections enable row level security;
alter table public.telemetry_evidence_windows enable row level security;
alter table public.telemetry_evidence_items enable row level security;
alter table public.telemetry_readiness_projections enable row level security;

revoke all on table public.correlation_projections from anon, authenticated;
revoke all on table public.telemetry_evidence_windows from anon, authenticated;
revoke all on table public.telemetry_evidence_items from anon, authenticated;
revoke all on table public.telemetry_readiness_projections from anon, authenticated;
