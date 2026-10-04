-- The Server SDK control channel: persisted health reports and the pause,
-- resume, and terminate commands the control plane sends over it.
--
-- The control WebSocket carries no product telemetry. What survives a socket is
-- here: the operational health a service reported, and the commands the control
-- plane issued with the acknowledgement each one received.
--
-- Contracts: docs/arch-components/server-sdk.md#readiness-health-and-failure
--            docs/arch-components/telemetry-and-evidence.md#completeness-and-readiness

create type public.server_control_command_kind as enum ('pause', 'resume', 'terminate');

-- Health reports contain operational metadata only. A report never carries
-- product content or a rejected raw value, so the whole row is safe to keep.
create table public.server_health_reports (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  activation_id uuid not null references public.telemetry_activations (id),
  server_stream_id uuid not null references public.server_streams (id),
  reported_at timestamptz not null,
  received_at timestamptz not null default now(),
  lifecycle_state text not null,
  sdk_version text not null,
  contract_major integer not null,
  node_version text not null,
  service_key text not null,
  service_instance_id uuid not null,
  manifest_version integer,
  policy_version text,
  utrace_schema_version text,
  semantic_convention_version text,
  credential_expires_at timestamptz,
  capabilities jsonb not null,
  adapters jsonb not null,
  buffer jsonb not null,
  probe_result jsonb not null,
  failures jsonb not null,
  schema_version smallint not null default 1
);
create index server_health_reports_stream_idx
  on public.server_health_reports (server_stream_id, reported_at desc);
create index server_health_reports_activation_idx
  on public.server_health_reports (activation_id, reported_at desc);

create trigger server_health_reports_immutable
  before update or delete on public.server_health_reports
  for each row execute function utrace.reject_mutation();

-- A control command is delivered over the connection that owns the stream and
-- acknowledged with the lifecycle state the SDK reached. The acknowledgement is
-- proof the SDK applied it; the command row alone is not.
create table public.server_control_commands (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  activation_id uuid not null references public.telemetry_activations (id),
  server_stream_id uuid not null references public.server_streams (id),
  kind public.server_control_command_kind not null,
  reason text not null,
  issued_at timestamptz not null default now(),
  delivered_at timestamptz,
  acknowledged_at timestamptz,
  acknowledged_lifecycle_state text,
  schema_version smallint not null default 1
);
create index server_control_commands_stream_idx
  on public.server_control_commands (server_stream_id, issued_at desc);

alter table public.server_health_reports enable row level security;
alter table public.server_control_commands enable row level security;

revoke all on table public.server_health_reports from anon, authenticated;
revoke all on table public.server_control_commands from anon, authenticated;
