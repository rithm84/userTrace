CREATE TYPE "public"."preview_readiness_outcome" AS ENUM (
  'passed',
  'failed',
  'blocked',
  'not_applicable'
);
