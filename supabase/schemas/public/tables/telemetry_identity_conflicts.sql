CREATE TABLE "public"."telemetry_identity_conflicts" (
  "id"                               uuid                     NOT NULL,
  "client_id"                        uuid                     NOT NULL,
  "activation_id"                    uuid                     NOT NULL,
  "source_stream_id"                 uuid                     NOT NULL,
  "identity_kind"                    text                     NOT NULL,
  "identity_value"                   text                     NOT NULL,
  "first_content_sha256"             text                     NOT NULL,
  "conflicting_content_sha256"       text                     NOT NULL,
  "first_ingestion_receipt_id"       uuid                     NOT NULL,
  "conflicting_ingestion_receipt_id" uuid,
  "detected_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                   smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "telemetry_identity_conflicts_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id),
  CONSTRAINT "telemetry_identity_conflicts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "telemetry_identity_conflicts_conflicting_ingestion_receipt_fkey" FOREIGN KEY (conflicting_ingestion_receipt_id) REFERENCES public.ingestion_receipts(id),
  CONSTRAINT "telemetry_identity_conflicts_first_ingestion_receipt_id_fkey" FOREIGN KEY (first_ingestion_receipt_id) REFERENCES public.ingestion_receipts(id),
  CONSTRAINT "telemetry_identity_conflicts_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."telemetry_identity_conflicts"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."telemetry_identity_conflicts"
  ADD COLUMN "producer_kind" public.telemetry_producer_kind NOT NULL;

CREATE INDEX telemetry_identity_conflicts_stream_idx ON public.telemetry_identity_conflicts USING btree (source_stream_id, detected_at DESC);

CREATE TRIGGER telemetry_identity_conflicts_immutable
  BEFORE DELETE OR UPDATE ON public.telemetry_identity_conflicts
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_identity_conflicts" TO "service_role";

REVOKE ALL ON TABLE "public"."telemetry_identity_conflicts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_identity_conflicts" TO "postgres";

REVOKE ALL ON TABLE "public"."telemetry_identity_conflicts" FROM "anon", "authenticated";
