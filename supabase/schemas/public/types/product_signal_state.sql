CREATE TYPE "public"."product_signal_state" AS ENUM (
  'awaiting_interview',
  'interviewing',
  'gate_pending',
  'implementing',
  'awaiting_validation',
  'validating',
  'closed_validated',
  'closed_no_implementation',
  'closed_blocked',
  'canceled'
);
