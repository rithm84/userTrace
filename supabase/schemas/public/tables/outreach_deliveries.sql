CREATE TABLE "public"."outreach_deliveries" (
  "id"                          uuid                     NOT NULL,
  "client_id"                   uuid                     NOT NULL,
  "outreach_attempt_id"         uuid                     NOT NULL,
  "command_id"                  uuid                     NOT NULL,
  "provider"                    text                     NOT NULL,
  "recipient_provider_identity" text                     NOT NULL,
  "provider_message_ref"        text,
  "failure_category"            text,
  "detail"                      jsonb                    NOT NULL,
  "delivered_at"                timestamp with time zone,
  "schema_version"              smallint                 NOT NULL DEFAULT 1,
  "created_at"                  timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "outreach_deliveries_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "outreach_deliveries_command_id_fkey" FOREIGN KEY (command_id) REFERENCES public.commands(id),
  CONSTRAINT "outreach_deliveries_command_id_key" UNIQUE (command_id),
  CONSTRAINT "outreach_deliveries_outreach_attempt_id_fkey" FOREIGN KEY (outreach_attempt_id) REFERENCES public.outreach_attempts(id),
  CONSTRAINT "outreach_deliveries_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."outreach_deliveries"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."outreach_deliveries"
  ADD COLUMN "disposition" public.command_disposition NOT NULL;

ALTER TABLE "public"."outreach_deliveries"
  ADD COLUMN "effect_state" public.effect_state NOT NULL;

CREATE INDEX outreach_deliveries_attempt_idx ON public.outreach_deliveries USING btree (outreach_attempt_id, created_at DESC);

CREATE TRIGGER outreach_deliveries_immutable
  BEFORE DELETE OR UPDATE ON public.outreach_deliveries
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."outreach_deliveries" TO "service_role";

REVOKE ALL ON TABLE "public"."outreach_deliveries" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."outreach_deliveries" TO "postgres";

REVOKE ALL ON TABLE "public"."outreach_deliveries" FROM "anon", "authenticated";
