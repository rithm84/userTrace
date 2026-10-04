-- Enumerations for telemetry ingestion, correlation, and evidence.
--
-- Contract: docs/arch-components/telemetry-and-evidence.md

-- Browser observations and server spans enter through separate public edges.
create type public.telemetry_producer_kind as enum ('browser', 'server');

-- Accepted journal records move through explicit stages. A terminal rejection or
-- quarantine records the exact stage it reached; no later stage is implied by an
-- earlier one.
create type public.ingestion_stage as enum (
  'received',
  'authenticated',
  'source_validated',
  'normalized',
  'indexed',
  'correlated',
  'available'
);

create type public.ingestion_outcome as enum ('accepted', 'rejected', 'quarantined');

create type public.telemetry_activation_state as enum (
  'active',
  'paused',
  'superseded',
  'revoked',
  'expired'
);

create type public.telemetry_credential_state as enum ('active', 'revoked', 'expired');

create type public.telemetry_stream_state as enum ('open', 'paused', 'ended', 'failed');

-- Correlation status is explicit; a projection never claims completeness it has
-- not observed.
create type public.correlation_status as enum (
  'pending',
  'complete',
  'partial',
  'ambiguous',
  'invalid',
  'expired'
);

-- A rolling live window may advance within the active session; a decision or
-- output freezes a new immutable window.
create type public.evidence_window_mode as enum ('rolling', 'frozen');

-- Ingestion owns source validation, stream continuity, trace completeness, and
-- correlation readiness evidence. The control plane combines them with the rest.
create type public.telemetry_readiness_kind as enum ('browser', 'server', 'correlation');

-- Source class stays explicit on every promoted item: an evidence artifact never
-- turns telemetry into user intent or a user statement into telemetry.
create type public.telemetry_source_class as enum ('browser_observation', 'server_span');
