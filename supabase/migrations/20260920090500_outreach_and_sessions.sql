-- Outreach Attempts, invitations, Interview Sessions, and voice connections.
--
-- Contracts: docs/arch-components/signal-and-outreach.md#originating-user-and-invitation-lifecycle
--            docs/arch-components/interview-and-voice.md#runtime-identities-and-sessions

create table public.outreach_attempts (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  client_user_id uuid not null references public.client_users (id),
  purpose public.outreach_purpose not null,
  state public.outreach_attempt_state not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  outreach_offer_id uuid,
  delivery_provider_ref text,
  scheduled_at timestamptz not null default now(),
  dispatched_at timestamptz,
  responded_at timestamptz,
  expires_at timestamptz not null,
  reason text,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index outreach_attempts_signal_idx
  on public.outreach_attempts (product_signal_id, state);

-- Invitation tokens are opaque and expiring. Only the digest is stored, and the
-- row is bound server-side to the invited Discord identity.
create table public.invitations (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  outreach_attempt_id uuid not null references public.outreach_attempts (id),
  product_signal_id uuid not null references public.product_signals (id),
  client_user_id uuid not null references public.client_users (id),
  token_digest text not null unique,
  bound_provider text not null,
  bound_provider_identity text not null,
  expires_at timestamptz not null,
  claimed_at timestamptz,
  revoked_at timestamptz,
  interview_session_id uuid,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index invitations_signal_idx on public.invitations (product_signal_id);

create table public.interview_sessions (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  product_signal_id uuid not null references public.product_signals (id),
  client_user_id uuid not null references public.client_users (id),
  state public.interview_session_state not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  phase public.interview_phase not null,
  conversational_ready boolean not null default false,
  observation_ready boolean not null default false,
  character_state public.agent_character_state not null,
  character_revision bigint not null default 0,
  implementation_progress public.implementation_progress not null,
  muted boolean not null default false,
  paused boolean not null default false,
  preview_runtime_allocation_id uuid,
  closure_reason text,
  closed_at timestamptz,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index interview_sessions_signal_idx
  on public.interview_sessions (product_signal_id, state);
create index interview_sessions_participant_idx
  on public.interview_sessions (client_user_id);

-- At most one active encounter per Product Signal.
create unique index interview_sessions_one_open
  on public.interview_sessions (product_signal_id)
  where state <> 'closed';

alter table public.invitations
  add constraint invitations_interview_session_fk
  foreign key (interview_session_id) references public.interview_sessions (id);

create table public.voice_connections (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  interview_session_id uuid not null references public.interview_sessions (id),
  state public.voice_connection_state not null,
  revision bigint not null default 0,
  machine_version smallint not null default 1,
  voice_run_id uuid not null unique,
  participant_connection_id uuid not null,
  provider_session_ref text,
  superseded_by_id uuid references public.voice_connections (id),
  closed_at timestamptz,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index voice_connections_session_idx
  on public.voice_connections (interview_session_id, state);

-- Only one connection holds current participant authority.
create unique index voice_connections_one_current
  on public.voice_connections (interview_session_id)
  where state <> 'closed';

alter table public.outreach_attempts enable row level security;
alter table public.invitations enable row level security;
alter table public.interview_sessions enable row level security;
alter table public.voice_connections enable row level security;

revoke all on table public.outreach_attempts from anon, authenticated;
revoke all on table public.invitations from anon, authenticated;
revoke all on table public.interview_sessions from anon, authenticated;
revoke all on table public.voice_connections from anon, authenticated;

grant select on table public.outreach_attempts to authenticated;
grant select on table public.interview_sessions to authenticated;

create policy outreach_attempts_member_select on public.outreach_attempts
  for select to authenticated
  using (utrace.is_client_member(client_id));

-- Staff read the session projection for their client; the participant reads only
-- the sessions bound to their own client-scoped identity.
create policy interview_sessions_member_select on public.interview_sessions
  for select to authenticated
  using (utrace.is_client_member(client_id));

create policy interview_sessions_participant_select on public.interview_sessions
  for select to authenticated
  using (utrace.is_participant(client_user_id));
