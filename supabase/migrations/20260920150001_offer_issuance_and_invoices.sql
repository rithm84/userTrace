-- Outreach Offers issuance identity, verification, and reconciled invoice
-- application.
--
-- Contract: docs/arch-components/outreach-offers.md#issuance-and-reconciliation

-- The idempotency key and the billing identity an issuance was verified against
-- belong to the operation, not to the provider response: a reconciliation after
-- Stripe's key retention window matches on the operation id in the adjustment's
-- metadata, so that identity has to be durable before the call.
alter table public.offer_issuance_operations
  add column idempotency_key text not null,
  add column billing_account_ref text not null,
  add column billing_customer_ref text not null,
  add column verified_at timestamptz,
  add column ending_balance_minor_units bigint,
  add column dispatch_count integer not null default 0;

alter table public.offer_issuance_operations
  add constraint offer_issuance_operations_issued_is_verified
  check (
    state <> 'issued'
    or (provider_transaction_ref is not null and verified_at is not null)
  );

alter table public.offer_issuance_operations
  add constraint offer_issuance_operations_blocked_carries_reason
  check (state <> 'blocked' or blocked_reason is not null);

create unique index offer_issuance_operations_idempotency_key_idx
  on public.offer_issuance_operations (client_id, idempotency_key);

-- A verified adjustment means the credit is issued, not consumed. Application is
-- recorded only from an invoice's own starting and ending balance, never
-- inferred from the customer's aggregate balance.
create table public.offer_invoice_applications (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  offer_issuance_operation_id uuid not null
    references public.offer_issuance_operations (id),
  billing_customer_ref text not null,
  invoice_ref text not null,
  currency text not null,
  invoice_total_minor_units bigint not null,
  applied_minor_units bigint not null,
  amount_due_minor_units bigint not null,
  invoice_status text not null,
  reconciled_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (offer_issuance_operation_id, invoice_ref),
  constraint offer_invoice_applications_applied_nonnegative
    check (applied_minor_units >= 0)
);
create index offer_invoice_applications_client_idx
  on public.offer_invoice_applications (client_id, billing_customer_ref);

create trigger offer_invoice_applications_immutable
  before update or delete on public.offer_invoice_applications
  for each row execute function utrace.reject_mutation();

alter table public.offer_invoice_applications enable row level security;
revoke all on table public.offer_invoice_applications from anon, authenticated;
grant select on table public.offer_invoice_applications to authenticated;

create policy offer_invoice_applications_member_select
  on public.offer_invoice_applications
  for select to authenticated
  using (utrace.is_client_member(client_id));
