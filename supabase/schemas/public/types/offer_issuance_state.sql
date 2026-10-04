CREATE TYPE "public"."offer_issuance_state" AS ENUM (
  'pending',
  'dispatched',
  'issued',
  'blocked'
);
