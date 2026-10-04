-- Content-addressed artifacts, read-only code investigation findings, and the
-- attempt-scoped records the implementation controller writes.
--
-- Contracts: docs/arch-components/data-and-schema.md#evidence-and-artifacts
--            docs/arch-components/implementation.md#artifact-extraction-and-cleanup
--            docs/arch-components/context-assembly.md#code-investigation
--            docs/arch-components/preview-build.md#readiness-and-preview-version-lifecycle

-- The private, tenant-scoped bucket every implementation artifact body lives in.
-- Postgres holds the metadata; Storage holds the bytes addressed by digest.
insert into storage.buckets (id, name, public)
values ('utrace-artifacts', 'utrace-artifacts', false)
on conflict (id) do nothing;

-- Artifact metadata. The reference other components hold carries identity, type,
-- digest, size, tenant scope, and retention class, and never a bucket path or a
-- credential: resolution goes back through the control plane.
create table public.artifacts (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  artifact_type text not null,
  media_type text not null,
  digest_algorithm text not null default 'sha256',
  digest text not null,
  byte_size bigint not null,
  retention_class text not null,
  storage_bucket text not null,
  storage_path text not null,
  producer text not null,
  source_revision text,
  implementation_attempt_id uuid references public.implementation_attempts (id),
  environment_lease_id uuid references public.environment_leases (id),
  product_signal_id uuid references public.product_signals (id),
  legal_hold boolean not null default false,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (client_id, digest),
  constraint artifacts_digest_is_sha256 check (digest ~ '^[0-9a-f]{64}$'),
  constraint artifacts_byte_size_nonnegative check (byte_size >= 0),
  constraint artifacts_retention_class_known check (
    retention_class in (
      'participant_personal', 'client_evidence', 'operational', 'audit_minimized'
    )
  )
);
create index artifacts_attempt_idx
  on public.artifacts (implementation_attempt_id, artifact_type);
create index artifacts_signal_idx on public.artifacts (product_signal_id);

-- An accepted artifact is immutable. Only the retention columns may move, and
-- only through the retention path, which this workstream does not own.
create trigger artifacts_immutable_body
  before delete on public.artifacts
  for each row execute function utrace.reject_mutation();

-- Exact-revision findings from the read-only pre-gate investigation. There is no
-- write lease behind these rows: they record what was read, where, and at which
-- revision, so a gate decision can cite code without trusting a model's memory.
create table public.code_investigation_findings (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  investigation_id uuid not null,
  repository_ref text not null,
  base_revision text not null,
  path text not null,
  first_line integer not null,
  last_line integer not null,
  excerpt_digest text not null,
  note text not null,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  constraint code_investigation_findings_line_range
    check (first_line >= 1 and last_line >= first_line)
);
create index code_investigation_findings_signal_idx
  on public.code_investigation_findings (product_signal_id, path);
create index code_investigation_findings_investigation_idx
  on public.code_investigation_findings (investigation_id);

create trigger code_investigation_findings_immutable
  before update or delete on public.code_investigation_findings
  for each row execute function utrace.reject_mutation();

-- One row per required check run, on the clean base and again on the candidate.
-- The candidate must introduce no new failure; a pre-existing failure is recorded
-- as integration health rather than hidden.
create table public.implementation_check_runs (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  implementation_attempt_id uuid not null references public.implementation_attempts (id),
  environment_lease_id uuid not null references public.environment_leases (id),
  phase text not null,
  check_name text not null,
  command text not null,
  exit_code integer not null,
  duration_seconds double precision not null,
  log_artifact_id uuid references public.artifacts (id),
  cycle_index integer not null,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  constraint implementation_check_runs_phase_known check (phase in ('baseline', 'candidate'))
);
create index implementation_check_runs_attempt_idx
  on public.implementation_check_runs (implementation_attempt_id, phase, cycle_index);

-- One row per diff-versus-approved-scope check, written after every edit cycle.
create table public.implementation_scope_checks (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  implementation_attempt_id uuid not null references public.implementation_attempts (id),
  cycle_index integer not null,
  passed boolean not null,
  scope_digest text not null,
  changed_paths text[] not null,
  violations jsonb not null,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now()
);
create index implementation_scope_checks_attempt_idx
  on public.implementation_scope_checks (implementation_attempt_id, cycle_index);

create trigger implementation_scope_checks_immutable
  before update or delete on public.implementation_scope_checks
  for each row execute function utrace.reject_mutation();

-- Preview Version columns the publication path needs: the artifact manifest it
-- binds, the build recipe the candidate runtime replays, the check evidence, and
-- the later revision that superseded it.
alter table public.preview_versions
  add column artifact_manifest_digest text,
  add column if not exists build_recipe jsonb,
  add column check_summary jsonb,
  add column superseded_by_preview_version_id uuid
    references public.preview_versions (id);

-- The durable checkpoint a paused, departed, or infrastructure-blocked attempt
-- resumes from, and the budget ledger every run recomputes limits against.
alter table public.implementation_attempts
  add column progress text not null default 'not_started',
  add column workspace_ref text,
  add column workspace_snapshot_id text;

alter table public.artifacts enable row level security;
alter table public.code_investigation_findings enable row level security;
alter table public.implementation_check_runs enable row level security;
alter table public.implementation_scope_checks enable row level security;

revoke all on table public.artifacts from anon, authenticated;
revoke all on table public.code_investigation_findings from anon, authenticated;
revoke all on table public.implementation_check_runs from anon, authenticated;
revoke all on table public.implementation_scope_checks from anon, authenticated;

grant select on table public.implementation_check_runs to authenticated;
grant select on table public.implementation_scope_checks to authenticated;

create policy implementation_check_runs_member_select on public.implementation_check_runs
  for select to authenticated
  using (utrace.is_client_member(client_id));

create policy implementation_scope_checks_member_select on public.implementation_scope_checks
  for select to authenticated
  using (utrace.is_client_member(client_id));
