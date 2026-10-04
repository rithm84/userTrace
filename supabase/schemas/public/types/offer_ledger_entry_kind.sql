CREATE TYPE "public"."offer_ledger_entry_kind" AS ENUM (
  'budget_authorized',
  'reservation_created',
  'reservation_released',
  'credit_earned',
  'credit_issued',
  'issuance_blocked'
);
