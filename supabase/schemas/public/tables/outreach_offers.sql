CREATE TABLE "public"."outreach_offers" (
  "id"                       uuid                     NOT NULL,
  "client_id"                uuid                     NOT NULL,
  "product_signal_id"        uuid                     NOT NULL,
  "customer_account_id"      uuid                     NOT NULL,
  "revision"                 bigint                   NOT NULL DEFAULT 0,
  "machine_version"          smallint                 NOT NULL DEFAULT 1,
  "currency"                 text                     NOT NULL,
  "amount_minor_units"       bigint                   NOT NULL,
  "billing_account_ref"      text                     NOT NULL,
  "billing_customer_ref"     text                     NOT NULL,
  "billing_mapping_revision" bigint                   NOT NULL,
  "completion_terms"         jsonb                    NOT NULL,
  "schema_version"           smallint                 NOT NULL DEFAULT 1,
  "created_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"               timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "outreach_offers_amount_positive" CHECK ((amount_minor_units > 0)),
  CONSTRAINT "outreach_offers_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "outreach_offers_customer_account_id_fkey" FOREIGN KEY (customer_account_id) REFERENCES public.customer_accounts(id),
  CONSTRAINT "outreach_offers_pkey" PRIMARY KEY (id),
  CONSTRAINT "outreach_offers_product_signal_id_key" UNIQUE (product_signal_id),
  CONSTRAINT "outreach_offers_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."outreach_offers"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."outreach_offers"
  ADD COLUMN "state" public.offer_state NOT NULL;

CREATE POLICY "outreach_offers_member_select" ON "public"."outreach_offers"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."outreach_offers" TO "service_role";

REVOKE ALL ON TABLE "public"."outreach_offers" FROM "authenticated";

GRANT SELECT ON TABLE "public"."outreach_offers" TO "authenticated";

REVOKE ALL ON TABLE "public"."outreach_offers" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."outreach_offers" TO "postgres";

REVOKE ALL ON TABLE "public"."outreach_offers" FROM "anon";
