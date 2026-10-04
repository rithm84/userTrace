CREATE TYPE "public"."agent_character_state" AS ENUM (
  'idle',
  'listening',
  'thinking',
  'speaking',
  'acknowledgement',
  'concern',
  'paused',
  'reconnecting',
  'completion'
);
