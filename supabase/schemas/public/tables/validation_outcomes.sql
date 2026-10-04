CREATE TABLE "public"."validation_outcomes" (
  "id"                                uuid                     NOT NULL,
  "client_id"                         uuid                     NOT NULL,
  "validation_attempt_id"             uuid                     NOT NULL,
  "validation_criterion_id"           uuid                     NOT NULL,
  "user_confirmed"                    boolean                  NOT NULL,
  "evidence_record_ids"               uuid[]                   NOT NULL,
  "note"                              text,
  "schema_version"                    smallint                 NOT NULL DEFAULT 1,
  "created_at"                        timestamp with time zone NOT NULL DEFAULT now(),
  "confirmation_evidence_record_id"   uuid,
  "contradicting_evidence_record_ids" uuid[]                   NOT NULL,
  "superseded_claim_record_ids"       uuid[]                   NOT NULL,
  "reason"                            text                     NOT NULL,
  CONSTRAINT "validation_outcomes_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "validation_outcomes_confirmation_evidence_record_id_fkey" FOREIGN KEY (confirmation_evidence_record_id) REFERENCES public.evidence_records(id),
  CONSTRAINT "validation_outcomes_pkey" PRIMARY KEY (id),
  CONSTRAINT "validation_outcomes_validation_attempt_id_fkey" FOREIGN KEY (validation_attempt_id) REFERENCES public.validation_attempts(id),
  CONSTRAINT "validation_outcomes_validation_attempt_id_validation_criter_key" UNIQUE (validation_attempt_id, validation_criterion_id),
  CONSTRAINT "validation_outcomes_validation_criterion_id_fkey" FOREIGN KEY (validation_criterion_id) REFERENCES public.validation_criteria(id)
);

ALTER TABLE "public"."validation_outcomes"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."validation_outcomes"
  ADD COLUMN "observability" public.criterion_observability NOT NULL;

ALTER TABLE "public"."validation_outcomes"
  ADD COLUMN "result" public.criterion_result NOT NULL;

CREATE TRIGGER validation_outcomes_immutable
  BEFORE DELETE OR UPDATE ON public.validation_outcomes
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE POLICY "validation_outcomes_member_select" ON "public"."validation_outcomes"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."validation_outcomes" TO "service_role";

REVOKE ALL ON TABLE "public"."validation_outcomes" FROM "authenticated";

GRANT SELECT ON TABLE "public"."validation_outcomes" TO "authenticated";

REVOKE ALL ON TABLE "public"."validation_outcomes" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."validation_outcomes" TO "postgres";

REVOKE ALL ON TABLE "public"."validation_outcomes" FROM "anon";
