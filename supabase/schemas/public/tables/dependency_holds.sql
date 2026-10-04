CREATE TABLE "public"."dependency_holds" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "product_signal_id"               uuid                     NOT NULL,
  "interview_session_id"            uuid,
  "reason"                          text                     NOT NULL,
  "raised_by_evidence_id"           uuid                     NOT NULL,
  "affected_evidence_id"            uuid,
  "semantic_judgement_id"           uuid,
  "blocks_new_implementation_steps" boolean                  NOT NULL,
  "released_at"                     timestamp with time zone,
  "release_reason"                  text,
  "created_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "dependency_holds_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "dependency_holds_pkey" PRIMARY KEY (id),
  CONSTRAINT "dependency_holds_release_reason" CHECK (((released_at IS NULL) = (release_reason IS NULL))),
  CONSTRAINT "dependency_holds_affected_evidence_id_fkey" FOREIGN KEY (affected_evidence_id) REFERENCES public.evidence_records(id),
  CONSTRAINT "dependency_holds_raised_by_evidence_id_fkey" FOREIGN KEY (raised_by_evidence_id) REFERENCES public.evidence_records(id),
  CONSTRAINT "dependency_holds_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "dependency_holds_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id),
  CONSTRAINT "dependency_holds_semantic_judgement_id_fkey" FOREIGN KEY (semantic_judgement_id) REFERENCES public.semantic_judgements(id)
);

ALTER TABLE "public"."dependency_holds"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."dependency_holds"
  ADD COLUMN "hold_kind" public.dependency_hold_kind NOT NULL;

CREATE INDEX dependency_holds_open_idx ON public.dependency_holds USING btree (product_signal_id)
  WHERE (released_at IS NULL);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."dependency_holds" TO "service_role";

REVOKE ALL ON TABLE "public"."dependency_holds" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."dependency_holds" TO "postgres";

REVOKE ALL ON TABLE "public"."dependency_holds" FROM "anon", "authenticated";
