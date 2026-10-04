CREATE TYPE "public"."evidence_claim_state" AS ENUM (
  'proposed',
  'confirmed',
  'contradicted',
  'withdrawn'
);
