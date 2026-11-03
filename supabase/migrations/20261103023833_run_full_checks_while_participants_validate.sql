ALTER TABLE "public"."preview_versions"
  DROP CONSTRAINT "preview_versions_implementation_attempt_id_key";

ALTER TABLE "public"."preview_versions"
  ADD COLUMN "full_checks_state" text;

ALTER TABLE "public"."preview_versions"
  ADD COLUMN "full_checks" jsonb;

ALTER TABLE "public"."preview_versions"
  ADD COLUMN "full_checks_source_digest" text;

ALTER TABLE "public"."preview_versions"
  ADD COLUMN "full_checks_recorded_at" timestamp WITH time zone;

ALTER TABLE "public"."preview_versions"
  ADD CONSTRAINT "preview_versions_full_checks_state_check" CHECK ((full_checks_state = ANY (ARRAY['running'::text, 'passed'::text, 'failed'::text])));
