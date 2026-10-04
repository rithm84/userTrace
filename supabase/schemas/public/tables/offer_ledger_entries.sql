CREATE TABLE "public"."offer_ledger_entries" (
  "id"                          uuid                     NOT NULL,
  "client_id"                   uuid                     NOT NULL,
  "currency"                    text                     NOT NULL,
  "delta_minor_units"           bigint                   NOT NULL,
  "outreach_offer_id"           uuid,
  "offer_entitlement_id"        uuid,
  "offer_issuance_operation_id" uuid,
  "causal_evidence"             jsonb                    NOT NULL,
  "recorded_at"                 timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"              smallint                 NOT NULL DEFAULT 1,
  "created_at"                  timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "offer_ledger_entries_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "offer_ledger_entries_offer_entitlement_id_fkey" FOREIGN KEY (offer_entitlement_id) REFERENCES public.offer_entitlements(id),
  CONSTRAINT "offer_ledger_entries_offer_issuance_operation_id_fkey" FOREIGN KEY (offer_issuance_operation_id) REFERENCES public.offer_issuance_operations(id),
  CONSTRAINT "offer_ledger_entries_pkey" PRIMARY KEY (id),
  CONSTRAINT "offer_ledger_entries_outreach_offer_id_fkey" FOREIGN KEY (outreach_offer_id) REFERENCES public.outreach_offers(id)
);

ALTER TABLE "public"."offer_ledger_entries"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."offer_ledger_entries"
  ADD COLUMN "entry_kind" public.offer_ledger_entry_kind NOT NULL;

CREATE INDEX offer_ledger_entries_client_currency_idx ON public.offer_ledger_entries USING btree (client_id, currency, recorded_at);

CREATE TRIGGER offer_ledger_entries_immutable
  BEFORE DELETE OR UPDATE ON public.offer_ledger_entries
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_ledger_entries" TO "service_role";

REVOKE ALL ON TABLE "public"."offer_ledger_entries" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_ledger_entries" TO "postgres";

REVOKE ALL ON TABLE "public"."offer_ledger_entries" FROM "anon", "authenticated";
