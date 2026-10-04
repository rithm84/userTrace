-- Execution core: the append-only event log, Jobs and their leased runs, Commands
-- and their single terminal Result, the transactional outbox, the deduplicating
-- inbox, and the append-only audit record.
--
-- Contract: docs/arch-components/control-plane.md#jobs-commands-and-scheduling

-- Accepted facts. `id` is the envelope message_id, so replays deduplicate, and
-- `sequence` is monotonic within one primary lifecycle stream.
create table public.events (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  stream_kind public.aggregate_kind not null,
  stream_id uuid not null,
  sequence bigint not null,
  event_type text not null,
  payload jsonb not null,
  actor_kind public.actor_kind not null,
  actor_ref text,
  causation_id uuid,
  correlation_id uuid,
  occurred_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (stream_kind, stream_id, sequence),
  constraint events_sequence_positive check (sequence > 0)
);
create index events_client_stream_idx
  on public.events (client_id, stream_kind, stream_id, sequence);

create trigger events_immutable
  before update or delete on public.events
  for each row execute function utrace.reject_mutation();

create table public.jobs (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  job_type text not null,
  state public.job_state not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  input jsonb not null,
  deadline_at timestamptz not null,
  timeout_seconds integer not null,
  max_runs integer not null,
  run_count integer not null default 0,
  resource_budget jsonb not null,
  retryable_categories text[] not null,
  backoff_base_seconds integer not null,
  backoff_max_seconds integer not null,
  concurrency_key text,
  cancellation_scope_id uuid,
  not_before timestamptz not null default now(),
  last_failure_category text,
  last_retry_advice public.retry_advice,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint jobs_max_runs_positive check (max_runs > 0),
  constraint jobs_run_count_within_max check (run_count >= 0 and run_count <= max_runs),
  constraint jobs_timeout_positive check (timeout_seconds > 0),
  constraint jobs_backoff_ordered
    check (backoff_base_seconds > 0 and backoff_max_seconds >= backoff_base_seconds)
);
create index jobs_claimable_idx
  on public.jobs (not_before, created_at)
  where state = 'ready';
create index jobs_cancellation_scope_idx
  on public.jobs (cancellation_scope_id)
  where state in ('pending', 'ready', 'leased');

-- At most one leased Job per concurrency key.
create unique index jobs_one_leased_per_concurrency_key
  on public.jobs (client_id, concurrency_key)
  where state = 'leased' and concurrency_key is not null;

-- One leased execution of a Job. The fencing token is globally monotonic.
create table public.job_runs (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  job_id uuid not null references public.jobs (id),
  state public.job_run_state not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  fencing_token bigint not null,
  worker_id text not null,
  lease_expires_at timestamptz not null,
  heartbeat_at timestamptz not null default now(),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  failure_category text,
  retry_advice public.retry_advice,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (job_id, fencing_token)
);
create unique index job_runs_one_leased
  on public.job_runs (job_id)
  where state = 'leased';
create index job_runs_lease_scan_idx
  on public.job_runs (lease_expires_at)
  where state = 'leased';

-- `id` is the command_id and the provider idempotency key.
create table public.commands (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  command_type text not null,
  state public.command_state not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  target_kind public.aggregate_kind not null,
  target_id uuid not null,
  payload jsonb not null,
  declared_revisions jsonb not null,
  cancellation_scope_id uuid,
  provider_ref text,
  queued_at timestamptz,
  dispatching_at timestamptz,
  dispatched_at timestamptz,
  acknowledged_at timestamptz,
  reconciling_at timestamptz,
  terminal_at timestamptz,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index commands_target_idx on public.commands (target_kind, target_id);
create index commands_cancellation_scope_idx
  on public.commands (cancellation_scope_id)
  where state <> 'terminal';

alter table public.offer_issuance_operations
  add constraint offer_issuance_operations_command_fk
  foreign key (command_id) references public.commands (id);

-- Accepted, progress, or exactly one canonical terminal Result per Command.
create table public.results (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  command_id uuid not null references public.commands (id),
  kind public.result_kind not null,
  disposition public.command_disposition,
  effect_state public.effect_state,
  technical_category text,
  retry_advice public.retry_advice,
  payload jsonb not null,
  received_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  constraint results_terminal_fields
    check ((kind = 'terminal') = (disposition is not null)),
  constraint results_terminal_effect_state
    check ((kind = 'terminal') = (effect_state is not null))
);
create unique index results_one_terminal
  on public.results (command_id)
  where kind = 'terminal';
create index results_command_idx on public.results (command_id, received_at);

create trigger results_immutable
  before update or delete on public.results
  for each row execute function utrace.reject_mutation();

-- The only path from committed workflow state to external effects.
create table public.outbox (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  state public.outbox_state not null,
  job_id uuid references public.jobs (id),
  command_id uuid references public.commands (id),
  available_at timestamptz not null default now(),
  enqueued_at timestamptz,
  dispatched_at timestamptz,
  attempts integer not null default 0,
  last_error text,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint outbox_exactly_one_intent check (num_nonnulls(job_id, command_id) = 1)
);
create index outbox_pending_idx
  on public.outbox (available_at, created_at)
  where state = 'pending';

-- Inbound provider receipts and Results deduplicate by message_id, and a Command
-- accepts only one terminal receipt.
create table public.inbox (
  message_id uuid primary key,
  client_id uuid not null references public.clients (id),
  source text not null,
  message_kind text not null,
  command_id uuid references public.commands (id),
  payload jsonb not null,
  received_at timestamptz not null default now(),
  processed_at timestamptz,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now()
);
create unique index inbox_one_terminal_per_command
  on public.inbox (command_id)
  where message_kind = 'terminal_result' and command_id is not null;
create index inbox_unprocessed_idx
  on public.inbox (received_at)
  where processed_at is null;

create table public.audit_records (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  subject_kind public.aggregate_kind not null,
  subject_id uuid not null,
  action text not null,
  outcome text not null,
  reason_code text,
  actor_kind public.actor_kind not null,
  actor_ref text,
  lifecycle_revision bigint,
  pinned_configuration_release_id uuid references public.configuration_releases (id),
  detail jsonb not null,
  recorded_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  created_at timestamptz not null default now()
);
create index audit_records_subject_idx
  on public.audit_records (subject_kind, subject_id, recorded_at);
create index audit_records_client_idx on public.audit_records (client_id, recorded_at);

create trigger audit_records_immutable
  before update or delete on public.audit_records
  for each row execute function utrace.reject_mutation();

-- Execution and audit rows are internal: RLS is enabled with no policy, so no
-- browser role can read them, and internal writes use the privileged identity.
alter table public.events enable row level security;
alter table public.jobs enable row level security;
alter table public.job_runs enable row level security;
alter table public.commands enable row level security;
alter table public.results enable row level security;
alter table public.outbox enable row level security;
alter table public.inbox enable row level security;
alter table public.audit_records enable row level security;

revoke all on table public.events from anon, authenticated;
revoke all on table public.jobs from anon, authenticated;
revoke all on table public.job_runs from anon, authenticated;
revoke all on table public.commands from anon, authenticated;
revoke all on table public.results from anon, authenticated;
revoke all on table public.outbox from anon, authenticated;
revoke all on table public.inbox from anon, authenticated;
revoke all on table public.audit_records from anon, authenticated;
