-- Intake Items, Product Signals, and each Product Signal's originating source report.
--
-- Contracts: docs/arch-components/signal-and-outreach.md#routing-contract
--            docs/arch-components/control-plane.md#product-lifecycles

create table public.intake_items (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  state public.intake_item_state not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  source_provider text not null,
  source_channel_ref text not null,
  source_batch_digest text not null,
  author_provider_identity text not null,
  client_user_id uuid references public.client_users (id),
  customer_account_id uuid references public.customer_accounts (id),
  payload jsonb not null,
  routing_outcome public.routing_outcome,
  product_signal_id uuid,
  blocked_reason text,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (client_id, source_batch_digest)
);
create index intake_items_client_state_idx on public.intake_items (client_id, state);

create table public.product_signals (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  state public.product_signal_state not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  intake_item_id uuid not null references public.intake_items (id),
  pinned_configuration_release_id uuid not null references public.configuration_releases (id),
  originating_client_user_id uuid not null references public.client_users (id),
  originating_customer_account_id uuid not null references public.customer_accounts (id),
  initial_flow_type public.flow_type not null,
  established_flow_type public.flow_type,
  classification_revision bigint not null default 0,
  concern_revision bigint not null default 0,
  acceptance_criteria_revision bigint not null default 0,
  cancellation_scope_id uuid not null,
  return_deadline_at timestamptz,
  closure_reason text,
  closure_detail jsonb,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index product_signals_client_state_idx on public.product_signals (client_id, state);
create index product_signals_participant_idx
  on public.product_signals (originating_client_user_id);
create index product_signals_cancellation_scope_idx
  on public.product_signals (cancellation_scope_id);

alter table public.intake_items
  add constraint intake_items_product_signal_fk
  foreign key (product_signal_id) references public.product_signals (id);

-- The originating user's report: the source a Product Signal was created from. Every
-- routed report opens its own Product Signal, so each signal has exactly one.
create table public.source_reports (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null unique references public.product_signals (id),
  intake_item_id uuid not null references public.intake_items (id),
  reporting_client_user_id uuid not null references public.client_users (id),
  reporting_customer_account_id uuid not null references public.customer_accounts (id),
  source_excerpt text not null,
  occurred_at timestamptz not null,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now()
);

alter table public.intake_items enable row level security;
alter table public.product_signals enable row level security;
alter table public.source_reports enable row level security;

revoke all on table public.intake_items from anon, authenticated;
revoke all on table public.product_signals from anon, authenticated;
revoke all on table public.source_reports from anon, authenticated;

grant select on table public.product_signals to authenticated;
grant select on table public.source_reports to authenticated;

create policy product_signals_member_select on public.product_signals
  for select to authenticated
  using (utrace.is_client_member(client_id));

create policy source_reports_member_select on public.source_reports
  for select to authenticated
  using (utrace.is_client_member(client_id));

