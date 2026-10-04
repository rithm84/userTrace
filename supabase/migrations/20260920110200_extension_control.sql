-- Browser extension control: installation identity and its device key, the
-- one-time account-link code, single-use proof-of-possession records, the
-- authoritative document binding, the resumable control stream, the
-- control-plane-owned action preferences, and visual-command dispatch records.
--
-- Contracts: docs/arch-components/browser-extension.md#installation-identity-permissions-and-packaging
--            docs/arch-components/browser-extension.md#session-anchor-binding-and-handshake
--            docs/arch-components/browser-extension.md#visual-commands-and-cursor-choreography
--            docs/arch-components/policy-and-permissions.md#browser-command-permission-scopes

-- One installation per browser profile, linked to the global uTrace User. The
-- extension keeps the private key; only the public half is stored here.
create table public.extension_installations (
  id uuid primary key,
  utrace_user_id uuid not null references public.users (id),
  device_public_key_spki_b64u text not null unique,
  account_link_state public.extension_account_link_state not null,
  extension_version text not null,
  supported_protocol_versions text[] not null,
  negotiated_protocol_version text not null,
  revision bigint not null default 0,
  registered_at timestamptz not null default now(),
  revoked_at timestamptz,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint extension_installations_revoked_state
    check ((account_link_state = 'revoked') = (revoked_at is not null))
);
create index extension_installations_user_idx
  on public.extension_installations (utrace_user_id)
  where account_link_state = 'linked';

-- The account-link redirect mints one short-lived, single-use code. Only its
-- digest is stored, exactly as an invitation token is.
create table public.extension_link_codes (
  id uuid primary key,
  code_digest text not null unique,
  utrace_user_id uuid not null references public.users (id),
  redirect_uri text not null,
  extension_version text not null,
  protocol_version text not null,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  consumed_by_installation_id uuid references public.extension_installations (id),
  created_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  constraint extension_link_codes_consumed_together
    check ((consumed_at is null) = (consumed_by_installation_id is null))
);
create index extension_link_codes_user_idx
  on public.extension_link_codes (utrace_user_id, expires_at desc);

-- Every accepted proof of possession records its `jti` so the same challenge can
-- never authenticate twice.
create table public.extension_possession_proofs (
  jti uuid primary key,
  extension_installation_id uuid references public.extension_installations (id),
  device_public_key_spki_b64u text not null,
  purpose text not null,
  audience text not null,
  issued_at timestamptz not null,
  accepted_at timestamptz not null default now(),
  schema_version smallint not null default 1
);
create index extension_possession_proofs_issued_idx
  on public.extension_possession_proofs (issued_at);

create trigger extension_possession_proofs_immutable
  before update or delete on public.extension_possession_proofs
  for each row execute function utrace.reject_mutation();

-- One authoritative binding of one Interview Session to one preview document,
-- joined from the session tab's offer, the installation's proof of possession,
-- and the embedded SDK's page proof.
create table public.extension_bindings (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  interview_session_id uuid not null references public.interview_sessions (id),
  extension_installation_id uuid not null references public.extension_installations (id),
  preview_runtime_allocation_id uuid not null
    references public.preview_runtime_allocations (id),
  document_attachment_id uuid not null,
  status public.extension_binding_status not null,
  binding_revision bigint not null default 0,
  document_origin text not null,
  tab_id integer not null,
  document_nonce text not null,
  sdk_runtime_id text not null,
  sdk_protocol_version text not null,
  page_proof_digest text not null,
  offer_token_id uuid,
  reason text,
  bound_at timestamptz not null default now(),
  revoked_at timestamptz,
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint extension_bindings_reason_when_not_bound
    check ((status = 'bound') = (reason is null))
);
create index extension_bindings_session_idx
  on public.extension_bindings (interview_session_id, status);

-- One session has one authoritative preview-tab binding.
create unique index extension_bindings_one_current
  on public.extension_bindings (interview_session_id)
  where status = 'bound';

-- A consumed offer cannot bind a second document.
create unique index extension_bindings_offer_consumed_once
  on public.extension_bindings (offer_token_id)
  where offer_token_id is not null;

-- The resumable outbound control stream. `last_sequence` is the highest sequence
-- allocated; `acknowledged_sequence` is the highest the extension confirmed.
create table public.extension_control_streams (
  extension_installation_id uuid primary key
    references public.extension_installations (id),
  last_sequence bigint not null default 0,
  acknowledged_sequence bigint not null default 0,
  connected_at timestamptz,
  disconnected_at timestamptz,
  updated_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  constraint extension_control_streams_ordered
    check (acknowledged_sequence <= last_sequence)
);

-- Every sequenced frame the control plane sent. A resume truncates the stream by
-- superseding the frames above the resume point rather than replaying them:
-- recovery re-projects current state instead of assuming missed delivery.
create table public.extension_control_frames (
  id uuid primary key,
  client_id uuid references public.clients (id),
  extension_installation_id uuid not null
    references public.extension_installations (id),
  interview_session_id uuid references public.interview_sessions (id),
  sequence bigint not null,
  kind text not null,
  payload jsonb not null,
  sent_at timestamptz not null default now(),
  acknowledged_at timestamptz,
  superseded_at timestamptz,
  schema_version smallint not null default 1
);
create unique index extension_control_frames_live_sequence
  on public.extension_control_frames (extension_installation_id, sequence)
  where superseded_at is null;
create index extension_control_frames_stream_idx
  on public.extension_control_frames (extension_installation_id, sequence desc);

-- Global action preferences are control-plane owned; the extension caches them.
create table public.extension_preferences (
  utrace_user_id uuid primary key references public.users (id),
  preferences_revision bigint not null default 0,
  visual_guidance_enabled boolean not null default true,
  agent_cursor_enabled boolean not null default true,
  updated_at timestamptz not null default now(),
  schema_version smallint not null default 1
);

-- The extension-specific facts of one dispatched visual Command. The Command and
-- its single terminal Result stay in public.commands and public.results.
create table public.visual_command_dispatches (
  command_id uuid primary key references public.commands (id),
  client_id uuid not null references public.clients (id),
  interview_session_id uuid not null references public.interview_sessions (id),
  extension_binding_id uuid not null references public.extension_bindings (id),
  document_attachment_id uuid not null,
  command_kind public.visual_command_kind not null,
  targets jsonb not null,
  expected_lifecycle_revision bigint not null,
  deadline_at timestamptz not null,
  policy_decision jsonb not null,
  outcome public.visual_command_outcome,
  outcome_reason text,
  observed_targets jsonb,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  constraint visual_command_dispatches_reason_when_not_applied
    check (outcome is null or (outcome = 'applied') = (outcome_reason is null))
);
create index visual_command_dispatches_session_idx
  on public.visual_command_dispatches (interview_session_id, created_at desc);
create index visual_command_dispatches_pending_idx
  on public.visual_command_dispatches (interview_session_id)
  where outcome is null;

alter table public.extension_installations enable row level security;
alter table public.extension_link_codes enable row level security;
alter table public.extension_possession_proofs enable row level security;
alter table public.extension_bindings enable row level security;
alter table public.extension_control_streams enable row level security;
alter table public.extension_control_frames enable row level security;
alter table public.extension_preferences enable row level security;
alter table public.visual_command_dispatches enable row level security;

revoke all on table public.extension_installations from anon, authenticated;
revoke all on table public.extension_link_codes from anon, authenticated;
revoke all on table public.extension_possession_proofs from anon, authenticated;
revoke all on table public.extension_bindings from anon, authenticated;
revoke all on table public.extension_control_streams from anon, authenticated;
revoke all on table public.extension_control_frames from anon, authenticated;
revoke all on table public.extension_preferences from anon, authenticated;
revoke all on table public.visual_command_dispatches from anon, authenticated;

-- The popup reads its own installation status through the API, but the
-- authenticated person may also read their own rows directly.
grant select on table public.extension_installations to authenticated;
grant select on table public.extension_preferences to authenticated;

create policy extension_installations_self_select on public.extension_installations
  for select to authenticated
  using (
    exists (
      select 1 from public.users u
      where u.id = utrace_user_id and u.auth_user_id = (select auth.uid())
    )
  );

create policy extension_preferences_self_select on public.extension_preferences
  for select to authenticated
  using (
    exists (
      select 1 from public.users u
      where u.id = utrace_user_id and u.auth_user_id = (select auth.uid())
    )
  );
