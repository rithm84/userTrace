CREATE TYPE "public"."job_run_state" AS ENUM (
  'leased',
  'succeeded',
  'failed',
  'abandoned',
  'timed_out'
);
