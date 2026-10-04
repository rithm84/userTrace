CREATE TABLE "public"."outreach_attempts" (
  "id"                    uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "product_signal_id"     uuid                     NOT NULL,
  "client_user_id"        uuid                     NOT NULL,
  "revision"              bigint                   NOT NULL DEFAULT 0,
  "machine_version"       smallint                 NOT NULL DEFAULT 1,
  "outreach_offer_id"     uuid,
  "delivery_provider_ref" text,
  "scheduled_at"          timestamp with time zone NOT NULL DEFAULT now(),
  "dispatched_at"         timestamp with time zone,
  "responded_at"          timestamp with time zone,
  "expires_at"            timestamp with time zone NOT NULL,
  "reason"                text,
  "schema_version"        smallint                 NOT NULL DEFAULT 1,
  "created_at"            timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"            timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "outreach_attempts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "outreach_attempts_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id),
  CONSTRAINT "outreach_attempts_pkey" PRIMARY KEY (id),
  CONSTRAINT "outreach_attempts_offer_fk" FOREIGN KEY (outreach_offer_id) REFERENCES public.outreach_offers(id),
  CONSTRAINT "outreach_attempts_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."outreach_attempts"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."outreach_attempts"
  ADD COLUMN "state" public.outreach_attempt_state NOT NULL;

ALTER TABLE "public"."outreach_attempts"
  ADD COLUMN "purpose" public.outreach_purpose NOT NULL;

CREATE INDEX outreach_attempts_signal_idx ON public.outreach_attempts USING btree (product_signal_id, state);

CREATE POLICY "outreach_attempts_member_select" ON "public"."outreach_attempts"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."outreach_attempts" TO "service_role";

REVOKE ALL ON TABLE "public"."outreach_attempts" FROM "authenticated";

GRANT SELECT ON TABLE "public"."outreach_attempts" TO "authenticated";

REVOKE ALL ON TABLE "public"."outreach_attempts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."outreach_attempts" TO "postgres";

REVOKE ALL ON TABLE "public"."outreach_attempts" FROM "anon";
