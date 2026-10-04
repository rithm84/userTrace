CREATE TABLE "public"."product_signals" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "revision"                        bigint                   NOT NULL DEFAULT 0,
  "machine_version"                 smallint                 NOT NULL DEFAULT 1,
  "intake_item_id"                  uuid                     NOT NULL,
  "pinned_configuration_release_id" uuid                     NOT NULL,
  "originating_client_user_id"      uuid                     NOT NULL,
  "originating_customer_account_id" uuid                     NOT NULL,
  "classification_revision"         bigint                   NOT NULL DEFAULT 0,
  "concern_revision"                bigint                   NOT NULL DEFAULT 0,
  "acceptance_criteria_revision"    bigint                   NOT NULL DEFAULT 0,
  "cancellation_scope_id"           uuid                     NOT NULL,
  "return_deadline_at"              timestamp with time zone,
  "closure_reason"                  text,
  "closure_detail"                  jsonb,
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  "created_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "product_signals_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "product_signals_originating_client_user_id_fkey" FOREIGN KEY (originating_client_user_id) REFERENCES public.client_users(id),
  CONSTRAINT "product_signals_originating_customer_account_id_fkey" FOREIGN KEY (originating_customer_account_id) REFERENCES public.customer_accounts(id),
  CONSTRAINT "product_signals_pinned_configuration_release_id_fkey" FOREIGN KEY (pinned_configuration_release_id) REFERENCES public.configuration_releases(id),
  CONSTRAINT "product_signals_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."product_signals"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."product_signals"
  ADD COLUMN "initial_flow_type" public.flow_type NOT NULL;

ALTER TABLE "public"."product_signals"
  ADD COLUMN "established_flow_type" public.flow_type;

ALTER TABLE "public"."product_signals"
  ADD COLUMN "state" public.product_signal_state NOT NULL;

CREATE INDEX product_signals_cancellation_scope_idx ON public.product_signals USING btree (cancellation_scope_id);

CREATE INDEX product_signals_client_state_idx ON public.product_signals USING btree (client_id, state);

CREATE INDEX product_signals_participant_idx ON public.product_signals USING btree (originating_client_user_id);

CREATE POLICY "product_signals_member_select" ON "public"."product_signals"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."product_signals" TO "service_role";

REVOKE ALL ON TABLE "public"."product_signals" FROM "authenticated";

GRANT SELECT ON TABLE "public"."product_signals" TO "authenticated";

REVOKE ALL ON TABLE "public"."product_signals" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."product_signals" TO "postgres";

REVOKE ALL ON TABLE "public"."product_signals" FROM "anon";
