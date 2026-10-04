CREATE TABLE "public"."source_reports" (
  "id"                            uuid                     NOT NULL,
  "client_id"                     uuid                     NOT NULL,
  "product_signal_id"             uuid                     NOT NULL,
  "intake_item_id"                uuid                     NOT NULL,
  "reporting_client_user_id"      uuid                     NOT NULL,
  "reporting_customer_account_id" uuid                     NOT NULL,
  "source_excerpt"                text                     NOT NULL,
  "occurred_at"                   timestamp with time zone NOT NULL,
  "schema_version"                smallint                 NOT NULL DEFAULT 1,
  "created_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "source_reports_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "source_reports_intake_item_id_fkey" FOREIGN KEY (intake_item_id) REFERENCES public.intake_items(id),
  CONSTRAINT "source_reports_pkey" PRIMARY KEY (id),
  CONSTRAINT "source_reports_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id),
  CONSTRAINT "source_reports_product_signal_id_key" UNIQUE (product_signal_id),
  CONSTRAINT "source_reports_reporting_client_user_id_fkey" FOREIGN KEY (reporting_client_user_id) REFERENCES public.client_users(id),
  CONSTRAINT "source_reports_reporting_customer_account_id_fkey" FOREIGN KEY (reporting_customer_account_id) REFERENCES public.customer_accounts(id)
);

ALTER TABLE "public"."source_reports"
  ENABLE ROW LEVEL SECURITY;

CREATE POLICY "source_reports_member_select" ON "public"."source_reports"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."source_reports" TO "service_role";

REVOKE ALL ON TABLE "public"."source_reports" FROM "authenticated";

GRANT SELECT ON TABLE "public"."source_reports" TO "authenticated";

REVOKE ALL ON TABLE "public"."source_reports" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."source_reports" TO "postgres";

REVOKE ALL ON TABLE "public"."source_reports" FROM "anon";
