CREATE TABLE "public"."acceptance_criteria_agreements" (
  "id"                           uuid                     NOT NULL,
  "client_id"                    uuid                     NOT NULL,
  "product_signal_id"            uuid                     NOT NULL,
  "interview_session_id"         uuid                     NOT NULL,
  "acceptance_criteria_revision" bigint                   NOT NULL,
  "agreed_scope"                 jsonb                    NOT NULL,
  "criterion_ids"                uuid[]                   NOT NULL,
  "supporting_evidence_ids"      uuid[]                   NOT NULL,
  "context_bundle_id"            uuid                     NOT NULL,
  "recorded_at"                  timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"               smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "acceptance_criteria_agreement_product_signal_id_acceptance__key" UNIQUE (product_signal_id, acceptance_criteria_revision),
  CONSTRAINT "acceptance_criteria_agreements_pkey" PRIMARY KEY (id),
  CONSTRAINT "acceptance_criteria_agreements_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "acceptance_criteria_agreements_context_bundle_id_fkey" FOREIGN KEY (context_bundle_id) REFERENCES public.context_bundles(id),
  CONSTRAINT "acceptance_criteria_agreements_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "acceptance_criteria_agreements_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."acceptance_criteria_agreements"
  ENABLE ROW LEVEL SECURITY;

CREATE TRIGGER acceptance_criteria_agreements_immutable
  BEFORE DELETE OR UPDATE ON public.acceptance_criteria_agreements
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."acceptance_criteria_agreements" TO "service_role";

REVOKE ALL ON TABLE "public"."acceptance_criteria_agreements" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."acceptance_criteria_agreements" TO "postgres";

REVOKE ALL ON TABLE "public"."acceptance_criteria_agreements" FROM "anon", "authenticated";
