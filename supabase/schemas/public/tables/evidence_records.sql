CREATE TABLE "public"."evidence_records" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "product_signal_id"    uuid                     NOT NULL,
  "interview_session_id" uuid,
  "source_ref"           text                     NOT NULL,
  "context_revision"     bigint                   NOT NULL,
  "artifact_ref"         jsonb,
  "body"                 jsonb                    NOT NULL,
  "supersedes_id"        uuid,
  "recorded_at"          timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  "created_at"           timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "evidence_records_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "evidence_records_pkey" PRIMARY KEY (id),
  CONSTRAINT "evidence_records_supersedes_id_fkey" FOREIGN KEY (supersedes_id) REFERENCES public.evidence_records(id),
  CONSTRAINT "evidence_records_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "evidence_records_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."evidence_records"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."evidence_records"
  ADD COLUMN "claim_state" public.evidence_claim_state NOT NULL;

ALTER TABLE "public"."evidence_records"
  ADD COLUMN "evidence_class" public.evidence_class NOT NULL;

CREATE INDEX evidence_records_signal_idx ON public.evidence_records USING btree (product_signal_id, evidence_class);

CREATE TRIGGER evidence_records_immutable
  BEFORE DELETE OR UPDATE ON public.evidence_records
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."evidence_records" TO "service_role";

REVOKE ALL ON TABLE "public"."evidence_records" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."evidence_records" TO "postgres";

REVOKE ALL ON TABLE "public"."evidence_records" FROM "anon", "authenticated";
