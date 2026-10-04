-- The interview runtime's durable records: the participant compatibility
-- preflight, the two revisioned readiness boundaries, the session projection
-- Realtime distributes, transcript fragments and the playback evidence the
-- transport exposes, and GPT-Live client delegations.
--
-- Contracts: docs/arch-components/interview-and-voice.md#readiness-has-two-boundaries
--            docs/arch-components/interview-and-voice.md#transcript-and-evidence-contract
--            docs/arch-components/browser-extension.md#installation-identity-permissions-and-packaging
--            docs/arch-components/telemetry-and-evidence.md#query-and-subscription-contracts

-- What the claimed invitation verified before the Interview Session may begin.
create table public.participant_preflight_records (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  interview_session_id uuid not null references public.interview_sessions (id),
  client_user_id uuid not null references public.client_users (id),
  browser_name text not null,
  browser_major_version integer not null,
  browser_supported boolean not null,
  extension_installed boolean not null,
  extension_installation_id uuid references public.extension_installations (id),
  account_linked boolean not null,
  host_permission_granted boolean not null,
  microphone_permission_granted boolean not null,
  control_plane_reachable boolean not null,
  reported_protocol_version text not null,
  protocol_supported boolean not null,
  passed boolean not null,
  failed_checks text[] not null,
  recorded_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  constraint participant_preflight_failed_checks_match
    check (passed = (cardinality(failed_checks) = 0))
);
create index participant_preflight_records_session_idx
  on public.participant_preflight_records (interview_session_id, recorded_at desc);

create trigger participant_preflight_records_immutable
  before update or delete on public.participant_preflight_records
  for each row execute function utrace.reject_mutation();

-- The two readiness boundaries with one monotonic revision. Connected sockets
-- alone establish neither; each component fact is recorded separately so a
-- withdrawal names what is missing.
create table public.session_readiness (
  interview_session_id uuid primary key references public.interview_sessions (id),
  client_id uuid not null references public.clients (id),
  readiness_revision bigint not null default 0,
  participant_authenticated boolean not null default false,
  voice_transport_ready boolean not null default false,
  sideband_attached boolean not null default false,
  backend_context_current boolean not null default false,
  preview_allocation_ready boolean not null default false,
  extension_readiness public.extension_readiness_state not null default 'withdrawn',
  browser_sdk_ready boolean not null default false,
  shared_runtime_ready boolean not null default false,
  server_sdk_ready boolean not null default false,
  trace_correlation_ready boolean not null default false,
  product_probe_path_complete boolean not null default false,
  conversational_ready boolean not null default false,
  observation_ready boolean not null default false,
  blockers text[] not null default '{}',
  updated_at timestamptz not null default now(),
  schema_version smallint not null default 1
);

-- The denormalised projection the session tab reads over Supabase Realtime and
-- the extension's character frame is derived from. Durable Postgres stays
-- authoritative; a Realtime notification is a hint, never proof.
create table public.session_projections (
  session_id uuid primary key references public.interview_sessions (id),
  client_id uuid not null references public.clients (id),
  client_user_id uuid not null references public.client_users (id),
  product_signal_id uuid not null references public.product_signals (id),
  revision bigint not null,
  projection jsonb not null,
  updated_at timestamptz not null default now(),
  schema_version smallint not null default 1
);
create index session_projections_participant_idx
  on public.session_projections (client_user_id);

-- Timestamped input and output fragments with speaker, provider event, the
-- connection that produced them, and their provenance. A fragment is not a
-- completed turn and establishes no consent, scope, or validation outcome.
create table public.voice_transcript_fragments (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  interview_session_id uuid not null references public.interview_sessions (id),
  voice_connection_id uuid not null references public.voice_connections (id),
  voice_run_id uuid not null,
  provider_session_ref text not null,
  speaker public.transcript_speaker not null,
  provider_event_type text not null,
  start_ms integer not null,
  end_ms integer not null,
  fragment_text text not null,
  provenance jsonb not null,
  recorded_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  unique (voice_connection_id, provider_event_type, start_ms, end_ms),
  constraint voice_transcript_fragments_span_ordered check (end_ms >= start_ms)
);
create index voice_transcript_fragments_session_idx
  on public.voice_transcript_fragments (interview_session_id, start_ms);

create trigger voice_transcript_fragments_immutable
  before update or delete on public.voice_transcript_fragments
  for each row execute function utrace.reject_mutation();

-- Generated speech, transmitted audio, and heard speech are different facts.
-- This records the first where the transport exposes it; delivery stays unknown
-- under WebRTC, where audio rides the media track.
create table public.voice_playback_segments (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  interview_session_id uuid not null references public.interview_sessions (id),
  voice_connection_id uuid not null references public.voice_connections (id),
  start_ms integer not null,
  end_ms integer not null,
  audio_byte_length integer not null,
  delivery public.playback_delivery not null,
  recorded_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  unique (voice_connection_id, start_ms, end_ms),
  constraint voice_playback_segments_span_ordered check (end_ms >= start_ms)
);

create trigger voice_playback_segments_immutable
  before update or delete on public.voice_playback_segments
  for each row execute function utrace.reject_mutation();

-- One GPT-Live client delegation. The provider id is an opaque correlation
-- handle: the backend works out the request from its own transcript and state.
create table public.voice_delegations (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  interview_session_id uuid not null references public.interview_sessions (id),
  voice_connection_id uuid not null references public.voice_connections (id),
  provider_delegation_id text not null,
  delegation_target text not null,
  state public.voice_delegation_state not null,
  context_bundle_id uuid references public.context_bundles (id),
  finding text,
  refusal_reason text,
  offset_ms integer,
  created_at timestamptz not null default now(),
  settled_at timestamptz,
  schema_version smallint not null default 1,
  unique (voice_connection_id, provider_delegation_id)
);
create index voice_delegations_session_idx
  on public.voice_delegations (interview_session_id, created_at desc);

alter table public.participant_preflight_records enable row level security;
alter table public.session_readiness enable row level security;
alter table public.session_projections enable row level security;
alter table public.voice_transcript_fragments enable row level security;
alter table public.voice_playback_segments enable row level security;
alter table public.voice_delegations enable row level security;

revoke all on table public.participant_preflight_records from anon, authenticated;
revoke all on table public.session_readiness from anon, authenticated;
revoke all on table public.session_projections from anon, authenticated;
revoke all on table public.voice_transcript_fragments from anon, authenticated;
revoke all on table public.voice_playback_segments from anon, authenticated;
revoke all on table public.voice_delegations from anon, authenticated;

-- The session tab subscribes as the participant; client staff read their own
-- tenant's sessions. Transcripts and delegations stay backend-only.
grant select on table public.session_projections to authenticated;

create policy session_projections_authorized_select on public.session_projections
  for select to authenticated
  using (
    utrace.is_client_member(client_id)
    or utrace.is_participant(client_user_id)
  );

alter publication supabase_realtime add table public.session_projections;
