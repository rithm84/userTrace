CREATE TABLE "public"."case_files" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "product_signal_id"         uuid                     NOT NULL,
  "revision"                  bigint                   NOT NULL DEFAULT 0,
  "machine_version"           smallint                 NOT NULL DEFAULT 1,
  "core"                      jsonb,
  "core_digest"               text,
  "delivery_id"               uuid,
  "sealed_at"                 timestamp with time zone,
  "published_at"              timestamp with time zone,
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  "created_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "title"                     text,
  "context_revision"          bigint,
  "narrative_source"          text,
  "authenticated_url"         text,
  "blocker_reason"            text,
  "core_canonical"            text,
  "validation_attempt_id"     uuid,
  "implementation_attempt_id" uuid,
  "preview_version_id"        uuid,
  "gate_decision_id"          uuid,
  "evidence_window_id"        uuid,
  "originating_user_ref"      text,
  "customer_account_ref"      text,
  CONSTRAINT "case_files_narrative_source_values" CHECK (((narrative_source IS NULL) OR (narrative_source = ANY (ARRAY['model_drafted'::text, 'evidence_derived'::text])))),
  CONSTRAINT "case_files_pkey" PRIMARY KEY (id),
  CONSTRAINT "case_files_product_signal_id_key" UNIQUE (product_signal_id),
  CONSTRAINT "case_files_sealed_bytes" CHECK (((core_digest IS NULL) = (core_canonical IS NULL))),
  CONSTRAINT "case_files_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "case_files_evidence_window_id_fkey" FOREIGN KEY (evidence_window_id) REFERENCES public.evidence_windows(id),
  CONSTRAINT "case_files_gate_decision_id_fkey" FOREIGN KEY (gate_decision_id) REFERENCES public.gate_decisions(id),
  CONSTRAINT "case_files_implementation_attempt_id_fkey" FOREIGN KEY (implementation_attempt_id) REFERENCES public.implementation_attempts(id),
  CONSTRAINT "case_files_preview_version_id_fkey" FOREIGN KEY (preview_version_id) REFERENCES public.preview_versions(id),
  CONSTRAINT "case_files_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id),
  CONSTRAINT "case_files_validation_attempt_id_fkey" FOREIGN KEY (validation_attempt_id) REFERENCES public.validation_attempts(id)
);

ALTER TABLE "public"."case_files"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."case_files"
  ADD COLUMN "stage" public.case_file_stage NOT NULL;

ALTER TABLE "public"."case_files"
  ADD COLUMN "variant" public.case_file_variant NOT NULL;

ALTER TABLE "public"."case_files"
  ADD CONSTRAINT "case_files_sealed_core" CHECK (((stage = ANY (ARRAY['ready'::public.case_file_stage, 'published'::public.case_file_stage])) = (core_digest IS NOT NULL)));

CREATE INDEX case_files_client_stage_idx ON public.case_files USING btree (client_id, stage);

CREATE POLICY "case_files_member_select" ON "public"."case_files"
  FOR SELECT
  TO "authenticated"
  USING ((utrace.is_client_member(client_id) AND (stage = 'published'::public.case_file_stage)));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."case_files" TO "service_role";

REVOKE ALL ON TABLE "public"."case_files" FROM "authenticated";

GRANT SELECT ON TABLE "public"."case_files" TO "authenticated";

REVOKE ALL ON TABLE "public"."case_files" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."case_files" TO "postgres";

REVOKE ALL ON TABLE "public"."case_files" FROM "anon";
