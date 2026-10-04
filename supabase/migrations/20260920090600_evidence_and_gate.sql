-- Append-only evidence ledger, immutable evidence windows, immutable Implementation
-- Gate decisions, and the separate pending async authorization record.
--
-- Contracts: docs/arch-components/policy-and-permissions.md#implementation-gate-contract
--            docs/arch-components/implementation.md#async-authorization-before-dispatch

-- Corrections append superseding rows; source records are never overwritten.
create table public.evidence_records (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  interview_session_id uuid references public.interview_sessions (id),
  evidence_class public.evidence_class not null,
  claim_state public.evidence_claim_state not null,
  source_ref text not null,
  context_revision bigint not null,
  artifact_ref jsonb,
  body jsonb not null,
  supersedes_id uuid references public.evidence_records (id),
  recorded_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  created_at timestamptz not null default now()
);
create index evidence_records_signal_idx
  on public.evidence_records (product_signal_id, evidence_class);

create trigger evidence_records_immutable
  before update or delete on public.evidence_records
  for each row execute function utrace.reject_mutation();

-- An evidence window records the admissible range for one decision. It is written
-- with both bounds known, so the row is immutable on insert.
create table public.evidence_windows (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  interview_session_id uuid references public.interview_sessions (id),
  window_kind text not null,
  starts_at timestamptz not null,
  ends_at timestamptz not null,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  constraint evidence_windows_ordered check (ends_at >= starts_at)
);
create index evidence_windows_signal_idx on public.evidence_windows (product_signal_id);

create trigger evidence_windows_immutable
  before update or delete on public.evidence_windows
  for each row execute function utrace.reject_mutation();

create table public.gate_decisions (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  decision public.gate_decision_kind not null,
  blocker_reason text,
  classification_revision bigint not null,
  acceptance_criteria_revision bigint not null,
  pinned_configuration_release_id uuid not null
    references public.configuration_releases (id),
  evidence_window_id uuid not null references public.evidence_windows (id),
  approved_scope jsonb not null,
  required_checks jsonb not null,
  budgets jsonb not null,
  cited_evidence jsonb not null,
  decided_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  constraint gate_decisions_blocker_only_when_denied
    check ((decision = 'no_implementation') = (blocker_reason is not null))
);
create index gate_decisions_signal_idx
  on public.gate_decisions (product_signal_id, decided_at desc);

create trigger gate_decisions_immutable
  before update or delete on public.gate_decisions
  for each row execute function utrace.reject_mutation();

alter table public.evidence_records enable row level security;
alter table public.evidence_windows enable row level security;
alter table public.gate_decisions enable row level security;

revoke all on table public.evidence_records from anon, authenticated;
revoke all on table public.evidence_windows from anon, authenticated;
revoke all on table public.gate_decisions from anon, authenticated;

grant select on table public.gate_decisions to authenticated;

create policy gate_decisions_member_select on public.gate_decisions
  for select to authenticated
  using (utrace.is_client_member(client_id));
