DROP INDEX "public"."voice_connections_one_current";

ALTER TABLE "public"."voice_delegations"
  DROP CONSTRAINT "voice_delegations_client_id_fkey";

ALTER TABLE "public"."voice_delegations"
  DROP CONSTRAINT "voice_delegations_context_bundle_id_fkey";

ALTER TABLE "public"."voice_delegations"
  DROP CONSTRAINT "voice_delegations_interview_session_id_fkey";

ALTER TABLE "public"."voice_delegations"
  DROP CONSTRAINT "voice_delegations_voice_connection_id_fkey";

ALTER TABLE "public"."voice_playback_segments"
  DROP CONSTRAINT "voice_playback_segments_client_id_fkey";

ALTER TABLE "public"."voice_playback_segments"
  DROP CONSTRAINT "voice_playback_segments_interview_session_id_fkey";

ALTER TABLE "public"."voice_playback_segments"
  DROP CONSTRAINT "voice_playback_segments_voice_connection_id_fkey";

DROP TABLE "public"."voice_delegations";

DROP TABLE "public"."voice_playback_segments";

ALTER TYPE "public"."context_purpose" RENAME TO "context_purpose__pgdelta_replaced";

CREATE TYPE "public"."context_purpose" AS ENUM (
  'voice_delegation',
  'jev_attention',
  'flow_classification'
);

ALTER TABLE "public"."context_bundles"
  ALTER COLUMN "purpose" TYPE "public"."context_purpose" USING "purpose"::text::"public"."context_purpose";

DROP TYPE "public"."context_purpose__pgdelta_replaced";

ALTER TYPE "public"."voice_connection_state" RENAME TO "voice_connection_state__pgdelta_replaced";

CREATE TYPE "public"."voice_connection_state" AS ENUM (
  'connecting',
  'active',
  'closed'
);

ALTER TABLE "public"."voice_connections"
  ALTER COLUMN "state" TYPE "public"."voice_connection_state" USING "state"::text::"public"."voice_connection_state";

DROP TYPE "public"."voice_connection_state__pgdelta_replaced";

CREATE UNIQUE INDEX voice_connections_one_current ON public.voice_connections USING btree (interview_session_id)
  WHERE (state <> 'closed'::public.voice_connection_state);
