CREATE TYPE "public"."validation_attempt_state" AS ENUM (
  'pending',
  'in_progress',
  'interrupted',
  'completed',
  'abandoned'
);
