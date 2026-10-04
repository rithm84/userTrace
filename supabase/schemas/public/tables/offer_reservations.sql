CREATE TABLE "public"."offer_reservations" (
  "id"                 uuid                     NOT NULL,
  "client_id"          uuid                     NOT NULL,
  "outreach_offer_id"  uuid                     NOT NULL,
  "currency"           text                     NOT NULL,
  "amount_minor_units" bigint                   NOT NULL,
  "released_at"        timestamp with time zone,
  "converted_at"       timestamp with time zone,
  "schema_version"     smallint                 NOT NULL DEFAULT 1,
  "created_at"         timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"         timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "offer_reservations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "offer_reservations_outreach_offer_id_key" UNIQUE (outreach_offer_id),
  CONSTRAINT "offer_reservations_pkey" PRIMARY KEY (id),
  CONSTRAINT "offer_reservations_single_disposition" CHECK (((released_at IS NULL) OR (converted_at IS NULL))),
  CONSTRAINT "offer_reservations_outreach_offer_id_fkey" FOREIGN KEY (outreach_offer_id) REFERENCES public.outreach_offers(id)
);

ALTER TABLE "public"."offer_reservations"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX offer_reservations_open_idx ON public.offer_reservations USING btree (client_id, currency)
  WHERE (released_at IS NULL);

CREATE POLICY "offer_reservations_member_select" ON "public"."offer_reservations"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_reservations" TO "service_role";

REVOKE ALL ON TABLE "public"."offer_reservations" FROM "authenticated";

GRANT SELECT ON TABLE "public"."offer_reservations" TO "authenticated";

REVOKE ALL ON TABLE "public"."offer_reservations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_reservations" TO "postgres";

REVOKE ALL ON TABLE "public"."offer_reservations" FROM "anon";
