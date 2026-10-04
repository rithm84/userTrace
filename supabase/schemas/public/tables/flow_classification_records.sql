CREATE TABLE "public"."flow_classification_records" (
  "id"                      uuid                     NOT NULL,
  "client_id"               uuid                     NOT NULL,
  "product_signal_id"       uuid                     NOT NULL,
  "interview_session_id"    uuid,
  "classification_revision" bigint                   NOT NULL,
  "retained"                boolean                  NOT NULL,
  "rationale"               text                     NOT NULL,
  "supporting_evidence_ids" uuid[]                   NOT NULL,
  "context_bundle_id"       uuid                     NOT NULL,
  "semantic_judgement_id"   uuid,
  "recorded_at"             timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"          smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "flow_classification_records_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "flow_classification_records_context_bundle_id_fkey" FOREIGN KEY (context_bundle_id) REFERENCES public.context_bundles(id),
  CONSTRAINT "flow_classification_records_pkey" PRIMARY KEY (id),
  CONSTRAINT "flow_classification_records_product_signal_id_classificatio_key" UNIQUE (product_signal_id, classification_revision),
  CONSTRAINT "flow_classification_records_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "flow_classification_records_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id),
  CONSTRAINT "flow_classification_records_semantic_judgement_id_fkey" FOREIGN KEY (semantic_judgement_id) REFERENCES public.semantic_judgements(id)
);

ALTER TABLE "public"."flow_classification_records"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."flow_classification_records"
  ADD COLUMN "flow_type" public.flow_type NOT NULL;

ALTER TABLE "public"."flow_classification_records"
  ADD COLUMN "initial_flow_type" public.flow_type NOT NULL;

ALTER TABLE "public"."flow_classification_records"
  ADD CONSTRAINT "flow_classification_retained_matches" CHECK ((retained = (flow_type = initial_flow_type)));

CREATE TRIGGER flow_classification_records_immutable
  BEFORE DELETE OR UPDATE ON public.flow_classification_records
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."flow_classification_records" TO "service_role";

REVOKE ALL ON TABLE "public"."flow_classification_records" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."flow_classification_records" TO "postgres";

REVOKE ALL ON TABLE "public"."flow_classification_records" FROM "anon", "authenticated";
