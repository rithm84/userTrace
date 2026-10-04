CREATE TABLE "public"."offer_entitlements" (
  "id"                     uuid                     NOT NULL,
  "client_id"              uuid                     NOT NULL,
  "outreach_offer_id"      uuid                     NOT NULL,
  "product_signal_id"      uuid                     NOT NULL,
  "customer_account_id"    uuid                     NOT NULL,
  "currency"               text                     NOT NULL,
  "amount_minor_units"     bigint                   NOT NULL,
  "participation_evidence" jsonb                    NOT NULL,
  "stop_reason"            text                     NOT NULL,
  "earned_at"              timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"         smallint                 NOT NULL DEFAULT 1,
  "created_at"             timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "offer_entitlements_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "offer_entitlements_customer_account_id_fkey" FOREIGN KEY (customer_account_id) REFERENCES public.customer_accounts(id),
  CONSTRAINT "offer_entitlements_outreach_offer_id_key" UNIQUE (outreach_offer_id),
  CONSTRAINT "offer_entitlements_pkey" PRIMARY KEY (id),
  CONSTRAINT "offer_entitlements_outreach_offer_id_fkey" FOREIGN KEY (outreach_offer_id) REFERENCES public.outreach_offers(id),
  CONSTRAINT "offer_entitlements_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."offer_entitlements"
  ENABLE ROW LEVEL SECURITY;

CREATE TRIGGER offer_entitlements_immutable
  BEFORE DELETE OR UPDATE ON public.offer_entitlements
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE POLICY "offer_entitlements_member_select" ON "public"."offer_entitlements"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_entitlements" TO "service_role";

REVOKE ALL ON TABLE "public"."offer_entitlements" FROM "authenticated";

GRANT SELECT ON TABLE "public"."offer_entitlements" TO "authenticated";

REVOKE ALL ON TABLE "public"."offer_entitlements" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_entitlements" TO "postgres";

REVOKE ALL ON TABLE "public"."offer_entitlements" FROM "anon";
