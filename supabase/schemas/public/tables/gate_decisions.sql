CREATE TABLE "public"."gate_decisions" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "product_signal_id"               uuid                     NOT NULL,
  "blocker_reason"                  text,
  "classification_revision"         bigint                   NOT NULL,
  "acceptance_criteria_revision"    bigint                   NOT NULL,
  "pinned_configuration_release_id" uuid                     NOT NULL,
  "evidence_window_id"              uuid                     NOT NULL,
  "approved_scope"                  jsonb                    NOT NULL,
  "required_checks"                 jsonb                    NOT NULL,
  "budgets"                         jsonb                    NOT NULL,
  "cited_evidence"                  jsonb                    NOT NULL,
  "decided_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  "created_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "gate_decisions_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "gate_decisions_pinned_configuration_release_id_fkey" FOREIGN KEY (pinned_configuration_release_id) REFERENCES public.configuration_releases(id),
  CONSTRAINT "gate_decisions_pkey" PRIMARY KEY (id),
  CONSTRAINT "gate_decisions_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."gate_decisions"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."gate_decisions"
  ADD COLUMN "decision" public.gate_decision_kind NOT NULL;

ALTER TABLE "public"."gate_decisions"
  ADD CONSTRAINT "gate_decisions_blocker_only_when_denied" CHECK (((decision = 'no_implementation'::public.gate_decision_kind) = (blocker_reason IS NOT NULL)));

CREATE INDEX gate_decisions_signal_idx ON public.gate_decisions USING btree (product_signal_id, decided_at DESC);

CREATE TRIGGER gate_decisions_immutable
  BEFORE DELETE OR UPDATE ON public.gate_decisions
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE POLICY "gate_decisions_member_select" ON "public"."gate_decisions"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."gate_decisions" TO "service_role";

REVOKE ALL ON TABLE "public"."gate_decisions" FROM "authenticated";

GRANT SELECT ON TABLE "public"."gate_decisions" TO "authenticated";

REVOKE ALL ON TABLE "public"."gate_decisions" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."gate_decisions" TO "postgres";

REVOKE ALL ON TABLE "public"."gate_decisions" FROM "anon";
