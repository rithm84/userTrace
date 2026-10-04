CREATE TYPE "public"."actor_kind" AS ENUM (
  'customer_participant',
  'client_member',
  'utrace_agent_role',
  'provider_identity',
  'service',
  'system'
);
