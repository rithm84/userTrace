-- The durable ingress journal and the immutable artifact metadata that large
-- payloads use.
--
-- An acknowledgement means the authenticated batch passed the ingress checks and
-- was committed here. Receipt by a load balancer, a WebSocket process, an OTLP
-- receiver, or an in-memory queue is not enough.
--
-- Contracts: docs/arch-components/telemetry-and-evidence.md#durable-receipt-and-acknowledgement
--            docs/arch-components/cross-component-contracts.md#artifact-references

-- Artifact content is immutable and addressed by digest. The relational row holds
-- no bucket name, path, URL, or credential: the object key is derived from the
-- client scope and the artifact identity at resolution time.
create table public.telemetry_artifacts (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  artifact_class text not null,
  media_type text not null,
  schema_type text not null,
  byte_size bigint not null,
  content_sha256 text not null,
  chunk_manifest jsonb,
  retention_class text not null,
  created_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  constraint telemetry_artifacts_digest_format check (content_sha256 ~ '^[0-9a-f]{64}$'),
  constraint telemetry_artifacts_size_positive check (byte_size > 0)
);

-- Content-addressed reuse never crosses a client scope, so identical bytes in two
-- tenants stay independently authorized and deletable.
create unique index telemetry_artifacts_client_content
  on public.telemetry_artifacts (client_id, artifact_class, content_sha256);

create trigger telemetry_artifacts_immutable
  before update or delete on public.telemetry_artifacts
  for each row execute function utrace.reject_mutation();

create table public.ingestion_receipts (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  activation_id uuid not null references public.telemetry_activations (id),
  producer_kind public.telemetry_producer_kind not null,
  telemetry_credential_id uuid not null references public.telemetry_credentials (id),

  source_stream_id uuid not null,
  batch_id uuid not null,
  batch_sequence bigint,
  first_export_sequence bigint,
  last_export_sequence bigint,

  received_at timestamptz not null default now(),
  byte_count bigint not null,
  record_count integer not null,
  content_sha256 text not null,

  source_schema_version text not null,
  canonical_schema_version text not null,
  policy_version text not null,

  outcome public.ingestion_outcome not null,
  stage public.ingestion_stage not null,
  failure_code text,
  failure_reason text,
  retry_classification text,
  raw_artifact_id uuid references public.telemetry_artifacts (id),

  revision bigint not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  schema_version smallint not null default 1,

  constraint ingestion_receipts_failure_only_when_refused check (
    (outcome = 'accepted') = (failure_code is null)
  ),
  constraint ingestion_receipts_counts_nonnegative check (
    byte_count >= 0 and record_count >= 0
  )
);

-- A batch deduplicates by source stream and batch ID.
create unique index ingestion_receipts_stream_batch
  on public.ingestion_receipts (client_id, source_stream_id, batch_id);
create index ingestion_receipts_activation_idx
  on public.ingestion_receipts (activation_id, received_at desc);
create index ingestion_receipts_stage_idx
  on public.ingestion_receipts (outcome, stage, received_at);

-- Append-only stage log. The receipt's own stage column is the furthest stage
-- reached; this table records how it got there and which normalization run wrote
-- each derived output.
create table public.ingestion_receipt_stages (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  ingestion_receipt_id uuid not null references public.ingestion_receipts (id),
  stage public.ingestion_stage not null,
  normalization_run_id uuid,
  entered_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  unique (ingestion_receipt_id, stage)
);
create index ingestion_receipt_stages_receipt_idx
  on public.ingestion_receipt_stages (ingestion_receipt_id, entered_at);

create trigger ingestion_receipt_stages_immutable
  before update or delete on public.ingestion_receipt_stages
  for each row execute function utrace.reject_mutation();

-- Typed failures ingestion reports. A failure record carries safe identities,
-- stage, stable code, retry classification, affected watermarks, and readiness
-- consequence. It never contains the rejected product value.
create table public.ingestion_failures (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  activation_id uuid references public.telemetry_activations (id),
  ingestion_receipt_id uuid references public.ingestion_receipts (id),
  producer_kind public.telemetry_producer_kind not null,
  source_stream_id uuid,
  stage public.ingestion_stage not null,
  code text not null,
  retry_classification text not null,
  readiness_consequence text not null,
  affected_watermark bigint,
  affected_trace_id text,
  detail jsonb not null,
  occurred_at timestamptz not null default now(),
  schema_version smallint not null default 1
);
create index ingestion_failures_activation_idx
  on public.ingestion_failures (activation_id, occurred_at desc);
create index ingestion_failures_code_idx on public.ingestion_failures (code, occurred_at desc);

create trigger ingestion_failures_immutable
  before update or delete on public.ingestion_failures
  for each row execute function utrace.reject_mutation();

alter table public.telemetry_artifacts enable row level security;
alter table public.ingestion_receipts enable row level security;
alter table public.ingestion_receipt_stages enable row level security;
alter table public.ingestion_failures enable row level security;

revoke all on table public.telemetry_artifacts from anon, authenticated;
revoke all on table public.ingestion_receipts from anon, authenticated;
revoke all on table public.ingestion_receipt_stages from anon, authenticated;
revoke all on table public.ingestion_failures from anon, authenticated;
