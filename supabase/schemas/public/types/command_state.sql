CREATE TYPE "public"."command_state" AS ENUM (
  'created',
  'queued',
  'dispatching',
  'dispatched',
  'acknowledged',
  'reconciling',
  'terminal'
);
