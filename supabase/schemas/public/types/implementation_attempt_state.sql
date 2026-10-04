CREATE TYPE "public"."implementation_attempt_state" AS ENUM (
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
