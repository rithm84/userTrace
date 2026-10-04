CREATE TABLE "public"."offer_invoice_applications" (
  "id"                          uuid                     NOT NULL,
  "client_id"                   uuid                     NOT NULL,
  "offer_issuance_operation_id" uuid                     NOT NULL,
  "billing_customer_ref"        text                     NOT NULL,
  "invoice_ref"                 text                     NOT NULL,
  "currency"                    text                     NOT NULL,
  "invoice_total_minor_units"   bigint                   NOT NULL,
  "applied_minor_units"         bigint                   NOT NULL,
  "amount_due_minor_units"      bigint                   NOT NULL,
  "invoice_status"              text                     NOT NULL,
  "reconciled_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"              smallint                 NOT NULL DEFAULT 1,
  "created_at"                  timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "offer_invoice_applications_applied_nonnegative" CHECK ((applied_minor_units >= 0)),
  CONSTRAINT "offer_invoice_applications_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "offer_invoice_applications_offer_issuance_operation_id_invo_key" UNIQUE (offer_issuance_operation_id, invoice_ref),
  CONSTRAINT "offer_invoice_applications_pkey" PRIMARY KEY (id),
  CONSTRAINT "offer_invoice_applications_offer_issuance_operation_id_fkey" FOREIGN KEY (offer_issuance_operation_id) REFERENCES public.offer_issuance_operations(id)
);

ALTER TABLE "public"."offer_invoice_applications"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX offer_invoice_applications_client_idx ON public.offer_invoice_applications USING btree (client_id, billing_customer_ref);

CREATE TRIGGER offer_invoice_applications_immutable
  BEFORE DELETE OR UPDATE ON public.offer_invoice_applications
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE POLICY "offer_invoice_applications_member_select" ON "public"."offer_invoice_applications"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_invoice_applications" TO "service_role";

REVOKE ALL ON TABLE "public"."offer_invoice_applications" FROM "authenticated";

GRANT SELECT ON TABLE "public"."offer_invoice_applications" TO "authenticated";

REVOKE ALL ON TABLE "public"."offer_invoice_applications" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_invoice_applications" TO "postgres";

REVOKE ALL ON TABLE "public"."offer_invoice_applications" FROM "anon";
