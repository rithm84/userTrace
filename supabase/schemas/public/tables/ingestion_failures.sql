CREATE TABLE "public"."ingestion_failures" (
  "id"                    uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "activation_id"         uuid,
  "ingestion_receipt_id"  uuid,
  "source_stream_id"      uuid,
  "code"                  text                     NOT NULL,
  "retry_classification"  text                     NOT NULL,
  "readiness_consequence" text                     NOT NULL,
  "affected_watermark"    bigint,
  "affected_trace_id"     text,
  "detail"                jsonb                    NOT NULL,
  "occurred_at"           timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"        smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "ingestion_failures_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "ingestion_failures_pkey" PRIMARY KEY (id),
  CONSTRAINT "ingestion_failures_ingestion_receipt_id_fkey" FOREIGN KEY (ingestion_receipt_id) REFERENCES public.ingestion_receipts(id),
  CONSTRAINT "ingestion_failures_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id)
);

ALTER TABLE "public"."ingestion_failures"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."ingestion_failures"
  ADD COLUMN "stage" public.ingestion_stage NOT NULL;

ALTER TABLE "public"."ingestion_failures"
  ADD COLUMN "producer_kind" public.telemetry_producer_kind NOT NULL;

CREATE INDEX ingestion_failures_activation_idx ON public.ingestion_failures USING btree (activation_id, occurred_at DESC);

CREATE INDEX ingestion_failures_code_idx ON public.ingestion_failures USING btree (code, occurred_at DESC);

CREATE TRIGGER ingestion_failures_immutable
  BEFORE DELETE OR UPDATE ON public.ingestion_failures
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."ingestion_failures" TO "service_role";

REVOKE ALL ON TABLE "public"."ingestion_failures" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."ingestion_failures" TO "postgres";

REVOKE ALL ON TABLE "public"."ingestion_failures" FROM "anon", "authenticated";
