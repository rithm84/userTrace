CREATE TYPE "public"."telemetry_stream_state" AS ENUM (
  'open',
  'paused',
  'ended',
  'failed'
);
