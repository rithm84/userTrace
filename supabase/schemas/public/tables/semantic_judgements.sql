CREATE TABLE "public"."semantic_judgements" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "product_signal_id"    uuid                     NOT NULL,
  "interview_session_id" uuid,
  "context_bundle_id"    uuid                     NOT NULL,
  "model_id"             text                     NOT NULL,
  "question_ids"         text[]                   NOT NULL,
  "answers"              jsonb                    NOT NULL,
  "acceptance"           jsonb                    NOT NULL,
  "accepted"             boolean                  NOT NULL,
  "rejections"           text[]                   NOT NULL,
  "created_at"           timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "semantic_judgements_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "semantic_judgements_context_bundle_id_fkey" FOREIGN KEY (context_bundle_id) REFERENCES public.context_bundles(id),
  CONSTRAINT "semantic_judgements_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "semantic_judgements_pkey" PRIMARY KEY (id),
  CONSTRAINT "semantic_judgements_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."semantic_judgements"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."semantic_judgements"
  ADD COLUMN "purpose" public.attention_purpose NOT NULL;

CREATE INDEX semantic_judgements_signal_idx ON public.semantic_judgements USING btree (product_signal_id, purpose, created_at DESC);

CREATE TRIGGER semantic_judgements_immutable
  BEFORE DELETE OR UPDATE ON public.semantic_judgements
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."semantic_judgements" TO "service_role";

REVOKE ALL ON TABLE "public"."semantic_judgements" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."semantic_judgements" TO "postgres";

REVOKE ALL ON TABLE "public"."semantic_judgements" FROM "anon", "authenticated";
