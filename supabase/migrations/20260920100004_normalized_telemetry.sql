-- Normalized telemetry: browser streams and observations, server streams and
-- spans, explicit delivery gaps, and the integrity failures that identity reuse
-- and cross-activation trace claims produce.
--
-- Canonical records preserve source IDs, trace and span IDs, original timestamps,
-- source sequences, source schema versions, policy versions, and provenance.
-- Ingestion adds trusted receipt time, canonical schema version, normalization
-- run, and validation outcome without overwriting source facts.
--
-- Contract: docs/arch-components/telemetry-and-evidence.md#validation-and-normalization

create table public.browser_streams (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  activation_id uuid not null references public.telemetry_activations (id),
  runtime_id uuid not null,
  document_attachment_id uuid not null,
  state public.telemetry_stream_state not null default 'open',
  revision bigint not null default 0,
  -- Acknowledgement advances only to the highest contiguous accepted sequence.
  accepted_through_export_sequence bigint not null default 0,
  highest_received_export_sequence bigint not null default 0,
  terminal_export_sequence bigint,
  end_reason text,
  opened_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  constraint browser_streams_watermarks_ordered
    check (accepted_through_export_sequence <= highest_received_export_sequence)
);
create index browser_streams_activation_idx
  on public.browser_streams (activation_id, state);

create table public.browser_observations (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  activation_id uuid not null references public.telemetry_activations (id),
  browser_stream_id uuid not null references public.browser_streams (id),
  ingestion_receipt_id uuid not null references public.ingestion_receipts (id),

  observation_id uuid not null,
  observation_type text not null,
  runtime_id uuid not null,
  document_attachment_id uuid not null,
  frame_id text not null,
  source_sequence bigint not null,
  export_sequence bigint not null,
  occurred_at timestamptz not null,
  monotonic_offset double precision not null,
  route_revision bigint not null,
  visibility_state text not null,
  target_id text,
  target_revision bigint,
  trace_id text,
  span_id text,
  browser_request_id text,
  payload jsonb not null,

  policy_version text not null,
  redaction_version text not null,
  source_schema_version text not null,
  canonical_schema_version text not null,
  normalization_run_id uuid not null,
  content_sha256 text not null,
  received_at timestamptz not null,
  accepted_at timestamptz not null default now(),
  schema_version smallint not null default 1,

  constraint browser_observations_trace_id_format
    check (trace_id is null or trace_id ~ '^[0-9a-f]{32}$'),
  constraint browser_observations_span_id_format
    check (span_id is null or span_id ~ '^[0-9a-f]{16}$')
);

-- A browser record deduplicates by observation_id inside its client scope.
create unique index browser_observations_identity
  on public.browser_observations (client_id, observation_id);
create index browser_observations_stream_idx
  on public.browser_observations (browser_stream_id, export_sequence);
create index browser_observations_trace_idx
  on public.browser_observations (client_id, trace_id)
  where trace_id is not null;
create index browser_observations_target_idx
  on public.browser_observations (client_id, target_id, occurred_at)
  where target_id is not null;
create index browser_observations_request_idx
  on public.browser_observations (client_id, browser_request_id)
  where browser_request_id is not null;

create trigger browser_observations_immutable
  before update or delete on public.browser_observations
  for each row execute function utrace.reject_mutation();

-- A dropped low-priority range is explicit. Critical records are never silently
-- discarded, and a gap never advances a completeness claim across it.
create table public.browser_stream_gaps (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  browser_stream_id uuid not null references public.browser_streams (id),
  ingestion_receipt_id uuid not null references public.ingestion_receipts (id),
  gap_runtime_id uuid not null,
  first_source_sequence bigint not null,
  last_source_sequence bigint not null,
  record_count integer not null,
  observation_class text not null,
  reason text not null,
  baseline_follows boolean not null,
  resolved boolean not null default false,
  recorded_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  constraint browser_stream_gaps_range_ordered
    check (last_source_sequence >= first_source_sequence)
);
create index browser_stream_gaps_stream_idx
  on public.browser_stream_gaps (browser_stream_id, first_source_sequence);

create table public.server_streams (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  activation_id uuid not null references public.telemetry_activations (id),
  service_instance_id uuid not null,
  service_key text not null,
  state public.telemetry_stream_state not null default 'open',
  revision bigint not null default 0,
  accepted_through_batch_sequence bigint not null default 0,
  highest_received_batch_sequence bigint not null default 0,
  clean boolean,
  opened_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  schema_version smallint not null default 1
);
create index server_streams_activation_idx on public.server_streams (activation_id, state);

create table public.server_spans (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  activation_id uuid not null references public.telemetry_activations (id),
  server_stream_id uuid not null references public.server_streams (id),
  ingestion_receipt_id uuid not null references public.ingestion_receipts (id),

  trace_id text not null,
  span_id text not null,
  parent_span_id text,
  span_name text not null,
  span_kind text not null,
  start_time_unix_nano bigint not null,
  end_time_unix_nano bigint not null,
  status_code text not null,
  scope_name text not null,
  scope_version text,
  service_name text not null,
  service_namespace text not null,
  service_version text not null,
  deployment_environment_name text not null,
  attributes jsonb not null,
  links jsonb not null,
  span_events jsonb not null,
  registered_operation text,
  http_route text,
  http_status_code integer,

  source_schema_version text not null,
  canonical_schema_version text not null,
  semantic_convention_version text not null,
  policy_version text not null,
  normalization_run_id uuid not null,
  content_sha256 text not null,
  received_at timestamptz not null,
  accepted_at timestamptz not null default now(),
  schema_version smallint not null default 1,

  constraint server_spans_trace_id_format check (trace_id ~ '^[0-9a-f]{32}$'),
  constraint server_spans_span_id_format check (span_id ~ '^[0-9a-f]{16}$'),
  constraint server_spans_parent_span_id_format
    check (parent_span_id is null or parent_span_id ~ '^[0-9a-f]{16}$'),
  constraint server_spans_interval_ordered
    check (end_time_unix_nano >= start_time_unix_nano)
);

-- A server span deduplicates by authenticated activation plus trace and span ID.
create unique index server_spans_identity
  on public.server_spans (client_id, activation_id, trace_id, span_id);
create index server_spans_trace_idx on public.server_spans (client_id, trace_id);
create index server_spans_parent_idx
  on public.server_spans (client_id, trace_id, parent_span_id)
  where parent_span_id is not null;
create index server_spans_operation_idx
  on public.server_spans (client_id, registered_operation, accepted_at)
  where registered_operation is not null;

create trigger server_spans_immutable
  before update or delete on public.server_spans
  for each row execute function utrace.reject_mutation();

-- Reuse of an identity with different content leaves both versions as
-- non-authoritative diagnostic facts and fails the source stream. Ingestion never
-- chooses the later arrival.
create table public.telemetry_identity_conflicts (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  activation_id uuid not null references public.telemetry_activations (id),
  producer_kind public.telemetry_producer_kind not null,
  source_stream_id uuid not null,
  identity_kind text not null,
  identity_value text not null,
  first_content_sha256 text not null,
  conflicting_content_sha256 text not null,
  first_ingestion_receipt_id uuid not null references public.ingestion_receipts (id),
  conflicting_ingestion_receipt_id uuid references public.ingestion_receipts (id),
  detected_at timestamptz not null default now(),
  schema_version smallint not null default 1
);
create index telemetry_identity_conflicts_stream_idx
  on public.telemetry_identity_conflicts (source_stream_id, detected_at desc);

create trigger telemetry_identity_conflicts_immutable
  before update or delete on public.telemetry_identity_conflicts
  for each row execute function utrace.reject_mutation();

alter table public.browser_streams enable row level security;
alter table public.browser_observations enable row level security;
alter table public.browser_stream_gaps enable row level security;
alter table public.server_streams enable row level security;
alter table public.server_spans enable row level security;
alter table public.telemetry_identity_conflicts enable row level security;

revoke all on table public.browser_streams from anon, authenticated;
revoke all on table public.browser_observations from anon, authenticated;
revoke all on table public.browser_stream_gaps from anon, authenticated;
revoke all on table public.server_streams from anon, authenticated;
revoke all on table public.server_spans from anon, authenticated;
revoke all on table public.telemetry_identity_conflicts from anon, authenticated;
