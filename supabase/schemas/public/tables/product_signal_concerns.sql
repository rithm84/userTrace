CREATE TABLE "public"."product_signal_concerns" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "product_signal_id"    uuid                     NOT NULL,
  "concern_revision"     bigint                   NOT NULL,
  "pain_point"           text                     NOT NULL,
  "validation_criterion" text                     NOT NULL,
  "user_wording"         jsonb                    NOT NULL,
  "unknowns"             jsonb                    NOT NULL,
  "established_at"       timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  "created_at"           timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "product_signal_concerns_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "product_signal_concerns_pkey" PRIMARY KEY (id),
  CONSTRAINT "product_signal_concerns_product_signal_id_concern_revision_key" UNIQUE (product_signal_id, concern_revision),
  CONSTRAINT "product_signal_concerns_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."product_signal_concerns"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."product_signal_concerns"
  ADD COLUMN "origin" public.concern_origin NOT NULL;

CREATE INDEX product_signal_concerns_signal_idx ON public.product_signal_concerns USING btree (product_signal_id, concern_revision DESC);

CREATE TRIGGER product_signal_concerns_immutable
  BEFORE DELETE OR UPDATE ON public.product_signal_concerns
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE POLICY "product_signal_concerns_member_select" ON "public"."product_signal_concerns"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."product_signal_concerns" TO "service_role";

REVOKE ALL ON TABLE "public"."product_signal_concerns" FROM "authenticated";

GRANT SELECT ON TABLE "public"."product_signal_concerns" TO "authenticated";

REVOKE ALL ON TABLE "public"."product_signal_concerns" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."product_signal_concerns" TO "postgres";

REVOKE ALL ON TABLE "public"."product_signal_concerns" FROM "anon";
