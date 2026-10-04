CREATE TYPE "public"."preview_runtime_allocation_state" AS ENUM (
  'pending',
  'seeding',
  'ready',
  'bound',
  'released',
  'terminated'
);
