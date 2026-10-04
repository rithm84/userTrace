CREATE TYPE "public"."evidence_class" AS ENUM (
  'intake_source',
  'transcript_fragment',
  'browser_observation',
  'server_span',
  'implementation_artifact',
  'check_result',
  'validation_observation'
);
