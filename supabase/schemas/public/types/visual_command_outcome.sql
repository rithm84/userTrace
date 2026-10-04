CREATE TYPE "public"."visual_command_outcome" AS ENUM (
  'applied',
  'rejected',
  'failed',
  'stale_target',
  'canceled',
  'timed_out'
);
