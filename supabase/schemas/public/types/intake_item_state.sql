CREATE TYPE "public"."intake_item_state" AS ENUM (
  'received',
  'routing',
  'routed',
  'routing_blocked',
  'canceled'
);
