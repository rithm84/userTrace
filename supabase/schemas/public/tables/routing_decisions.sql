CREATE TABLE "public"."routing_decisions" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "intake_item_id"                  uuid                     NOT NULL,
  "attempt_index"                   integer                  NOT NULL,
  "blocked_reason"                  text,
  "product_signal_id"               uuid,
  "source_report_id"                uuid,
  "pinned_configuration_release_id" uuid                     NOT NULL,
  "product_overview_version_id"     uuid                     NOT NULL,
  "decision_model_id"               text                     NOT NULL,
  "questions"                       jsonb                    NOT NULL,
  "answers"                         jsonb                    NOT NULL,
  "acceptance"                      jsonb                    NOT NULL,
  "assistance_reason"               text,
  "assisted_interpretation"         text,
  "decided_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  "created_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "routing_decisions_attempt_index_positive" CHECK ((attempt_index > 0)),
  CONSTRAINT "routing_decisions_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "routing_decisions_intake_item_id_attempt_index_key" UNIQUE (intake_item_id, attempt_index),
  CONSTRAINT "routing_decisions_intake_item_id_fkey" FOREIGN KEY (intake_item_id) REFERENCES public.intake_items(id),
  CONSTRAINT "routing_decisions_pinned_configuration_release_id_fkey" FOREIGN KEY (pinned_configuration_release_id) REFERENCES public.configuration_releases(id),
  CONSTRAINT "routing_decisions_pkey" PRIMARY KEY (id),
  CONSTRAINT "routing_decisions_product_overview_version_id_fkey" FOREIGN KEY (product_overview_version_id) REFERENCES public.product_overview_versions(id),
  CONSTRAINT "routing_decisions_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id),
  CONSTRAINT "routing_decisions_source_report_id_fkey" FOREIGN KEY (source_report_id) REFERENCES public.source_reports(id)
);

ALTER TABLE "public"."routing_decisions"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."routing_decisions"
  ADD COLUMN "outcome" public.routing_outcome;

ALTER TABLE "public"."routing_decisions"
  ADD CONSTRAINT "routing_decisions_outcome_or_blocker" CHECK ((num_nonnulls(outcome, blocked_reason) = 1));

CREATE INDEX routing_decisions_client_idx ON public.routing_decisions USING btree (client_id, decided_at DESC);

CREATE TRIGGER routing_decisions_immutable
  BEFORE DELETE OR UPDATE ON public.routing_decisions
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."routing_decisions" TO "service_role";

REVOKE ALL ON TABLE "public"."routing_decisions" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."routing_decisions" TO "postgres";

REVOKE ALL ON TABLE "public"."routing_decisions" FROM "anon", "authenticated";
