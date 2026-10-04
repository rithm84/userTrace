CREATE TYPE "public"."correlation_status" AS ENUM (
  'pending',
  'complete',
  'partial',
  'ambiguous',
  'invalid',
  'expired'
);
