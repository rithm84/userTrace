-- Validation Attempts with their criteria and criterion-level outcomes, Case Files,
-- and GitHub deliveries.
--
-- Contracts: docs/arch-components/validation.md#validation-binding
--            docs/arch-components/client-outputs.md#case-file-identity-and-assembly

-- Acceptance criteria are frozen per revision; a new revision appends new rows.
create table public.validation_criteria (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  criteria_revision bigint not null,
  ordinal integer not null,
  statement text not null,
  observability public.criterion_observability not null,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (product_signal_id, criteria_revision, ordinal)
);

create trigger validation_criteria_immutable
  before update or delete on public.validation_criteria
  for each row execute function utrace.reject_mutation();

create table public.validation_attempts (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  implementation_attempt_id uuid not null references public.implementation_attempts (id),
  preview_version_id uuid not null references public.preview_versions (id),
  preview_runtime_allocation_id uuid not null
    references public.preview_runtime_allocations (id),
  interview_session_id uuid not null references public.interview_sessions (id),
  client_user_id uuid not null references public.client_users (id),
  state public.validation_attempt_state not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  dataset_manifest_digest text not null,
  acceptance_criteria_revision bigint not null,
  outcome public.validation_outcome,
  completed_at timestamptz,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint validation_attempts_outcome_when_completed
    check ((state = 'completed') = (outcome is not null))
);
create index validation_attempts_signal_idx
  on public.validation_attempts (product_signal_id, state);

-- A referenced candidate cannot be replaced during a Validation Attempt.
create unique index validation_attempts_one_open
  on public.validation_attempts (product_signal_id)
  where state in ('pending', 'in_progress', 'interrupted');

create table public.validation_outcomes (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  validation_attempt_id uuid not null references public.validation_attempts (id),
  validation_criterion_id uuid not null references public.validation_criteria (id),
  result public.criterion_result not null,
  user_confirmed boolean not null,
  evidence_record_ids uuid[] not null,
  note text,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (validation_attempt_id, validation_criterion_id)
);

create trigger validation_outcomes_immutable
  before update or delete on public.validation_outcomes
  for each row execute function utrace.reject_mutation();

create table public.case_files (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null unique references public.product_signals (id),
  variant public.case_file_variant not null,
  stage public.case_file_stage not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  core jsonb,
  core_digest text,
  delivery_id uuid,
  sealed_at timestamptz,
  published_at timestamptz,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint case_files_sealed_core
    check ((stage in ('ready', 'published')) = (core_digest is not null))
);
create index case_files_client_stage_idx on public.case_files (client_id, stage);

create table public.deliveries (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  case_file_id uuid not null references public.case_files (id),
  validation_attempt_id uuid not null references public.validation_attempts (id),
  state public.delivery_state not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  repository_ref text not null,
  branch_ref text,
  head_commit text not null,
  base_revision text not null,
  pull_request_ref text,
  idempotency_marker text not null unique,
  blocker_reason text,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index deliveries_signal_idx on public.deliveries (product_signal_id, state);

alter table public.case_files
  add constraint case_files_delivery_fk
  foreign key (delivery_id) references public.deliveries (id);

alter table public.validation_criteria enable row level security;
alter table public.validation_attempts enable row level security;
alter table public.validation_outcomes enable row level security;
alter table public.case_files enable row level security;
alter table public.deliveries enable row level security;

revoke all on table public.validation_criteria from anon, authenticated;
revoke all on table public.validation_attempts from anon, authenticated;
revoke all on table public.validation_outcomes from anon, authenticated;
revoke all on table public.case_files from anon, authenticated;
revoke all on table public.deliveries from anon, authenticated;

grant select on table public.validation_criteria to authenticated;
grant select on table public.validation_attempts to authenticated;
grant select on table public.validation_outcomes to authenticated;
grant select on table public.case_files to authenticated;
grant select on table public.deliveries to authenticated;

create policy validation_criteria_member_select on public.validation_criteria
  for select to authenticated
  using (utrace.is_client_member(client_id));

create policy validation_attempts_member_select on public.validation_attempts
  for select to authenticated
  using (utrace.is_client_member(client_id));

create policy validation_outcomes_member_select on public.validation_outcomes
  for select to authenticated
  using (utrace.is_client_member(client_id));

-- Only sealed Case Files are visible to client staff.
create policy case_files_member_select on public.case_files
  for select to authenticated
  using (utrace.is_client_member(client_id) and stage = 'published');

create policy deliveries_member_select on public.deliveries
  for select to authenticated
  using (utrace.is_client_member(client_id));
