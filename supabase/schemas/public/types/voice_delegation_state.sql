CREATE TYPE "public"."voice_delegation_state" AS ENUM (
  'received',
  'assembling',
  'running',
  'answered',
  'refused',
  'failed'
);
