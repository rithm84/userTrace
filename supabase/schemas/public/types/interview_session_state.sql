CREATE TYPE "public"."interview_session_state" AS ENUM (
  'invited',
  'claimed',
  'preparing',
  'live',
  'closed'
);
