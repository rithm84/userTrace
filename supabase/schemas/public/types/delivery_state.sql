CREATE TYPE "public"."delivery_state" AS ENUM (
  'reserved',
  'preflighting',
  'creating',
  'reconciling',
  'delivered',
  'blocked'
);
