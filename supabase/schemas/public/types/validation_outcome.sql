CREATE TYPE "public"."validation_outcome" AS ENUM (
  'validated',
  'partially_validated',
  'not_validated',
  'unclear'
);
