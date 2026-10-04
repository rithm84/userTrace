CREATE TYPE "public"."environment_lease_state" AS ENUM (
  'active',
  'fenced',
  'released',
  'expired'
);
