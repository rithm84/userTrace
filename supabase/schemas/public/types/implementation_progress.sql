CREATE TYPE "public"."implementation_progress" AS ENUM (
  'not_started',
  'reproduction_captured',
  'making_changes',
  'preparing_preview',
  'ready_for_validation'
);
