-- Outreach Offers: authorized allowance, fixed offers and reservations,
-- participation entitlements, issuance operations, and the append-only ledger.
-- Amounts are integer minor units.
--
-- Contract: docs/arch-components/outreach-offers.md#issuance-and-reconciliation

-- Each authorization is its own append-only row; the current ceiling is the row
-- with the highest sequence_number for the client and currency.
create table public.offer_budgets (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  currency text not null,
  sequence_number bigint not null,
  authorized_minor_units bigint not null,
  authorized_by_auth_user_id uuid not null references auth.users (id),
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (client_id, currency, sequence_number),
  constraint offer_budgets_currency_is_iso check (currency = upper(currency)),
  constraint offer_budgets_authorized_nonnegative check (authorized_minor_units >= 0)
);

create trigger offer_budgets_immutable
  before update or delete on public.offer_budgets
  for each row execute function utrace.reject_mutation();

-- One offer covers one case. Re-contact and revisions reuse it.
create table public.outreach_offers (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null unique references public.product_signals (id),
  customer_account_id uuid not null references public.customer_accounts (id),
  state public.offer_state not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  currency text not null,
  amount_minor_units bigint not null,
  billing_account_ref text not null,
  billing_customer_ref text not null,
  billing_mapping_revision bigint not null,
  completion_terms jsonb not null,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint outreach_offers_amount_positive check (amount_minor_units > 0)
);

alter table public.outreach_attempts
  add constraint outreach_attempts_offer_fk
  foreign key (outreach_offer_id) references public.outreach_offers (id);

create table public.offer_reservations (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  outreach_offer_id uuid not null unique references public.outreach_offers (id),
  currency text not null,
  amount_minor_units bigint not null,
  released_at timestamptz,
  converted_at timestamptz,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint offer_reservations_single_disposition
    check (released_at is null or converted_at is null)
);
create index offer_reservations_open_idx
  on public.offer_reservations (client_id, currency)
  where released_at is null;

-- Exactly one entitlement per offer.
create table public.offer_entitlements (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  outreach_offer_id uuid not null unique references public.outreach_offers (id),
  product_signal_id uuid not null references public.product_signals (id),
  customer_account_id uuid not null references public.customer_accounts (id),
  currency text not null,
  amount_minor_units bigint not null,
  participation_evidence jsonb not null,
  stop_reason text not null,
  earned_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  created_at timestamptz not null default now()
);

create trigger offer_entitlements_immutable
  before update or delete on public.offer_entitlements
  for each row execute function utrace.reject_mutation();

create table public.offer_issuance_operations (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  offer_entitlement_id uuid not null references public.offer_entitlements (id),
  command_id uuid not null,
  state public.offer_issuance_state not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  currency text not null,
  amount_minor_units bigint not null,
  provider_transaction_ref text,
  blocked_reason text,
  reconciled_at timestamptz,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (offer_entitlement_id)
);

-- Append-only movement record. Signed minor units: reservations and issuance
-- consume allowance, releases return it.
create table public.offer_ledger_entries (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  currency text not null,
  entry_kind public.offer_ledger_entry_kind not null,
  delta_minor_units bigint not null,
  outreach_offer_id uuid references public.outreach_offers (id),
  offer_entitlement_id uuid references public.offer_entitlements (id),
  offer_issuance_operation_id uuid references public.offer_issuance_operations (id),
  causal_evidence jsonb not null,
  recorded_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  created_at timestamptz not null default now()
);
create index offer_ledger_entries_client_currency_idx
  on public.offer_ledger_entries (client_id, currency, recorded_at);

create trigger offer_ledger_entries_immutable
  before update or delete on public.offer_ledger_entries
  for each row execute function utrace.reject_mutation();

alter table public.offer_budgets enable row level security;
alter table public.outreach_offers enable row level security;
alter table public.offer_reservations enable row level security;
alter table public.offer_entitlements enable row level security;
alter table public.offer_issuance_operations enable row level security;
alter table public.offer_ledger_entries enable row level security;

revoke all on table public.offer_budgets from anon, authenticated;
revoke all on table public.outreach_offers from anon, authenticated;
revoke all on table public.offer_reservations from anon, authenticated;
revoke all on table public.offer_entitlements from anon, authenticated;
revoke all on table public.offer_issuance_operations from anon, authenticated;
revoke all on table public.offer_ledger_entries from anon, authenticated;

grant select on table public.offer_budgets to authenticated;
grant select on table public.outreach_offers to authenticated;
grant select on table public.offer_reservations to authenticated;
grant select on table public.offer_entitlements to authenticated;
grant select on table public.offer_issuance_operations to authenticated;

create policy offer_budgets_member_select on public.offer_budgets
  for select to authenticated
  using (utrace.is_client_member(client_id));

create policy outreach_offers_member_select on public.outreach_offers
  for select to authenticated
  using (utrace.is_client_member(client_id));

create policy offer_reservations_member_select on public.offer_reservations
  for select to authenticated
  using (utrace.is_client_member(client_id));

create policy offer_entitlements_member_select on public.offer_entitlements
  for select to authenticated
  using (utrace.is_client_member(client_id));

create policy offer_issuance_operations_member_select on public.offer_issuance_operations
  for select to authenticated
  using (utrace.is_client_member(client_id));
