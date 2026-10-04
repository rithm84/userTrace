CREATE TYPE "public"."ingestion_stage" AS ENUM (
  'received',
  'authenticated',
  'source_validated',
  'normalized',
  'indexed',
  'correlated',
  'available'
);
