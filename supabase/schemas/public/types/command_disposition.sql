CREATE TYPE "public"."command_disposition" AS ENUM (
  'succeeded',
  'rejected',
  'failed',
  'canceled',
  'expired'
);
