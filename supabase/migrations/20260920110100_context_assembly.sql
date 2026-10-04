-- Revisioned, source-linked context bundles, the omitted-source records that keep
-- a budget honest, the typed Jev judgements assembled from them, the holds an
-- unresolved contradiction raises, and the invalidation a correction causes.
--
-- Contracts: docs/arch-components/context-assembly.md#provenance-and-concurrent-results
--            docs/arch-components/interview-and-voice.md#concurrent-work-and-corrections
--            docs/arch-components/cross-component-contracts.md#jev-decision-contract

-- A bundle is the immutable input one worker received. `dependencies` records the
-- revisions it was assembled against so a later result can be rejected as stale.
create table public.context_bundles (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  interview_session_id uuid references public.interview_sessions (id),
  purpose public.context_purpose not null,
  context_revision bigint not null,
  cutoff_at timestamptz not null,
  budget_items integer not null,
  included_items integer not null,
  omitted_items integer not null,
  dependencies jsonb not null,
  digest text not null,
  created_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  unique (product_signal_id, purpose, context_revision),
  constraint context_bundles_budget_positive check (budget_items > 0),
  constraint context_bundles_counts_nonnegative
    check (included_items >= 0 and omitted_items >= 0),
  constraint context_bundles_within_budget check (included_items <= budget_items)
);
create index context_bundles_signal_idx
  on public.context_bundles (product_signal_id, purpose, context_revision desc);

create trigger context_bundles_immutable
  before update or delete on public.context_bundles
  for each row execute function utrace.reject_mutation();

-- One item per source the assembler considered. An omitted item keeps its
-- identity and reason and carries no body, so a dropped source stays visible.
create table public.context_bundle_items (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  context_bundle_id uuid not null references public.context_bundles (id),
  ordinal integer not null,
  section public.context_section not null,
  source_kind text not null,
  source_id text not null,
  source_revision text not null,
  included boolean not null,
  omission_reason text,
  weight integer not null,
  body jsonb not null,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (context_bundle_id, ordinal),
  constraint context_bundle_items_omission_reason
    check (included = (omission_reason is null)),
  constraint context_bundle_items_weight_positive check (weight > 0)
);
create index context_bundle_items_source_idx
  on public.context_bundle_items (source_kind, source_id);

create trigger context_bundle_items_immutable
  before update or delete on public.context_bundle_items
  for each row execute function utrace.reject_mutation();

-- A correction or a changed observation invalidates the bundles that cited it.
-- The bundle itself stays immutable and diagnostic; this row withdraws its
-- authority to cause an effect.
create table public.context_bundle_invalidations (
  context_bundle_id uuid primary key references public.context_bundles (id),
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  reason text not null,
  caused_by_evidence_id uuid references public.evidence_records (id),
  invalidated_at timestamptz not null default now(),
  schema_version smallint not null default 1
);
create index context_bundle_invalidations_signal_idx
  on public.context_bundle_invalidations (product_signal_id, invalidated_at desc);

create trigger context_bundle_invalidations_immutable
  before update or delete on public.context_bundle_invalidations
  for each row execute function utrace.reject_mutation();

-- One batched Jev decision with its pinned model, the answers it returned, and
-- the acceptance verdict code reached. Advisory: it authorizes nothing.
create table public.semantic_judgements (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  interview_session_id uuid references public.interview_sessions (id),
  context_bundle_id uuid not null references public.context_bundles (id),
  purpose public.attention_purpose not null,
  model_id text not null,
  question_ids text[] not null,
  answers jsonb not null,
  acceptance jsonb not null,
  accepted boolean not null,
  rejections text[] not null,
  created_at timestamptz not null default now(),
  schema_version smallint not null default 1
);
create index semantic_judgements_signal_idx
  on public.semantic_judgements (product_signal_id, purpose, created_at desc);

create trigger semantic_judgements_immutable
  before update or delete on public.semantic_judgements
  for each row execute function utrace.reject_mutation();

-- A plausible contradiction flag holds new dependent implementation steps while
-- interpretation or participant clarification resolves it. An accepted
-- correction is the stronger form and invalidates outright.
create table public.dependency_holds (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  interview_session_id uuid references public.interview_sessions (id),
  hold_kind public.dependency_hold_kind not null,
  reason text not null,
  raised_by_evidence_id uuid not null references public.evidence_records (id),
  affected_evidence_id uuid references public.evidence_records (id),
  semantic_judgement_id uuid references public.semantic_judgements (id),
  blocks_new_implementation_steps boolean not null,
  released_at timestamptz,
  release_reason text,
  created_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  constraint dependency_holds_release_reason check ((released_at is null) = (release_reason is null))
);
create index dependency_holds_open_idx
  on public.dependency_holds (product_signal_id)
  where released_at is null;

-- The interview-established flow classification, one immutable row per revision.
create table public.flow_classification_records (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  interview_session_id uuid references public.interview_sessions (id),
  classification_revision bigint not null,
  flow_type public.flow_type not null,
  initial_flow_type public.flow_type not null,
  retained boolean not null,
  rationale text not null,
  supporting_evidence_ids uuid[] not null,
  context_bundle_id uuid not null references public.context_bundles (id),
  semantic_judgement_id uuid references public.semantic_judgements (id),
  recorded_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  unique (product_signal_id, classification_revision),
  constraint flow_classification_retained_matches
    check (retained = (flow_type = initial_flow_type))
);

create trigger flow_classification_records_immutable
  before update or delete on public.flow_classification_records
  for each row execute function utrace.reject_mutation();

-- The agreed scope and acceptance criteria at one criteria revision. The frozen
-- criteria themselves stay in public.validation_criteria.
create table public.acceptance_criteria_agreements (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  interview_session_id uuid not null references public.interview_sessions (id),
  acceptance_criteria_revision bigint not null,
  agreed_scope jsonb not null,
  criterion_ids uuid[] not null,
  supporting_evidence_ids uuid[] not null,
  context_bundle_id uuid not null references public.context_bundles (id),
  recorded_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  unique (product_signal_id, acceptance_criteria_revision)
);

create trigger acceptance_criteria_agreements_immutable
  before update or delete on public.acceptance_criteria_agreements
  for each row execute function utrace.reject_mutation();

-- Context, judgements, and holds are internal: RLS on with no policy, so no
-- browser role reads them and internal writes use the privileged identity.
alter table public.context_bundles enable row level security;
alter table public.context_bundle_items enable row level security;
alter table public.context_bundle_invalidations enable row level security;
alter table public.semantic_judgements enable row level security;
alter table public.dependency_holds enable row level security;
alter table public.flow_classification_records enable row level security;
alter table public.acceptance_criteria_agreements enable row level security;

revoke all on table public.context_bundles from anon, authenticated;
revoke all on table public.context_bundle_items from anon, authenticated;
revoke all on table public.context_bundle_invalidations from anon, authenticated;
revoke all on table public.semantic_judgements from anon, authenticated;
revoke all on table public.dependency_holds from anon, authenticated;
revoke all on table public.flow_classification_records from anon, authenticated;
revoke all on table public.acceptance_criteria_agreements from anon, authenticated;
