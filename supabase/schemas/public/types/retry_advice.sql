CREATE TYPE "public"."retry_advice" AS ENUM (
  'never',
  'after_delay',
  'after_condition_change'
);
