CREATE TYPE "public"."outbox_state" AS ENUM (
  'pending',
  'enqueued',
  'dispatched',
  'failed'
);
