CREATE TABLE "public"."validation_attempts" (
  "id"                            uuid                     NOT NULL,
  "client_id"                     uuid                     NOT NULL,
  "product_signal_id"             uuid                     NOT NULL,
  "implementation_attempt_id"     uuid                     NOT NULL,
  "preview_version_id"            uuid                     NOT NULL,
  "preview_runtime_allocation_id" uuid                     NOT NULL,
  "interview_session_id"          uuid                     NOT NULL,
  "client_user_id"                uuid                     NOT NULL,
  "revision"                      bigint                   NOT NULL DEFAULT 0,
  "machine_version"               smallint                 NOT NULL DEFAULT 1,
  "dataset_manifest_digest"       text                     NOT NULL,
  "acceptance_criteria_revision"  bigint                   NOT NULL,
  "completed_at"                  timestamp with time zone,
  "schema_version"                smallint                 NOT NULL DEFAULT 1,
  "created_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  "authenticated_identity_ref"    text                     NOT NULL,
  "workflow_ref"                  text                     NOT NULL,
  "evidence_window_id"            uuid,
  "interrupted_at"                timestamp with time zone,
  "frozen_result"                 jsonb,
  "frozen_result_digest"          text,
  CONSTRAINT "validation_attempts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "validation_attempts_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id),
  CONSTRAINT "validation_attempts_evidence_window_id_fkey" FOREIGN KEY (evidence_window_id) REFERENCES public.evidence_windows(id),
  CONSTRAINT "validation_attempts_frozen_digest_with_result" CHECK (((frozen_result IS NULL) = (frozen_result_digest IS NULL))),
  CONSTRAINT "validation_attempts_implementation_attempt_id_fkey" FOREIGN KEY (implementation_attempt_id) REFERENCES public.implementation_attempts(id),
  CONSTRAINT "validation_attempts_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "validation_attempts_pkey" PRIMARY KEY (id),
  CONSTRAINT "validation_attempts_preview_runtime_allocation_id_fkey" FOREIGN KEY (preview_runtime_allocation_id) REFERENCES public.preview_runtime_allocations(id),
  CONSTRAINT "validation_attempts_preview_version_id_fkey" FOREIGN KEY (preview_version_id) REFERENCES public.preview_versions(id),
  CONSTRAINT "validation_attempts_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."validation_attempts"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."validation_attempts"
  ADD COLUMN "state" public.validation_attempt_state NOT NULL;

ALTER TABLE "public"."validation_attempts"
  ADD COLUMN "outcome" public.validation_outcome;

ALTER TABLE "public"."validation_attempts"
  ADD CONSTRAINT "validation_attempts_frozen_result_when_completed" CHECK (((state = 'completed'::public.validation_attempt_state) = (frozen_result IS NOT NULL)));

ALTER TABLE "public"."validation_attempts"
  ADD CONSTRAINT "validation_attempts_outcome_when_completed" CHECK (((state = 'completed'::public.validation_attempt_state) = (outcome IS NOT NULL)));

CREATE UNIQUE INDEX validation_attempts_one_open ON public.validation_attempts USING btree (product_signal_id)
  WHERE (state = ANY (ARRAY['pending'::public.validation_attempt_state, 'in_progress'::public.validation_attempt_state, 'interrupted'::public.validation_attempt_state]));

CREATE INDEX validation_attempts_signal_idx ON public.validation_attempts USING btree (product_signal_id, state);

CREATE POLICY "validation_attempts_member_select" ON "public"."validation_attempts"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."validation_attempts" TO "service_role";

REVOKE ALL ON TABLE "public"."validation_attempts" FROM "authenticated";

GRANT SELECT ON TABLE "public"."validation_attempts" TO "authenticated";

REVOKE ALL ON TABLE "public"."validation_attempts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."validation_attempts" TO "postgres";

REVOKE ALL ON TABLE "public"."validation_attempts" FROM "anon";
