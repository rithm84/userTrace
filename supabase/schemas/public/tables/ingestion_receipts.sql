CREATE TABLE "public"."ingestion_receipts" (
  "id"                       uuid                     NOT NULL,
  "client_id"                uuid                     NOT NULL,
  "activation_id"            uuid                     NOT NULL,
  "telemetry_credential_id"  uuid                     NOT NULL,
  "source_stream_id"         uuid                     NOT NULL,
  "batch_id"                 uuid                     NOT NULL,
  "batch_sequence"           bigint,
  "first_export_sequence"    bigint,
  "last_export_sequence"     bigint,
  "received_at"              timestamp with time zone NOT NULL DEFAULT now(),
  "byte_count"               bigint                   NOT NULL,
  "record_count"             integer                  NOT NULL,
  "content_sha256"           text                     NOT NULL,
  "source_schema_version"    text                     NOT NULL,
  "canonical_schema_version" text                     NOT NULL,
  "policy_version"           text                     NOT NULL,
  "failure_code"             text,
  "failure_reason"           text,
  "retry_classification"     text,
  "raw_artifact_id"          uuid,
  "revision"                 bigint                   NOT NULL DEFAULT 0,
  "created_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"           smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "ingestion_receipts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "ingestion_receipts_counts_nonnegative" CHECK (((byte_count >= 0) AND (record_count >= 0))),
  CONSTRAINT "ingestion_receipts_pkey" PRIMARY KEY (id),
  CONSTRAINT "ingestion_receipts_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id),
  CONSTRAINT "ingestion_receipts_raw_artifact_id_fkey" FOREIGN KEY (raw_artifact_id) REFERENCES public.telemetry_artifacts(id),
  CONSTRAINT "ingestion_receipts_telemetry_credential_id_fkey" FOREIGN KEY (telemetry_credential_id) REFERENCES public.telemetry_credentials(id)
);

ALTER TABLE "public"."ingestion_receipts"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."ingestion_receipts"
  ADD COLUMN "outcome" public.ingestion_outcome NOT NULL;

ALTER TABLE "public"."ingestion_receipts"
  ADD COLUMN "stage" public.ingestion_stage NOT NULL;

ALTER TABLE "public"."ingestion_receipts"
  ADD COLUMN "producer_kind" public.telemetry_producer_kind NOT NULL;

ALTER TABLE "public"."ingestion_receipts"
  ADD CONSTRAINT "ingestion_receipts_failure_only_when_refused" CHECK (((outcome = 'accepted'::public.ingestion_outcome) = (failure_code IS NULL)));

CREATE INDEX ingestion_receipts_activation_idx ON public.ingestion_receipts USING btree (activation_id, received_at DESC);

CREATE INDEX ingestion_receipts_stage_idx ON public.ingestion_receipts USING btree (outcome, stage, received_at);

CREATE UNIQUE INDEX ingestion_receipts_stream_batch ON public.ingestion_receipts USING btree (client_id, source_stream_id, batch_id);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."ingestion_receipts" TO "service_role";

REVOKE ALL ON TABLE "public"."ingestion_receipts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."ingestion_receipts" TO "postgres";

REVOKE ALL ON TABLE "public"."ingestion_receipts" FROM "anon", "authenticated";
