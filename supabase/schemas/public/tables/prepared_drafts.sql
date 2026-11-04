CREATE TABLE "public"."prepared_drafts" (
  "id"                    uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "product_signal_id"     uuid                     NOT NULL,
  "interview_session_id"  uuid                     NOT NULL,
  "state"                 text                     NOT NULL,
  "proposed_scope"        jsonb                    NOT NULL,
  "criteria"              jsonb                    NOT NULL,
  "proposal_digest"       text                     NOT NULL,
  "prepared_workspace_id" uuid,
  "files"                 jsonb,
  "files_digest"          text,
  "patch"                 text,
  "consumed_budgets"      jsonb,
  "fast_checks"           jsonb,
  "staged_runtime_ref"    text,
  "failure_detail"        text,
  "implementation_attempt_id" uuid,
  "kind"                  text                     NOT NULL DEFAULT 'proposal'::text,
  "agent_cost_cents"      integer                  NOT NULL DEFAULT 0,
  "gate_proposal"         jsonb,
  "gate_inputs_digest"    text,
  "preview_version_id"    uuid,
  "source_digest"         text,
  "dependencies"          jsonb,
  "artifacts"             jsonb,
  "proposed_at"           timestamp with time zone NOT NULL DEFAULT now(),
  "settled_at"            timestamp with time zone,
  "updated_at"            timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "prepared_drafts_pkey" PRIMARY KEY (id),
  CONSTRAINT "prepared_drafts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "prepared_drafts_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id),
  CONSTRAINT "prepared_drafts_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "prepared_drafts_prepared_workspace_id_fkey" FOREIGN KEY (prepared_workspace_id) REFERENCES public.prepared_workspaces(id),
  CONSTRAINT "prepared_drafts_implementation_attempt_id_fkey" FOREIGN KEY (implementation_attempt_id) REFERENCES public.implementation_attempts(id),
  CONSTRAINT "prepared_drafts_state_check" CHECK ((state = ANY (ARRAY['proposed'::text, 'drafting'::text, 'drafted'::text, 'failed'::text, 'adopted'::text, 'discarded'::text]))),
  CONSTRAINT "prepared_drafts_drafted_check" CHECK (((state <> ALL (ARRAY['drafted'::text, 'adopted'::text])) OR ((files IS NOT NULL) AND (files_digest IS NOT NULL) AND (patch IS NOT NULL)))),
  CONSTRAINT "prepared_drafts_adopted_check" CHECK (((state <> 'adopted'::text) OR (implementation_attempt_id IS NOT NULL))),
  CONSTRAINT "prepared_drafts_kind_check" CHECK ((kind = ANY (ARRAY['intake'::text, 'proposal'::text])))
);

ALTER TABLE "public"."prepared_drafts"
  ENABLE ROW LEVEL SECURITY;

CREATE UNIQUE INDEX prepared_drafts_one_open_per_kind ON public.prepared_drafts USING btree (product_signal_id, kind)
  WHERE (state = ANY (ARRAY['proposed'::text, 'drafting'::text, 'drafted'::text]));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."prepared_drafts" TO "service_role";

REVOKE ALL ON TABLE "public"."prepared_drafts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."prepared_drafts" TO "postgres";

REVOKE ALL ON TABLE "public"."prepared_drafts" FROM "anon", "authenticated";
