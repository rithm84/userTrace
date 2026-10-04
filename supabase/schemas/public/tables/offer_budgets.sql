CREATE TABLE "public"."offer_budgets" (
  "id"                         uuid                     NOT NULL,
  "client_id"                  uuid                     NOT NULL,
  "currency"                   text                     NOT NULL,
  "sequence_number"            bigint                   NOT NULL,
  "authorized_minor_units"     bigint                   NOT NULL,
  "authorized_by_auth_user_id" uuid                     NOT NULL,
  "schema_version"             smallint                 NOT NULL DEFAULT 1,
  "created_at"                 timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "offer_budgets_authorized_by_auth_user_id_fkey" FOREIGN KEY (authorized_by_auth_user_id) REFERENCES auth.users(id),
  CONSTRAINT "offer_budgets_authorized_nonnegative" CHECK ((authorized_minor_units >= 0)),
  CONSTRAINT "offer_budgets_client_id_currency_sequence_number_key" UNIQUE (client_id, currency, sequence_number),
  CONSTRAINT "offer_budgets_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "offer_budgets_currency_is_iso" CHECK ((currency = upper(currency))),
  CONSTRAINT "offer_budgets_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."offer_budgets"
  ENABLE ROW LEVEL SECURITY;

CREATE TRIGGER offer_budgets_immutable
  BEFORE DELETE OR UPDATE ON public.offer_budgets
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE POLICY "offer_budgets_member_select" ON "public"."offer_budgets"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_budgets" TO "service_role";

REVOKE ALL ON TABLE "public"."offer_budgets" FROM "authenticated";

GRANT SELECT ON TABLE "public"."offer_budgets" TO "authenticated";

REVOKE ALL ON TABLE "public"."offer_budgets" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_budgets" TO "postgres";

REVOKE ALL ON TABLE "public"."offer_budgets" FROM "anon";
