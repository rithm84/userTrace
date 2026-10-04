CREATE TABLE "public"."implementation_attempts" (
  "id"                    uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "product_signal_id"     uuid                     NOT NULL,
  "gate_decision_id"      uuid                     NOT NULL,
  "revision"              bigint                   NOT NULL DEFAULT 0,
  "machine_version"       smallint                 NOT NULL DEFAULT 1,
  "repository_ref"        text                     NOT NULL,
  "base_revision"         text                     NOT NULL,
  "consumed_budgets"      jsonb                    NOT NULL,
  "checkpoint"            jsonb,
  "outcome_reason"        text,
  "schema_version"        smallint                 NOT NULL DEFAULT 1,
  "created_at"            timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"            timestamp with time zone NOT NULL DEFAULT now(),
  "progress"              text                     NOT NULL DEFAULT 'not_started'::text,
  "workspace_ref"         text,
  "workspace_snapshot_id" text,
  CONSTRAINT "implementation_attempts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "implementation_attempts_gate_decision_id_key" UNIQUE (gate_decision_id),
  CONSTRAINT "implementation_attempts_pkey" PRIMARY KEY (id),
  CONSTRAINT "implementation_attempts_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."implementation_attempts"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."implementation_attempts"
  ADD COLUMN "state" public.implementation_attempt_state NOT NULL;

CREATE UNIQUE INDEX implementation_attempts_one_in_flight ON public.implementation_attempts USING btree (product_signal_id)
  WHERE
    (state = ANY (ARRAY['authorized'::public.implementation_attempt_state, 'queued'::public.implementation_attempt_state,
    'acquiring_environment'::public.implementation_attempt_state,
    'executing'::public.implementation_attempt_state,
    'checking'::public.implementation_attempt_state,
    'publishing_candidate'::public.implementation_attempt_state, 'paused'::public.implementation_attempt_state, 'infrastructure_blocked'::public.implementation_attempt_state]));

CREATE POLICY "implementation_attempts_member_select" ON "public"."implementation_attempts"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."implementation_attempts" TO "service_role";

REVOKE ALL ON TABLE "public"."implementation_attempts" FROM "authenticated";

GRANT SELECT ON TABLE "public"."implementation_attempts" TO "authenticated";

REVOKE ALL ON TABLE "public"."implementation_attempts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."implementation_attempts" TO "postgres";

REVOKE ALL ON TABLE "public"."implementation_attempts" FROM "anon";
