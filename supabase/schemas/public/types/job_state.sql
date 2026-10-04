CREATE TYPE "public"."job_state" AS ENUM (
  'pending',
  'ready',
  'leased',
  'succeeded',
  'failed',
  'canceled',
  'expired'
);
