-- Enumerations the interview runtime, context assembly, and extension control
-- add. Kept in their own migration because Postgres refuses to use a value added
-- to an existing enum inside the transaction that added it.
--
-- Contracts: docs/arch-components/browser-extension.md#character-and-controls
--            docs/arch-components/context-assembly.md#provenance-and-concurrent-results
--            docs/arch-components/interview-and-voice.md#transcript-and-evidence-contract

-- The character contract names nine states; the initial enum carried seven.
alter type public.agent_character_state add value if not exists 'paused' after 'concern';
alter type public.agent_character_state add value if not exists 'reconnecting' after 'paused';

-- Extension control.
create type public.extension_account_link_state as enum ('linked', 'revoked');
create type public.extension_binding_status as enum ('bound', 'revoked', 'rejected');
create type public.extension_readiness_state as enum ('ready', 'recovering', 'withdrawn');
create type public.visual_command_kind as enum ('scene', 'scroll_into_view', 'clear_scene');
create type public.visual_command_outcome as enum (
  'applied',
  'rejected',
  'failed',
  'stale_target',
  'canceled',
  'timed_out'
);

-- Voice runtime.
create type public.transcript_speaker as enum ('participant', 'agent');
create type public.playback_delivery as enum ('generated', 'unknown');
create type public.voice_delegation_state as enum (
  'received',
  'assembling',
  'running',
  'answered',
  'refused',
  'failed'
);

-- Context assembly.
create type public.context_purpose as enum (
  'voice_delegation',
  'jev_attention',
  'flow_classification',
  'acceptance_criteria',
  'visual_target'
);
create type public.context_section as enum (
  'recent_conversation',
  'agreed_scope',
  'acceptance_criteria',
  'open_questions',
  'contradictions',
  'selected_observations',
  'implementation_status',
  'lifecycle_and_policy',
  'prior_commands_and_results',
  'product_knowledge'
);
create type public.attention_purpose as enum (
  'evidence_coverage',
  'claim_relationship',
  'attention_routing',
  'visual_target',
  'flow_classification'
);
create type public.dependency_hold_kind as enum ('contradiction_flag', 'accepted_correction');
