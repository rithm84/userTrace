-- The versioned concern a Product Signal investigates, the uniqueness that keeps
-- one initial invitation per case, and the durable record of one private outreach
-- delivery.
--
-- Contracts: docs/arch-components/signal-and-outreach.md#routing-contract
--            docs/arch-components/signal-and-outreach.md#originating-user-and-invitation-lifecycle

create type public.concern_origin as enum ('routing_inference', 'interview_established');

-- Revision 0 is routing's source-linked inference. The interview appends a new
-- revision when it establishes the pain point and validation criterion.
create table public.product_signal_concerns (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  concern_revision bigint not null,
  origin public.concern_origin not null,
  pain_point text not null,
  validation_criterion text not null,
  user_wording jsonb not null,
  unknowns jsonb not null,
  established_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (product_signal_id, concern_revision)
);
create index product_signal_concerns_signal_idx
  on public.product_signal_concerns (product_signal_id, concern_revision desc);

create trigger product_signal_concerns_immutable
  before update or delete on public.product_signal_concerns
  for each row execute function utrace.reject_mutation();

-- One initial invitation per Product Signal. A return reminder uses its own
-- purpose, so this index makes a replayed initial dispatch collide instead of
-- inviting twice.
create unique index outreach_attempts_one_initial_invitation
  on public.outreach_attempts (product_signal_id)
  where purpose = 'initial_invitation';

-- A replacement access link supersedes the previous one for the same case.
alter table public.invitations
  add column superseded_by_id uuid references public.invitations (id);

create unique index invitations_one_live_per_signal
  on public.invitations (product_signal_id)
  where revoked_at is null and superseded_by_id is null;

-- Provider sends are durable delivery results, never Product Signal state. One
-- row per attempted private delivery, including the failures.
create table public.outreach_deliveries (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  outreach_attempt_id uuid not null references public.outreach_attempts (id),
  command_id uuid not null references public.commands (id),
  provider text not null,
  recipient_provider_identity text not null,
  provider_message_ref text,
  effect_state public.effect_state not null,
  disposition public.command_disposition not null,
  failure_category text,
  detail jsonb not null,
  delivered_at timestamptz,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (command_id)
);
create index outreach_deliveries_attempt_idx
  on public.outreach_deliveries (outreach_attempt_id, created_at desc);

create trigger outreach_deliveries_immutable
  before update or delete on public.outreach_deliveries
  for each row execute function utrace.reject_mutation();

alter table public.product_signal_concerns enable row level security;
alter table public.outreach_deliveries enable row level security;

revoke all on table public.product_signal_concerns from anon, authenticated;
revoke all on table public.outreach_deliveries from anon, authenticated;

grant select on table public.product_signal_concerns to authenticated;

create policy product_signal_concerns_member_select on public.product_signal_concerns
  for select to authenticated
  using (utrace.is_client_member(client_id));
