CREATE TABLE "public"."context_bundles" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "product_signal_id"    uuid                     NOT NULL,
  "interview_session_id" uuid,
  "context_revision"     bigint                   NOT NULL,
  "cutoff_at"            timestamp with time zone NOT NULL,
  "budget_items"         integer                  NOT NULL,
  "included_items"       integer                  NOT NULL,
  "omitted_items"        integer                  NOT NULL,
  "dependencies"         jsonb                    NOT NULL,
  "digest"               text                     NOT NULL,
  "created_at"           timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "context_bundles_budget_positive" CHECK ((budget_items > 0)),
  CONSTRAINT "context_bundles_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "context_bundles_counts_nonnegative" CHECK (((included_items >= 0) AND (omitted_items >= 0))),
  CONSTRAINT "context_bundles_pkey" PRIMARY KEY (id),
  CONSTRAINT "context_bundles_within_budget" CHECK ((included_items <= budget_items)),
  CONSTRAINT "context_bundles_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "context_bundles_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."context_bundles"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."context_bundles"
  ADD COLUMN "purpose" public.context_purpose NOT NULL;

ALTER TABLE "public"."context_bundles"
  ADD CONSTRAINT "context_bundles_product_signal_id_purpose_context_revision_key" UNIQUE (product_signal_id, purpose, context_revision);

CREATE INDEX context_bundles_signal_idx ON public.context_bundles USING btree (product_signal_id, purpose, context_revision DESC);

CREATE TRIGGER context_bundles_immutable
  BEFORE DELETE OR UPDATE ON public.context_bundles
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."context_bundles" TO "service_role";

REVOKE ALL ON TABLE "public"."context_bundles" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."context_bundles" TO "postgres";

REVOKE ALL ON TABLE "public"."context_bundles" FROM "anon", "authenticated";
