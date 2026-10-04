CREATE TYPE "public"."preview_version_state" AS ENUM (
  'building',
  'ready',
  'retired',
  'failed'
);
