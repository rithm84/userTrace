-- Observation activations and the short-lived, write-only telemetry credentials
-- minted against them.
--
-- Credential claims resolve against these rows. Identity claims in a payload
-- never override the authenticated activation.
--
-- Contracts: docs/arch-components/telemetry-and-evidence.md#authentication-quotas-and-payload-limits
--            docs/arch-components/browser-sdk.md#activation-and-session-attestation
--            docs/arch-components/server-sdk.md#configuration-identity-and-credentials

create table public.telemetry_activations (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  kind public.telemetry_producer_kind not null,
  state public.telemetry_activation_state not null default 'active',
  revision bigint not null default 0,

  -- Authenticated identity. A payload claim that disagrees with these columns is
  -- an integrity violation, never a correlation opportunity.
  client_user_id uuid not null references public.client_users (id),
  customer_account_id uuid not null references public.customer_accounts (id),
  interview_session_id uuid not null references public.interview_sessions (id),
  preview_environment_id uuid not null,
  preview_runtime_allocation_id uuid not null
    references public.preview_runtime_allocations (id),
  preview_version_id uuid references public.preview_versions (id),

  -- Pinned, immutable-for-one-activation policy and schema versions.
  protocol_version text not null,
  source_schema_version text not null,
  policy_version text not null,
  redaction_version text not null,
  annotation_manifest_version text,
  annotation_manifest_digest text,
  semantic_convention_version text,
  limits jsonb not null,

  -- Browser activation columns.
  document_attachment_id uuid,
  document_nonce text,
  document_origin text,
  sdk_installation_id text,
  attestation_expires_at timestamptz,
  attestation_exchanged_at timestamptz,

  -- Server activation columns.
  server_stream_id uuid,
  service_instance_id uuid,
  service_key text,
  preview_entry_token_digest text,
  probe_id uuid,
  required_hops text[],

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  schema_version smallint not null default 1,

  constraint telemetry_activations_browser_columns check (
    kind <> 'browser'
    or (
      document_attachment_id is not null
      and document_nonce is not null
      and document_origin is not null
      and sdk_installation_id is not null
      and attestation_expires_at is not null
      and annotation_manifest_version is not null
      and annotation_manifest_digest is not null
    )
  ),
  constraint telemetry_activations_server_columns check (
    kind <> 'server'
    or (
      server_stream_id is not null
      and service_instance_id is not null
      and service_key is not null
      and preview_entry_token_digest is not null
      and semantic_convention_version is not null
      and probe_id is not null
    )
  )
);

-- One browser activation per attested document; one server activation per stream.
create unique index telemetry_activations_document
  on public.telemetry_activations (document_attachment_id)
  where kind = 'browser';
create unique index telemetry_activations_server_stream
  on public.telemetry_activations (server_stream_id)
  where kind = 'server';
-- A document nonce is single-use inside one client scope.
create unique index telemetry_activations_document_nonce
  on public.telemetry_activations (client_id, document_nonce)
  where kind = 'browser';
create index telemetry_activations_session_idx
  on public.telemetry_activations (interview_session_id, kind, state);

-- The credential is write-only: it cannot read telemetry, resolve artifacts, call
-- control-plane APIs, or authorize product actions. Only its digest is stored, so
-- a database read never yields a usable credential.
create table public.telemetry_credentials (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  activation_id uuid not null references public.telemetry_activations (id),
  kind public.telemetry_producer_kind not null,
  state public.telemetry_credential_state not null default 'active',
  revision bigint not null default 0,
  token_digest text not null unique,
  issued_at timestamptz not null default now(),
  expires_at timestamptz not null,
  revoked_at timestamptz,
  revocation_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  schema_version smallint not null default 1
);
create index telemetry_credentials_activation_idx
  on public.telemetry_credentials (activation_id, state);

-- Revocation is durable rather than a token property, so withdrawing authority
-- stops new writes immediately instead of waiting for expiry.
create table public.telemetry_credential_revocations (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  telemetry_credential_id uuid not null references public.telemetry_credentials (id),
  reason text not null,
  revoked_at timestamptz not null default now(),
  schema_version smallint not null default 1
);
create index telemetry_credential_revocations_credential_idx
  on public.telemetry_credential_revocations (telemetry_credential_id);

create trigger telemetry_credential_revocations_immutable
  before update or delete on public.telemetry_credential_revocations
  for each row execute function utrace.reject_mutation();

alter table public.telemetry_activations enable row level security;
alter table public.telemetry_credentials enable row level security;
alter table public.telemetry_credential_revocations enable row level security;

revoke all on table public.telemetry_activations from anon, authenticated;
revoke all on table public.telemetry_credentials from anon, authenticated;
revoke all on table public.telemetry_credential_revocations from anon, authenticated;
