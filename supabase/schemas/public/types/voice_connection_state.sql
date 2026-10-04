CREATE TYPE "public"."voice_connection_state" AS ENUM (
  'connecting',
  'active',
  'reconnecting',
  'closed'
);
