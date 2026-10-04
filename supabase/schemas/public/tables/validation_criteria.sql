CREATE TABLE "public"."validation_criteria" (
  "id"                uuid                     NOT NULL,
  "client_id"         uuid                     NOT NULL,
  "product_signal_id" uuid                     NOT NULL,
  "criteria_revision" bigint                   NOT NULL,
  "ordinal"           integer                  NOT NULL,
  "statement"         text                     NOT NULL,
  "schema_version"    smallint                 NOT NULL DEFAULT 1,
  "created_at"        timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "validation_criteria_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "validation_criteria_pkey" PRIMARY KEY (id),
  CONSTRAINT "validation_criteria_product_signal_id_criteria_revision_ord_key" UNIQUE (product_signal_id, criteria_revision, ordinal),
  CONSTRAINT "validation_criteria_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."validation_criteria"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."validation_criteria"
  ADD COLUMN "observability" public.criterion_observability NOT NULL;

CREATE TRIGGER validation_criteria_immutable
  BEFORE DELETE OR UPDATE ON public.validation_criteria
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE POLICY "validation_criteria_member_select" ON "public"."validation_criteria"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."validation_criteria" TO "service_role";

REVOKE ALL ON TABLE "public"."validation_criteria" FROM "authenticated";

GRANT SELECT ON TABLE "public"."validation_criteria" TO "authenticated";

REVOKE ALL ON TABLE "public"."validation_criteria" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."validation_criteria" TO "postgres";

REVOKE ALL ON TABLE "public"."validation_criteria" FROM "anon";
