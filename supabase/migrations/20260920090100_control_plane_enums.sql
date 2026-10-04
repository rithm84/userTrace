-- Typed lifecycle enumerations. Values are the exact snake_case names used by the
-- PRD and the lifecycle diagrams; adding a value stays expand-only.

-- Aggregate kinds double as the primary lifecycle stream kind for Events.
create type public.aggregate_kind as enum (
  'intake_item',
  'product_signal',
  'outreach_attempt',
  'invitation',
  'interview_session',
  'voice_connection',
  'preview_runtime_allocation',
  'environment_lease',
  'implementation_attempt',
  'preview_version',
  'validation_attempt',
  'case_file',
  'delivery',
  'outreach_offer',
  'job',
  'job_run',
  'command'
);

create type public.actor_kind as enum (
  'customer_participant',
  'client_member',
  'utrace_agent_role',
  'provider_identity',
  'service',
  'system'
);

create type public.client_member_role as enum ('owner', 'admin');
create type public.membership_status as enum ('active', 'revoked');

create type public.intake_item_state as enum (
  'received',
  'routing',
  'routed',
  'routing_blocked',
  'canceled'
);

create type public.routing_outcome as enum (
  'close_as_noise',
  'create_product_signal'
);

create type public.flow_type as enum ('bugfix_flow', 'feature_request_flow');

create type public.product_signal_state as enum (
  'awaiting_interview',
  'interviewing',
  'gate_pending',
  'implementing',
  'awaiting_validation',
  'validating',
  'closed_validated',
  'closed_no_implementation',
  'closed_blocked',
  'canceled'
);

create type public.outreach_purpose as enum (
  'initial_invitation',
  'return_reminder'
);

create type public.outreach_attempt_state as enum (
  'scheduled',
  'dispatching',
  'awaiting_response',
  'claimed',
  'declined',
  'expired',
  'unreached',
  'revoked'
);

create type public.interview_session_state as enum (
  'invited',
  'claimed',
  'preparing',
  'live',
  'closed'
);

create type public.interview_phase as enum (
  'participant_setup',
  'greeting',
  'discovery',
  'implementation',
  'validation',
  'closing'
);

create type public.agent_character_state as enum (
  'idle',
  'listening',
  'thinking',
  'speaking',
  'acknowledgement',
  'concern',
  'completion'
);

create type public.implementation_progress as enum (
  'not_started',
  'reproduction_captured',
  'making_changes',
  'preparing_preview',
  'ready_for_validation'
);

create type public.voice_connection_state as enum (
  'connecting',
  'active',
  'reconnecting',
  'closed'
);

create type public.gate_decision_kind as enum (
  'approved_live',
  'no_implementation'
);

create type public.implementation_attempt_state as enum (
  'authorized',
  'queued',
  'acquiring_environment',
  'executing',
  'checking',
  'publishing_candidate',
  'candidate_ready',
  'scope_change_required',
  'checks_failed',
  'unsafe',
  'infrastructure_blocked',
  'paused',
  'canceled',
  'budget_exhausted'
);

create type public.environment_lease_state as enum ('active', 'fenced', 'released', 'expired');

create type public.preview_allocation_kind as enum ('base', 'candidate');

create type public.preview_runtime_allocation_state as enum (
  'pending',
  'seeding',
  'ready',
  'bound',
  'released',
  'terminated'
);

create type public.preview_version_state as enum ('building', 'ready', 'retired', 'failed');

create type public.validation_attempt_state as enum (
  'pending',
  'in_progress',
  'interrupted',
  'completed',
  'abandoned'
);

create type public.validation_outcome as enum (
  'validated',
  'partially_validated',
  'not_validated',
  'unclear'
);

create type public.criterion_observability as enum ('observed', 'subjective');
create type public.criterion_result as enum ('met', 'unmet', 'unconfirmed', 'contradicted');

create type public.case_file_variant as enum (
  'validated_implementation',
  'no_implementation',
  'blocked'
);

create type public.case_file_stage as enum (
  'draft',
  'verified',
  'delivery_resolved',
  'ready',
  'published'
);

create type public.delivery_state as enum (
  'reserved',
  'preflighting',
  'creating',
  'reconciling',
  'delivered',
  'blocked'
);

create type public.evidence_class as enum (
  'intake_source',
  'transcript_fragment',
  'browser_observation',
  'server_span',
  'implementation_artifact',
  'check_result',
  'validation_observation'
);

create type public.evidence_claim_state as enum (
  'proposed',
  'confirmed',
  'contradicted',
  'withdrawn'
);

create type public.offer_state as enum ('reserved', 'earned', 'released');
create type public.offer_issuance_state as enum ('pending', 'dispatched', 'issued', 'blocked');
create type public.offer_ledger_entry_kind as enum (
  'budget_authorized',
  'reservation_created',
  'reservation_released',
  'credit_earned',
  'credit_issued',
  'issuance_blocked'
);

create type public.job_state as enum (
  'pending',
  'ready',
  'leased',
  'succeeded',
  'failed',
  'canceled',
  'expired'
);

create type public.job_run_state as enum ('leased', 'succeeded', 'failed', 'abandoned', 'timed_out');

create type public.command_state as enum (
  'created',
  'queued',
  'dispatching',
  'dispatched',
  'acknowledged',
  'reconciling',
  'terminal'
);

create type public.command_disposition as enum (
  'succeeded',
  'rejected',
  'failed',
  'canceled',
  'expired'
);

create type public.effect_state as enum ('absent', 'partial', 'completed', 'indeterminate');
create type public.retry_advice as enum ('never', 'after_delay', 'after_condition_change');
create type public.result_kind as enum ('accepted', 'progress', 'terminal');
create type public.outbox_state as enum ('pending', 'enqueued', 'dispatched', 'failed');
