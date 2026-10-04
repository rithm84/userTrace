-- Completes the Validation Attempt binding tuple, the Case File metadata and
-- sealed bytes, the GitHub delivery identities, and Case File amendments.
--
-- Contracts: docs/arch-components/validation.md#validation-binding
--            docs/arch-components/client-outputs.md#case-file-identity-and-assembly
--            docs/arch-components/client-outputs.md#github-delivery

-- The Validation Attempt cites one exact tuple. The candidate allocation,
-- dataset digest, criteria revision, and session were already columns; the
-- authenticated product identity, the agreed workflow, the frozen evidence
-- window, and the frozen result complete it.
alter table public.validation_attempts
  add column authenticated_identity_ref text not null,
  add column workflow_ref text not null,
  add column evidence_window_id uuid references public.evidence_windows (id),
  add column interrupted_at timestamptz,
  add column frozen_result jsonb,
  add column frozen_result_digest text;

-- A completed attempt always carries its frozen result; an open or abandoned one
-- never does. The result is the record delivery may begin from.
alter table public.validation_attempts
  add constraint validation_attempts_frozen_result_when_completed
  check ((state = 'completed') = (frozen_result is not null));

alter table public.validation_attempts
  add constraint validation_attempts_frozen_digest_with_result
  check ((frozen_result is null) = (frozen_result_digest is null));

-- One criterion-level proof record: what the originating user confirmed, what the
-- product did, what contradicted it, and which earlier claims a correction
-- superseded. Rows stay immutable after insert.
alter table public.validation_outcomes
  add column observability public.criterion_observability not null,
  add column confirmation_evidence_record_id uuid references public.evidence_records (id),
  add column contradicting_evidence_record_ids uuid[] not null,
  add column superseded_claim_record_ids uuid[] not null,
  add column reason text not null;

-- Case File metadata: the exact records the published core cites, the opaque
-- client-scoped references the dashboard resolves, the reserved authenticated
-- URL a PR body links, and the exact sealed bytes a publication retry reuses.
alter table public.case_files
  add column title text,
  add column context_revision bigint,
  add column narrative_source text,
  add column authenticated_url text,
  add column blocker_reason text,
  add column core_canonical text,
  add column validation_attempt_id uuid references public.validation_attempts (id),
  add column implementation_attempt_id uuid references public.implementation_attempts (id),
  add column preview_version_id uuid references public.preview_versions (id),
  add column gate_decision_id uuid references public.gate_decisions (id),
  add column evidence_window_id uuid references public.evidence_windows (id),
  add column originating_user_ref text,
  add column customer_account_ref text;

alter table public.case_files
  add constraint case_files_narrative_source_values
  check (narrative_source is null or narrative_source in ('sol', 'evidence_derived'));

-- Sealing stores the canonical bytes the digest was taken over.
alter table public.case_files
  add constraint case_files_sealed_bytes
  check ((core_digest is null) = (core_canonical is null));

-- A correction after publication is a separately identified amendment, never a
-- rewrite of the sealed core.
create table public.case_file_amendments (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  case_file_id uuid not null references public.case_files (id),
  ordinal integer not null,
  reason text not null,
  body jsonb not null,
  actor_kind public.actor_kind not null,
  actor_ref text,
  recorded_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  created_at timestamptz not null default now(),
  unique (case_file_id, ordinal),
  constraint case_file_amendments_ordinal_positive check (ordinal >= 1)
);
create index case_file_amendments_case_file_idx
  on public.case_file_amendments (case_file_id, ordinal);

create trigger case_file_amendments_immutable
  before update or delete on public.case_file_amendments
  for each row execute function utrace.reject_mutation();

-- Delivery identities. The marker, branch, and head are what a retry adopts; the
-- verified head is what the sealed Case File records.
alter table public.deliveries
  add column command_id uuid,
  add column base_branch text,
  add column case_file_url text,
  add column pull_request_number integer,
  add column pull_request_node_id text,
  add column verified_head_sha text,
  add column preflight jsonb,
  add column reconciliation jsonb;

alter table public.deliveries
  add constraint deliveries_delivered_identities
  check (
    state <> 'delivered'
    or (
      pull_request_ref is not null
      and pull_request_number is not null
      and verified_head_sha is not null
      and branch_ref is not null
    )
  );

alter table public.deliveries
  add constraint deliveries_blocked_carries_reason
  check (state <> 'blocked' or blocker_reason is not null);

alter table public.case_file_amendments enable row level security;
revoke all on table public.case_file_amendments from anon, authenticated;
grant select on table public.case_file_amendments to authenticated;

create policy case_file_amendments_member_select on public.case_file_amendments
  for select to authenticated
  using (utrace.is_client_member(client_id));
