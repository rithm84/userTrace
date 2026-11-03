-- Nothing reads or writes these objects. Refuse to run if any of them still holds
-- data, so an unexpected production row stops the migration instead of being lost.
DO $$
DECLARE
  occupied text[] := ARRAY[]::text[];
BEGIN
  IF EXISTS (SELECT 1 FROM public.telemetry_credential_revocations) THEN
    occupied := occupied || 'telemetry_credential_revocations';
  END IF;
  IF EXISTS (SELECT 1 FROM public.evidence_records WHERE artifact_ref IS NOT NULL) THEN
    occupied := occupied || 'evidence_records.artifact_ref';
  END IF;
  IF EXISTS (SELECT 1 FROM public.implementation_check_runs WHERE log_artifact_id IS NOT NULL) THEN
    occupied := occupied || 'implementation_check_runs.log_artifact_id';
  END IF;
  IF EXISTS (SELECT 1 FROM public.outbox WHERE last_error IS NOT NULL) THEN
    occupied := occupied || 'outbox.last_error';
  END IF;
  IF EXISTS (SELECT 1 FROM public.product_signals WHERE closure_detail IS NOT NULL) THEN
    occupied := occupied || 'product_signals.closure_detail';
  END IF;
  IF EXISTS (SELECT 1 FROM public.telemetry_evidence_items WHERE source_observation_id IS NOT NULL) THEN
    occupied := occupied || 'telemetry_evidence_items.source_observation_id';
  END IF;
  IF EXISTS (SELECT 1 FROM public.telemetry_identity_conflicts WHERE conflicting_ingestion_receipt_id IS NOT NULL) THEN
    occupied := occupied || 'telemetry_identity_conflicts.conflicting_ingestion_receipt_id';
  END IF;
  IF EXISTS (SELECT 1 FROM public.validation_outcomes WHERE note IS NOT NULL) THEN
    occupied := occupied || 'validation_outcomes.note';
  END IF;
  IF cardinality(occupied) > 0 THEN
    RAISE EXCEPTION 'refusing to drop schema objects that hold data: %', array_to_string(occupied, ', ');
  END IF;
END
$$;

ALTER TABLE "public"."implementation_check_runs"
  DROP CONSTRAINT "implementation_check_runs_log_artifact_id_fkey";

ALTER TABLE "public"."telemetry_credential_revocations"
  DROP CONSTRAINT "telemetry_credential_revocations_client_id_fkey";

ALTER TABLE "public"."telemetry_credential_revocations"
  DROP CONSTRAINT "telemetry_credential_revocations_telemetry_credential_id_fkey";

ALTER TABLE "public"."telemetry_identity_conflicts"
  DROP CONSTRAINT "telemetry_identity_conflicts_conflicting_ingestion_receipt_fkey";

DROP TYPE "public"."playback_delivery";

DROP TYPE "public"."voice_delegation_state";

ALTER TABLE "public"."evidence_records"
  DROP COLUMN "artifact_ref";

ALTER TABLE "public"."implementation_check_runs"
  DROP COLUMN "log_artifact_id";

ALTER TABLE "public"."outbox"
  DROP COLUMN "last_error";

ALTER TABLE "public"."product_signals"
  DROP COLUMN "closure_detail";

ALTER TABLE "public"."telemetry_evidence_items"
  DROP COLUMN "source_observation_id";

ALTER TABLE "public"."telemetry_identity_conflicts"
  DROP COLUMN "conflicting_ingestion_receipt_id";

ALTER TABLE "public"."validation_outcomes"
  DROP COLUMN "note";

DROP TABLE "public"."telemetry_credential_revocations";
