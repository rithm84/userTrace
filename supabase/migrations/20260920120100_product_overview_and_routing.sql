-- The immutable Product Overview Version a routing batch pins, and the
-- append-only routing decision record that carries the Jev answers, their
-- acceptance, and any conditional-assistance reason.
--
-- Contracts: docs/arch-components/context-assembly.md#product-overview-lifecycle
--            docs/arch-components/signal-and-outreach.md#routing-context-and-cognition

create type public.product_overview_state as enum ('active', 'superseded');

create table public.product_overview_versions (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  version_number bigint not null,
  state public.product_overview_state not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  index_revision text not null,
  index_name text not null,
  content_digest text not null,
  document_count integer not null,
  overview jsonb not null,
  sources jsonb not null,
  activated_at timestamptz,
  superseded_at timestamptz,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (client_id, version_number),
  unique (client_id, index_revision),
  constraint product_overview_versions_document_count_positive
    check (document_count > 0)
);

-- At most one active overview per client; routing waits when none exists.
create unique index product_overview_versions_one_active
  on public.product_overview_versions (client_id)
  where state = 'active';

-- One record per routing evaluation, including the ones that blocked. A second
-- evaluation after conditional assistance appends another row rather than
-- rewriting the first.
create table public.routing_decisions (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  intake_item_id uuid not null references public.intake_items (id),
  attempt_index integer not null,
  outcome public.routing_outcome,
  blocked_reason text,
  product_signal_id uuid references public.product_signals (id),
  source_report_id uuid references public.source_reports (id),
  pinned_configuration_release_id uuid not null
    references public.configuration_releases (id),
  product_overview_version_id uuid not null
    references public.product_overview_versions (id),
  decision_model_id text not null,
  questions jsonb not null,
  answers jsonb not null,
  acceptance jsonb not null,
  assistance_reason text,
  -- Opus 5.5's labelled restatement of the batch, when assistance needed one.
  assisted_interpretation text,
  decided_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (intake_item_id, attempt_index),
  constraint routing_decisions_attempt_index_positive check (attempt_index > 0),
  constraint routing_decisions_outcome_or_blocker
    check (num_nonnulls(outcome, blocked_reason) = 1)
);
create index routing_decisions_client_idx
  on public.routing_decisions (client_id, decided_at desc);

create trigger routing_decisions_immutable
  before update or delete on public.routing_decisions
  for each row execute function utrace.reject_mutation();

alter table public.product_overview_versions enable row level security;
alter table public.routing_decisions enable row level security;

revoke all on table public.product_overview_versions from anon, authenticated;
revoke all on table public.routing_decisions from anon, authenticated;

grant select on table public.product_overview_versions to authenticated;

create policy product_overview_versions_member_select on public.product_overview_versions
  for select to authenticated
  using (utrace.is_client_member(client_id));

