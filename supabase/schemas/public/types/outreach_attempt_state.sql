CREATE TYPE "public"."outreach_attempt_state" AS ENUM (
  'scheduled',
  'dispatching',
  'awaiting_response',
  'claimed',
  'declined',
  'expired',
  'unreached',
  'revoked'
);
