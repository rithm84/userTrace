CREATE TYPE "public"."ingestion_outcome" AS ENUM (
  'accepted',
  'rejected',
  'quarantined'
);
