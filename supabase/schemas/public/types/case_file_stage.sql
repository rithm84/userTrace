CREATE TYPE "public"."case_file_stage" AS ENUM (
  'draft',
  'verified',
  'delivery_resolved',
  'ready',
  'published'
);
