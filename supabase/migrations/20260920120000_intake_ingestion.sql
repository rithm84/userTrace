-- Discord ingestion: the immutable per-message source record, the open debounce
-- batch that collapses one author in one channel into one Intake Item, and the
-- client-scoped attribution that lets a bot-authored message stand in for a
-- reporter inside a test database.
--
-- Contract: docs/arch-components/signal-and-outreach.md#discord-ingestion-and-identity

create type public.intake_batch_state as enum ('open', 'sealed', 'discarded');

-- Provider message identity is the idempotency key: a duplicate gateway delivery
-- collides on the unique index instead of appending the message twice.
create table public.intake_messages (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  intake_batch_id uuid not null,
  provider text not null,
  channel_ref text not null,
  provider_message_id text not null,
  author_provider_identity text not null,
  author_is_bot boolean not null,
  ordinal integer not null,
  content text not null,
  reply_to_provider_message_id text,
  thread_ref text,
  occurred_at timestamptz not null,
  edited_at timestamptz,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (client_id, provider, provider_message_id),
  constraint intake_messages_ordinal_positive check (ordinal > 0)
);
create index intake_messages_batch_idx
  on public.intake_messages (intake_batch_id, ordinal);

create trigger intake_messages_immutable
  before update or delete on public.intake_messages
  for each row execute function utrace.reject_mutation();

-- One open batch per author per channel. Sealing closes it and names the Intake
-- Item it produced, so a late message starts a new batch rather than mutating a
-- sealed one.
create table public.intake_batches (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  provider text not null,
  channel_ref text not null,
  author_provider_identity text not null,
  state public.intake_batch_state not null,
  seal_job_id uuid references public.jobs (id),
  intake_item_id uuid references public.intake_items (id),
  message_count integer not null default 0,
  opened_at timestamptz not null default now(),
  seal_not_before timestamptz not null,
  seal_deadline_at timestamptz not null,
  sealed_at timestamptz,
  revision bigint not null default 0,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint intake_batches_seal_window_ordered
    check (seal_deadline_at >= seal_not_before)
);

create unique index intake_batches_one_open
  on public.intake_batches (client_id, provider, channel_ref, author_provider_identity)
  where state = 'open';

alter table public.intake_messages
  add constraint intake_messages_batch_fk
  foreign key (intake_batch_id) references public.intake_batches (id);

-- Attributing a bot-authored provider identity to a reporting client user.
-- Intake refuses every bot-authored message that has no row here, so the uTrace
-- bot's own posts can never become customer reports. Production configuration
-- writes no rows; a test database inserts one to reuse a bot-posted fixture
-- message as a reporter's message.
create table public.intake_author_attributions (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  provider text not null,
  author_provider_identity text not null,
  client_user_id uuid not null references public.client_users (id),
  customer_account_id uuid not null references public.customer_accounts (id),
  reason text not null,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (client_id, provider, author_provider_identity)
);

-- Inbound direct messages route only to an invitation, an active session,
-- re-contact, or an explicit preference action. `client_id` is null when no
-- outstanding outreach resolves the sender to a client.
create table public.inbound_direct_messages (
  id uuid primary key,
  client_id uuid references public.clients (id),
  provider text not null,
  provider_message_id text not null,
  sender_provider_identity text not null,
  content text not null,
  route_kind text not null,
  outreach_attempt_id uuid references public.outreach_attempts (id),
  interview_session_id uuid references public.interview_sessions (id),
  occurred_at timestamptz not null,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (provider, provider_message_id)
);
create index inbound_direct_messages_sender_idx
  on public.inbound_direct_messages (provider, sender_provider_identity, occurred_at);

create trigger inbound_direct_messages_immutable
  before update or delete on public.inbound_direct_messages
  for each row execute function utrace.reject_mutation();

alter table public.intake_messages enable row level security;
alter table public.intake_batches enable row level security;
alter table public.intake_author_attributions enable row level security;
alter table public.inbound_direct_messages enable row level security;

revoke all on table public.intake_messages from anon, authenticated;
revoke all on table public.intake_batches from anon, authenticated;
revoke all on table public.intake_author_attributions from anon, authenticated;
revoke all on table public.inbound_direct_messages from anon, authenticated;
