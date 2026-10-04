CREATE TYPE "public"."telemetry_activation_state" AS ENUM (
  'active',
  'paused',
  'superseded',
  'revoked',
  'expired'
);
