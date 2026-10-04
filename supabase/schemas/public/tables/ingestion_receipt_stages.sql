CREATE TABLE "public"."ingestion_receipt_stages" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "ingestion_receipt_id" uuid                     NOT NULL,
  "normalization_run_id" uuid,
  "entered_at"           timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "ingestion_receipt_stages_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "ingestion_receipt_stages_pkey" PRIMARY KEY (id),
  CONSTRAINT "ingestion_receipt_stages_ingestion_receipt_id_fkey" FOREIGN KEY (ingestion_receipt_id) REFERENCES public.ingestion_receipts(id)
);

ALTER TABLE "public"."ingestion_receipt_stages"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."ingestion_receipt_stages"
  ADD COLUMN "stage" public.ingestion_stage NOT NULL;

ALTER TABLE "public"."ingestion_receipt_stages"
  ADD CONSTRAINT "ingestion_receipt_stages_ingestion_receipt_id_stage_key" UNIQUE (ingestion_receipt_id, stage);

CREATE INDEX ingestion_receipt_stages_receipt_idx ON public.ingestion_receipt_stages USING btree (ingestion_receipt_id, entered_at);

CREATE TRIGGER ingestion_receipt_stages_immutable
  BEFORE DELETE OR UPDATE ON public.ingestion_receipt_stages
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."ingestion_receipt_stages" TO "service_role";

REVOKE ALL ON TABLE "public"."ingestion_receipt_stages" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."ingestion_receipt_stages" TO "postgres";

REVOKE ALL ON TABLE "public"."ingestion_receipt_stages" FROM "anon", "authenticated";
